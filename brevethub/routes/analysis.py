"""BrevetHub per-ride analysis — a rider's detailed breakdown of one of their own
Strava activities (M9).

A connected rider opens ``/analysis``, sees a list of their recent Strava rides,
and clicks "Analyze" on one. That POST fetches the activity's data STREAMS from
Strava once, runs the REUSED, club-agnostic ``shared.strava_analysis`` engine (the
``build_activity_analysis`` entrypoint — stop detection, inter-stop legs, the ride
summary, and the GPS map, all the same proven code Team Asha's engine provides),
caches the computed breakdown + the compressed raw streams in ``rp_ride_analysis``,
and redirects to the detail view. Every subsequent detail load
reads the cache — it makes ZERO Strava calls and NEVER recomputes — so the heavy,
rate-limited fetch + analysis stays off the request path (the mission's hard cost
constraint), exactly like the weather-cron / brevet-plan cache pattern.

Scope A (keyless): the breakdown is stop list + per inter-stop leg pace/HR/power +
summary metrics + a GPS map. It needs NO plan and NO OPENAI key — the AI coach
(Scope B) is a deferred follow-up. Segments here are inter-stop legs derived purely
from ``detect_stops()`` (no plan data), so none of Team Asha's plan-coupled
comparison math is used or reimplemented.

Security / ownership: the URL ``activity_id`` is UNTRUSTED. Before any stream fetch
or DB write, :func:`compute` proves the id belongs to the current rider's OWN Strava
athlete by requiring it to be a member of the rider-token's own activity list; a
non-owned / other-athlete id is rejected with a 404 and makes NO ``fetch_activity_
streams`` call and writes NO ``rp_ride_analysis`` row. Reads are scoped by session
``rider_id``, so a rider only ever sees their own analysis (guest -> login).

Isolation: imports only flask / stdlib / brevethub.* / shared.*, and every model call
is on an rp_* table, so test_brevethub_isolation.py and test_rp_only.py stay green.
"""
import time

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, session, url_for)

from brevethub import models
from brevethub.decorators import profile_required
from brevethub.routes.strava import _valid_access_token
from shared.strava import (CYCLING_TYPES, fetch_activities,
                           fetch_activity_streams)
from shared.strava_analysis import build_activity_analysis, _compress_streams

analysis_bp = Blueprint('analysis', __name__)

# The recent window (days) the activity picker + the ownership gate resolve over.
# Only a rider's own rides in this window are listable and analyzable — a bounded,
# honest definition of "recent" that keeps each owned-list fetch to a page or two.
ANALYSIS_WINDOW_DAYS = 90

# Unit conversions — the engine is imperial internally; BrevetHub converts to km /
# km/h / feet at this view boundary (US-imperial display is a Team-Asha-specific
# choice we deliberately drop).
_METERS_PER_KM = 1000.0
_MPH_TO_KMH = 1.609344
_M_PER_S_TO_KMH = 3.6
_M_TO_FT = 3.28084
_MILES_TO_KM = 1.609344


def _fmt_hm(minutes):
    """Format a minute count as ``'Hh MMm'`` (e.g. 95 -> '1h 35m'), or None.

    Pure builtin formatting so the templates need no ``commafy``/``clean_name``
    filter (BrevetHub registers neither — using one would 500 the render).
    """
    if minutes is None:
        return None
    total = int(round(minutes))
    h, m = divmod(max(total, 0), 60)
    return f"{h}h {m:02d}m"


def _owned_cycling_activities(token):
    """The current rider's OWN recent cycling activities as ``{activity_id: raw}``.

    The token is the rider's, so ``fetch_activities`` returns only that athlete's own
    activities — membership in this set is therefore a Strava-owned-athlete ownership
    proof, never inferred from the URL. Non-cycling activities are dropped (this is
    ride analysis). Raises on a Strava failure so the caller can degrade to a message.
    """
    after = int(time.time()) - ANALYSIS_WINDOW_DAYS * 24 * 3600
    raw = fetch_activities(
        token, api_base=current_app.config['STRAVA_API_BASE'], after_epoch=after
    )
    return {a['id']: a for a in raw if a.get('type') in CYCLING_TYPES}


def _summarize_for_list(activity, analyzed_ids=frozenset()):
    """A JSON-safe, de-branded, km/feet summary row for the activity picker.

    ``analyzed_ids`` is the set of the rider's already-analyzed activity ids, so the
    row can offer a "View analysis" link instead of a fresh compute when a cached
    breakdown already exists (cache-on-read, no needless recompute).
    """
    return {
        'id': activity['id'],
        'name': activity.get('name') or 'Untitled ride',
        'date': (activity.get('start_date_local') or '')[:10],
        'distance_km': round((activity.get('distance') or 0) / _METERS_PER_KM, 1),
        'elevation_ft': round((activity.get('total_elevation_gain') or 0) * _M_TO_FT),
        'moving_time': _fmt_hm((activity.get('moving_time') or 0) / 60),
        'analyzed': activity['id'] in analyzed_ids,
    }


def _leg_row(seg):
    """Convert one imperial engine segment into a de-branded km/km-h display leg.

    The heavy per-leg math (riding time from the interpolator, HR/cadence, average +
    normalized power, elevation gain, and gradient) is all computed by the REUSED
    ``build_activity_analysis`` engine; this only converts miles→km and mph→km/h at
    the view boundary and keeps the already-metric fields (bpm / W / rpm / % / ft).
    """
    speed_mph = seg.get('speed_mph')
    return {
        'to_km': round(seg['end_mi'] * _MILES_TO_KM, 1),
        'distance_km': round(seg['distance_mi'] * _MILES_TO_KM, 1),
        'riding_time': _fmt_hm(seg.get('riding_min')),
        'speed_kmh': round(speed_mph * _MPH_TO_KMH, 1) if speed_mph else None,
        'avg_hr': seg.get('avg_hr'),
        'avg_watts': seg.get('avg_watts'),
        'np_watts': seg.get('np_watts'),
        'avg_cadence': seg.get('avg_cadence'),
        'grade_pct': seg.get('grade_pct'),
        'climb_ft_per_mi': seg.get('climb_ft_per_mi'),
    }


def _build_analysis(activity, streams):
    """Assemble the JSON-safe, de-branded (km/km-h/feet) analysis payload.

    Delegates the whole computation to the REUSED shared entrypoint
    ``build_activity_analysis(streams, activity)`` — stop detection, the inter-stop
    leg partition with per-leg pace/HR/power/NP/climb/gradient, the ride summary, and
    the GPS map are all the proven engine's work. This function only converts the
    engine's imperial output to km/km-h/feet at the display boundary and drops the
    Team-Asha-specific imperial framing. Everything is precomputed here (on the
    explicit compute action) and stored, so the detail view is a pure cache read.
    """
    raw = build_activity_analysis(streams, activity)
    summary = raw['summary']

    stops = [{
        'distance_km': round((s.get('distance_miles') or 0) * _MILES_TO_KM, 1),
        'duration_min': s.get('duration_min'),
        'lat': s.get('lat'),
        'lng': s.get('lng'),
    } for s in raw['stops']]

    ride_map = None
    if raw['map'] and raw['map'].get('track'):
        ride_map = {'track': raw['map']['track'], 'bounds': raw['map'].get('bounds')}

    moving_mph = summary.get('avg_moving_speed_mph')
    return {
        'activity': {
            'name': activity.get('name') or 'Untitled ride',
            'date': (activity.get('start_date_local') or '')[:10],
            'distance_km': round((activity.get('distance') or 0) / _METERS_PER_KM, 1),
            'elevation_ft': round((activity.get('total_elevation_gain') or 0) * _M_TO_FT),
            'moving_time': _fmt_hm((activity.get('moving_time') or 0) / 60),
            'elapsed_time': _fmt_hm((activity.get('elapsed_time') or 0) / 60),
            'avg_speed_kmh': round((activity.get('average_speed') or 0) * _M_PER_S_TO_KMH, 1),
        },
        'summary': {
            'moving_speed_kmh': round(moving_mph * _MPH_TO_KMH, 1) if moving_mph else None,
            'avg_hr': summary.get('avg_hr'),
            'max_hr': summary.get('max_hr'),
            'avg_watts': summary.get('avg_watts'),
            'max_watts': summary.get('max_watts'),
        },
        'stop_count': len(stops),
        'stops': stops,
        'legs': [_leg_row(seg) for seg in raw['segments']],
        'map': ride_map,
    }


@analysis_bp.route('/analysis')
@profile_required
def analysis_list():
    """List the rider's recent OWN Strava rides, each with an Analyze control.

    Cache-aware / failure-tolerant like the dashboard Strava section: a Strava outage
    (or an unconnected rider) degrades to a neutral message, never a 500. Exposes no
    other rider's data — the list is the signed-in rider's own athlete only.
    """
    rider_id = session['rider_id']
    connection = models.get_strava_connection(rider_id)
    if not connection:
        return render_template('analysis_list.html', connected=False,
                               activities=[], error=None)

    activities = []
    error = None
    try:
        token = _valid_access_token(rider_id, connection)
        owned = _owned_cycling_activities(token)
        analyzed = models.get_analyzed_activity_ids(rider_id)
        activities = [_summarize_for_list(a, analyzed) for a in owned.values()]
        activities.sort(key=lambda a: a['date'], reverse=True)
    except Exception as e:  # noqa: BLE001 — degrade, never 500 the picker
        current_app.logger.warning(
            'analysis list: Strava fetch failed for rider %s: %s', rider_id, e)
        error = 'Could not load your Strava activities right now. Please try again later.'

    return render_template('analysis_list.html', connected=True,
                           activities=activities, error=error)


@analysis_bp.route('/analysis/<int:activity_id>/compute', methods=['POST'])
@profile_required
def compute(activity_id):
    """Fetch streams + run the engine for ONE of the rider's own activities, then cache.

    The ONLY path that hits Strava for streams and writes ``rp_ride_analysis``. The
    ownership gate runs FIRST: the untrusted URL ``activity_id`` must be a member of
    the rider-token's own recent cycling activities. A non-owned / other-athlete id is
    rejected with a 404 BEFORE any ``fetch_activity_streams`` call and BEFORE any upsert
    — so the ``(rider_id, activity_id)`` cache key can never hold a ride the rider does
    not own. On success the result is redirected to the cache-read detail view.
    """
    rider_id = session['rider_id']
    connection = models.get_strava_connection(rider_id)
    if not connection:
        flash('Connect Strava to analyze your rides.', 'error')
        return redirect(url_for('analysis.analysis_list'))

    try:
        token = _valid_access_token(rider_id, connection)
        owned = _owned_cycling_activities(token)
    except Exception as e:  # noqa: BLE001
        current_app.logger.warning(
            'analysis compute: owned-list fetch failed for rider %s: %s', rider_id, e)
        flash('Could not reach Strava right now. Please try again later.', 'error')
        return redirect(url_for('analysis.analysis_list'))

    # --- Ownership gate (untrusted id): fail closed with NO fetch and NO write. ---
    activity = owned.get(activity_id)
    if activity is None:
        current_app.logger.warning(
            'analysis compute: rider %s requested non-owned activity %s (rejected)',
            rider_id, activity_id)
        abort(404)

    try:
        streams = fetch_activity_streams(
            token, activity_id, api_base=current_app.config['STRAVA_API_BASE'])
    except Exception as e:  # noqa: BLE001
        current_app.logger.warning(
            'analysis compute: stream fetch failed for rider %s activity %s: %s',
            rider_id, activity_id, e)
        flash('Could not fetch this activity from Strava. Please try again later.', 'error')
        return redirect(url_for('analysis.analysis_list'))

    analysis = _build_analysis(activity, streams)
    models.upsert_ride_analysis(
        rider_id, activity_id, analysis,
        compressed_streams=_compress_streams(streams))
    return redirect(url_for('analysis.analysis_detail', activity_id=activity_id))


@analysis_bp.route('/analysis/<int:activity_id>')
@profile_required
def analysis_detail(activity_id):
    """Render the cached breakdown for one activity — a pure cache read, NEVER compute.

    Reads ``get_ride_analysis(rider_id, activity_id)``: scoped by session ``rider_id``,
    so a rider requesting another rider's activity id gets None (the not-analyzed state)
    and never sees the other rider's data. Makes zero Strava calls. When there is no
    cached row yet, shows the not-analyzed state with an Analyze control.
    """
    rider_id = session['rider_id']
    cached = models.get_ride_analysis(rider_id, activity_id)
    analysis = cached['analysis'] if cached and cached.get('analysis') else None
    return render_template('analysis_detail.html', analysis=analysis,
                           activity_id=activity_id, analyzed=analysis is not None)
