"""BrevetHub ride planning — the pacing schedule for an upcoming brevet.

A rider opens a brevet from the calendar, picks a target average speed (or a target
finish time), and sees a per-stop pacing schedule: cumulative distance, arrival
time, time bank vs the ACP control cutoff, and average speed. The math is the
REUSED, club-agnostic pacing engine (shared/pacing.py, extracted verbatim from Team
Asha's proven ``recalculate_cumulative_values``) — BrevetHub passes kilometres
straight through the engine's unit-agnostic ``distance_miles`` field and reads
``avg_speed`` back as km/h with zero conversion.

Guest surface (NO account required):
  GET  /plan/<event_id>[?speed=<km/h> | ?finish=<hours>]
                                     — compute + render the schedule for a target
                                       pace. Guests can compute freely; the view
                                       exposes no rider PII.

Rider surface (authenticated BrevetHub rider):
  POST /plan/<event_id>/save         — persist the rider's target + the
                                       server-recomputed schedule to rp_brevet_plan
                                       (JSON API; 401 for guests with a login_url).

Scope A (keyless): stops are derived evenly from the brevet distance (every 100 km
plus a final stop at the exact total) — no RWGPS credentials or real controls yet.
Scope B (RWGPS-backed real controls + elevation) can later swap in a richer stop
generator without touching the engine or rp_brevet_plan.

Isolation: imports only flask / stdlib / brevethub.* / shared.*, and every model
call is on an rp_* table, so test_brevethub_isolation.py and test_rp_only.py stay
green; the schedule is always recomputed server-side (never trusted from a client).
"""
from flask import (Blueprint, abort, jsonify, render_template, request,
                   url_for)

from brevethub import models
from brevethub.decorators import current_rider
from shared.pacing import recalculate_cumulative_values, _get_cutoff_hours

plan_bp = Blueprint('plan', __name__)

# Default target when the rider has not (yet) picked a pace — a conservative,
# finishable brevet speed for every ACP distance.
DEFAULT_SPEED_KMH = 20.0

# Stops are derived evenly from the brevet distance (Scope A, keyless): a control
# every STOP_STEP_KM plus a final stop at the exact total.
STOP_STEP_KM = 100


def _control_distances(total_km):
    """Evenly spaced cumulative stop distances for a brevet, ending at the total.

    e.g. total 200 -> [100, 200]; total 600 -> [100, 200, 300, 400, 500, 600].
    Always includes a final stop at the exact total, even when it is not a clean
    multiple of the step.
    """
    stops = []
    d = STOP_STEP_KM
    while d < total_km:
        stops.append(float(d))
        d += STOP_STEP_KM
    stops.append(float(total_km))
    return stops


def _cutoff_hours(event):
    """The brevet's control cutoff in hours: the cached ``time_limit_hours`` when
    present, else the ACP distance->time-limit mapping from the shared engine."""
    limit = event.get('time_limit_hours')
    if limit is not None:
        return float(limit)
    return _get_cutoff_hours(int(event['distance_km']))


def _resolve_target(source, total_km):
    """Resolve the target average speed (km/h) from the request inputs.

    ``source`` is a mapping (request.args or a JSON/form body). Precedence:
    an explicit ``speed`` (km/h) wins; else a ``finish`` time (hours) is converted
    to a speed; else the default. Returns ``(speed_kmh, mode)`` where mode is
    'speed' | 'finish' | 'default'. Invalid/non-positive inputs fall through.
    """
    speed_raw = str(source.get('speed') or '').strip()
    if speed_raw:
        try:
            speed = float(speed_raw)
            if speed > 0:
                return speed, 'speed'
        except (TypeError, ValueError):
            pass

    finish_raw = str(source.get('finish') or '').strip()
    if finish_raw:
        try:
            hours = float(finish_raw)
            if hours > 0:
                return total_km / hours, 'finish'
        except (TypeError, ValueError):
            pass

    return DEFAULT_SPEED_KMH, 'default'


def _compute_schedule(total_km, cutoff_hours, speed_kmh):
    """Per-stop pacing schedule from the REUSED shared engine.

    Builds a stop per control distance with a segment time implied by the target
    speed, then hands the list to ``recalculate_cumulative_values`` — which fills in
    seg_dist, avg_speed (km/h, since km go straight through the unit-agnostic
    ``distance_miles`` field), cumulative/arrival time, and the time bank vs the
    cutoff. Returns the engine's mutated stop dicts.
    """
    stops = []
    prev = 0.0
    for cum in _control_distances(total_km):
        seg = cum - prev
        seg_time = int(round(seg / speed_kmh * 60)) if speed_kmh > 0 else 0
        stops.append({
            'distance_miles': cum,        # km passed straight through (unit-agnostic engine)
            'elevation_gain': 0,          # no elevation in Scope A (flat/gray difficulty)
            'segment_time_min': seg_time,
            'stop_duration_min': 0,       # no rest stops modelled in Scope A
        })
        prev = cum
    return recalculate_cumulative_values(
        stops, {'name': ''}, cutoff_hours=cutoff_hours, total_mi=total_km)


def _fmt_hm(minutes):
    """Format a minute count as ``'Hh MMm'`` (e.g. 90 -> '1h 30m'), or None.

    Handles negatives (a blown time bank) with a leading minus so the template can
    print the value directly without any custom Jinja filter.
    """
    if minutes is None:
        return None
    total = int(round(minutes))
    sign = '-' if total < 0 else ''
    h, m = divmod(abs(total), 60)
    return f"{sign}{h}h {m:02d}m"


def _display_rows(raw):
    """Turn the engine's stop dicts into display rows for the template.

    Pre-formats the clock-ish fields in Python so ``plan.html`` needs only builtin
    Jinja (no commafy/clean_name — this app registers neither).
    """
    rows = []
    for s in raw:
        tb = s.get('time_bank_min')
        rows.append({
            'distance_km': int(round(s['distance_miles'])),
            'arrival': _fmt_hm(s.get('arrival_time_min')),
            'avg_speed': s.get('avg_speed'),
            'time_bank': _fmt_hm(tb),
            'time_bank_positive': (tb is not None and tb >= 0),
            'time_bank_known': tb is not None,
        })
    return rows


def _serialize_plan(raw, speed_kmh, cutoff_hours, total_km):
    """JSON-safe, server-computed plan payload persisted to rp_brevet_plan.plan_data.

    Stores raw minute values (not formatted strings) so a later view can re-render
    or recompute without re-deriving them.
    """
    return {
        'target_speed_kmh': round(speed_kmh, 2),
        'cutoff_hours': cutoff_hours,
        'total_km': total_km,
        'stops': [{
            'distance_km': int(round(s['distance_miles'])),
            'arrival_time_min': s.get('arrival_time_min'),
            'time_bank_min': s.get('time_bank_min'),
            'avg_speed': s.get('avg_speed'),
        } for s in raw],
    }


@plan_bp.route('/plan/<int:event_id>')
def plan_view(event_id):
    """Compute + render the pacing schedule for a brevet at a target pace.

    Guest-readable: anyone can compute a schedule; no rider PII is rendered. A
    signed-in rider additionally sees their previously-saved target (if any) and the
    Save control. Unknown event -> 404.
    """
    event = models.get_brevet_event_full(event_id)
    if not event:
        abort(404)

    total_km = float(event['distance_km'])
    cutoff_hours = _cutoff_hours(event)
    speed_kmh, mode = _resolve_target(request.args, total_km)

    raw = _compute_schedule(total_km, cutoff_hours, speed_kmh)
    schedule = _display_rows(raw)

    rider = current_rider()
    saved = models.get_rider_brevet_plan(rider['id'], event_id) if rider else None

    finish_hours = round(total_km / speed_kmh, 2) if speed_kmh > 0 else None
    return render_template(
        'plan.html',
        event=event,
        schedule=schedule,
        cutoff_hours=cutoff_hours,
        speed_kmh=round(speed_kmh, 1),
        finish_hours=finish_hours,
        finish_row=schedule[-1] if schedule else None,
        mode=mode,
        rider=rider,
        saved=saved,
    )


@plan_bp.route('/plan/<int:event_id>/save', methods=['POST'])
def save_plan(event_id):
    """Persist the signed-in rider's target + the SERVER-recomputed schedule.

    Auth ladder mirrors the calendar sign-up: no session rider -> 401 (with a
    login_url the client can send them to); unknown event -> 404. The schedule is
    always recomputed here from the reused engine — a client-posted schedule is
    never trusted.
    """
    rider = current_rider()
    if not rider:
        return jsonify({
            'error': 'Sign in to save your plan.',
            'login_url': url_for('auth.login',
                                 next=url_for('plan.plan_view', event_id=event_id)),
        }), 401

    event = models.get_brevet_event_full(event_id)
    if not event:
        return jsonify({'error': 'Event not found'}), 404

    total_km = float(event['distance_km'])
    cutoff_hours = _cutoff_hours(event)
    payload = request.get_json(silent=True) or request.form
    speed_kmh, mode = _resolve_target(payload, total_km)

    raw = _compute_schedule(total_km, cutoff_hours, speed_kmh)
    plan_data = _serialize_plan(raw, speed_kmh, cutoff_hours, total_km)
    target_finish_min = int(round(total_km / speed_kmh * 60)) if speed_kmh > 0 else None

    models.upsert_rider_brevet_plan(
        rider['id'], event_id,
        target_speed_kmh=round(speed_kmh, 2),
        target_finish_min=target_finish_min,
        plan_data=plan_data,
    )
    return jsonify({
        'ok': True,
        'event_id': event_id,
        'target_speed_kmh': round(speed_kmh, 2),
    }), 200
