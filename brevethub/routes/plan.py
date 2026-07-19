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
from flask import (Blueprint, abort, current_app, jsonify, render_template,
                   request, url_for)

from brevethub import models
from brevethub.decorators import current_rider
from shared.pacing import recalculate_cumulative_values, _get_cutoff_hours
from shared.weather import compute_stop_winds

plan_bp = Blueprint('plan', __name__)

# Default target when the rider has not (yet) picked a pace — a conservative,
# finishable brevet speed for every ACP distance.
DEFAULT_SPEED_KMH = 20.0

# Stops are derived evenly from the brevet distance (Scope A, keyless): a control
# every STOP_STEP_KM plus a final stop at the exact total.
STOP_STEP_KM = 100

# ── Unit-conversion boundary (real RWGPS plans) ────────────────────────────
# The reused shared/rwgps.py engine emits NATIVE miles / mph (and feet); it stores
# them verbatim in rp_brevet_route_plan[_stop]. BrevetHub's /plan UI is km / km-h,
# so THIS route is the single conversion boundary: every mile value becomes km and
# every mph value becomes km-h HERE, before display rows or SVG geometry are built.
# Elevation stays feet (BH brevet convention). Convert exactly once — relabeling
# without converting would show ~0.62× the real distance and mph-as-km-h.
KM_PER_MILE = 1.609344


def _mi_to_km(miles):
    """Miles → km (or None). The engine's stored distance unit → BH's display unit.

    Coerces to float first: the NUMERIC columns come back from psycopg2 as
    ``Decimal``, and ``Decimal * float`` raises ``TypeError`` — so cast before the
    multiply (also accepts int / numeric str)."""
    return round(float(miles) * KM_PER_MILE, 1) if miles is not None else None


def _mph_to_kmh(mph):
    """mph → km-h (or None). The engine's stored speed unit → BH's display unit.

    Coerces to float first (NUMERIC → Decimal, and ``Decimal * float`` raises)."""
    return round(float(mph) * KM_PER_MILE, 1) if mph is not None else None


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


# ── Real RWGPS plan rendering (km / km-h) ──────────────────────────────────
# Difficulty coloring bands (unit-agnostic 0-10 score → a green→red ramp). Mirrors
# the intent of Team Asha's per-segment difficulty coloring; the score itself is
# what the reused engine's _compute_difficulty_score produced.
_DIFFICULTY_BANDS = [
    (2.0, '#22c55e'),   # easy — green
    (4.0, '#84cc16'),   # rolling — lime
    (6.0, '#eab308'),   # moderate — amber
    (8.0, '#f97316'),   # hard — orange
    (10.1, '#ef4444'),  # very hard — red
]


def _difficulty_color(score):
    """Map a 0-10 difficulty score to a green→red hex color for a stop marker/row."""
    s = score or 0
    for threshold, color in _DIFFICULTY_BANDS:
        if s < threshold:
            return color
    return _DIFFICULTY_BANDS[-1][1]


def _build_elevation_svg(stops_km, *, width=940, height=200):
    """Build SVG geometry for the cumulative-climb elevation profile (feet vs km).

    Consumes the ALREADY-converted km display stops (so the x-axis is km, matching
    the tables) and the stored per-stop elevation gain (feet). Returns a dict of
    pre-computed pixel paths / gridlines / markers so plan.html stays builtin-Jinja
    only (no namespace math in the template). Modelled on TA's journey_svg macro.
    """
    padl, padr, padt, padb = 40, 16, 18, 26
    inner_w = width - padl - padr
    inner_h = height - padt - padb

    total_km = stops_km[-1]['distance_km'] if stops_km else 0
    span = total_km or 1

    # Cumulative climb series (feet) at each stop's cumulative km position.
    pts = []
    cum_ft = 0
    for s in stops_km:
        cum_ft += (s['elevation_gain'] or 0)
        pts.append({'km': s['distance_km'] or 0, 'ft': cum_ft})
    max_ft = max((p['ft'] for p in pts), default=0) or 1

    def _px(km):
        return round(padl + (km / span) * inner_w, 2)

    def _py(ft):
        return round(padt + inner_h - (ft / max_ft) * inner_h, 2)

    # Line + filled area under it.
    line_cmds = []
    for i, p in enumerate(pts):
        line_cmds.append(f"{'M' if i == 0 else 'L'} {_px(p['km'])} {_py(p['ft'])}")
    line_path = ' '.join(line_cmds)
    baseline_y = round(padt + inner_h, 2)
    area_path = (
        f"M {_px(0)} {baseline_y} " + ' '.join('L' + c[1:] for c in line_cmds)
        + f" L {_px(span)} {baseline_y} Z"
    ) if pts else ''

    # km gridlines every 50 km.
    gridlines = []
    step = 50
    km = 0
    while km <= total_km + 0.001:
        gridlines.append({'x': _px(km), 'label': int(km),
                          'minor': (km % 100 != 0)})
        km += step

    # Elevation axis labels (0, half, max).
    elev_labels = [
        {'y': _py(0), 'label': 0},
        {'y': _py(max_ft / 2), 'label': int(round(max_ft / 2))},
        {'y': _py(max_ft), 'label': int(round(max_ft))},
    ]

    # Per-stop markers, colored by difficulty.
    markers = []
    for i, s in enumerate(stops_km):
        st = s['stop_type']
        markers.append({
            'x': _px(s['distance_km'] or 0),
            'y': _py(pts[i]['ft']),
            'r': 7 if st in ('start', 'finish') else (5.5 if st == 'control' else 4),
            'color': s['difficulty_color'],
            'location': s['location'],
            'distance_km': s['distance_km'],
            'elevation_gain': s['elevation_gain'],
            'difficulty_score': s['difficulty_score'],
        })

    return {
        'width': width, 'height': height,
        'line_path': line_path, 'area_path': area_path,
        'gridlines': gridlines, 'elev_labels': elev_labels, 'markers': markers,
        'total_km': round(total_km, 1), 'max_ft': int(round(max_ft)),
    }


def _build_real_plan(plan, stops, stop_winds=None):
    """Turn a persisted real plan (native miles/mph/feet) into a km / km-h display
    context for plan.html: converted per-stop rows, the SVG elevation profile, and
    plan-level summary values. THE conversion happens here and nowhere else.

    ``stop_winds`` (when present) is the per-stop forecast wind list from
    ``compute_stop_winds`` — SAME length + order as ``stops``, with ``None`` for a
    stop that has no forecast. Each stop's dict gets a ``wind`` field (its wind dict
    or None); ``has_wind`` flags whether the Wind column should render at all.
    """
    display_stops = []
    for i, s in enumerate(stops):
        wind = stop_winds[i] if stop_winds and i < len(stop_winds) else None
        display_stops.append({
            'stop_order': s['stop_order'],
            'location': s['location'],
            'stop_type': s['stop_type'],
            'distance_km': _mi_to_km(s['distance_miles']),   # miles → km
            'seg_dist_km': _mi_to_km(s['seg_dist']),         # miles → km
            'elevation_gain': s['elevation_gain'],           # feet (unchanged)
            'ft_per_mi': s['ft_per_mi'],                     # labeled ft/mi in UI
            'avg_speed_kmh': _mph_to_kmh(s['avg_speed']),    # mph → km-h
            'difficulty_score': s['difficulty_score'],
            'difficulty_color': _difficulty_color(s['difficulty_score']),
            'arrival': _fmt_hm(s['cum_time_min']),
            'time_bank': _fmt_hm(s['time_bank_min']),
            'time_bank_positive': (s['time_bank_min'] is not None
                                   and s['time_bank_min'] >= 0),
            'time_bank_known': s['time_bank_min'] is not None,
            'wind': wind,
        })

    final_km = display_stops[-1]['distance_km'] if display_stops else None
    return {
        'name': plan['name'],
        'rwgps_url': plan['rwgps_url'],
        'distance_km': _mi_to_km(plan['total_distance_miles']),
        'total_elevation_ft': plan['total_elevation_ft'],
        'overall_ft_per_mile': plan['overall_ft_per_mile'],
        'avg_moving_speed_kmh': _mph_to_kmh(plan['avg_moving_speed']),
        'final_distance_km': final_km,
        'stops': display_stops,
        'has_wind': any(ds['wind'] for ds in display_stops),
        'svg': _build_elevation_svg(display_stops),
    }


def _forecast_stop_winds(event, plan, stops):
    """Per-stop forecast wind for a real plan, from the warm route-weather cache.

    Reads the pre-fetched rp_brevet_route_weather row for (event, forecast_date) and
    hands its stored forecast + sample points to the SHARED ``compute_stop_winds`` —
    the SAME pure per-stop math Team Asha uses — so the guest page NEVER calls
    Open-Meteo/RWGPS live (it only reads the cron-warmed cache). Returns a list the
    same length as ``stops`` (None entries for unresolved stops), or None when no
    forecast is cached (graceful miss: the plan renders with no Wind column). Fails
    SOFT: any error yields None so the plan page never 500s on the wind path.
    """
    try:
        forecast_date = event.get('date')
        if not forecast_date:
            return None
        row = models.get_brevet_route_weather(event['id'], forecast_date)
        if not row:
            return None
        weather_data = row.get('weather_data')
        sample_points = row.get('sample_points')
        if not weather_data or not sample_points:
            return None
        start_time_str = plan.get('start_time') or '07:00'
        return compute_stop_winds(stops, weather_data, sample_points,
                                  forecast_date, start_time_str)
    except Exception as e:  # pragma: no cover - defensive; keep the page up
        current_app.logger.warning('Forecast wind injection failed for event %s: %s',
                                    event.get('id'), e)
        return None


def _load_real_plan(event_id, event=None):
    """Fetch the persisted real plan for an event, or None. Fails SOFT: any DB error
    (or no real plan) yields None so /plan falls back to the synthetic schedule and
    never 500s on the read path.

    When ``event`` is given, per-stop forecast wind is injected from the warm
    route-weather cache (fail-soft — no wind on a miss)."""
    try:
        bundle = models.get_brevet_route_plan_with_stops(event_id)
    except Exception as e:  # pragma: no cover - defensive; keep the page up
        current_app.logger.warning('Real plan lookup failed for event %s: %s',
                                    event_id, e)
        return None
    if not bundle or not bundle.get('stops'):
        return None
    stop_winds = _forecast_stop_winds(event, bundle['plan'], bundle['stops']) if event else None
    return _build_real_plan(bundle['plan'], bundle['stops'], stop_winds)


@plan_bp.route('/plan/<int:event_id>')
def plan_view(event_id):
    """Compute + render the pacing schedule for a brevet at a target pace.

    If a real, RWGPS-backed plan has been persisted for this brevet, render THAT
    (real control names, SVG elevation profile, per-segment difficulty coloring and
    gradient speed, all in km / km-h). Otherwise fall back to the synthetic
    evenly-spaced Scope-A schedule at a target pace.

    Guest-readable either way: anyone can view; no rider PII is rendered. A signed-in
    rider additionally sees their previously-saved target (synthetic mode) and the
    Save control. Unknown event -> 404.
    """
    event = models.get_brevet_event_full(event_id)
    if not event:
        abort(404)

    rider = current_rider()
    real_plan = _load_real_plan(event_id, event)

    total_km = float(event['distance_km'])
    cutoff_hours = _cutoff_hours(event)

    if real_plan:
        # Real plan mode: the persisted plan is the schedule; no target picker.
        return render_template(
            'plan.html',
            event=event,
            real_plan=real_plan,
            schedule=None,
            cutoff_hours=cutoff_hours,
            rider=rider,
            saved=None,
        )

    speed_kmh, mode = _resolve_target(request.args, total_km)
    raw = _compute_schedule(total_km, cutoff_hours, speed_kmh)
    schedule = _display_rows(raw)

    saved = models.get_rider_brevet_plan(rider['id'], event_id) if rider else None

    finish_hours = round(total_km / speed_kmh, 2) if speed_kmh > 0 else None
    return render_template(
        'plan.html',
        event=event,
        real_plan=None,
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
