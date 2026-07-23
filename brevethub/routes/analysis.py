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
from datetime import datetime, timedelta

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, session, url_for)

from brevethub import models
from brevethub.decorators import profile_required
from brevethub.routes.strava import _valid_access_token
from shared.strava import (CYCLING_TYPES, fetch_activities,
                           fetch_activity_streams)
from shared.strava_analysis import (
    METERS_PER_MILE,
    build_activity_analysis,
    build_map_data,
    _build_stream_interpolator,
    _compress_streams,
    _decompress_streams,
)
from shared.weather import (_AVG_SPEED_KMH, _safe_get, calculate_bearing,
                            classify_wind, compass_label, crosswind_component,
                            fetch_historical_wind, get_hour_index,
                            headwind_component, wind_arrow_glyph,
                            wind_arrow_rotation, wind_cell_style)

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


def _event_date(value):
    if not value:
        return ''
    if hasattr(value, 'date'):
        return value.date().isoformat()
    return str(value)[:10]


def _brevet_history_key(brevet):
    event_date = _event_date(brevet.get('date'))
    try:
        distance_km = round(float(brevet.get('distance_km') or 0))
    except (TypeError, ValueError):
        distance_km = 0
    return (event_date, distance_km)


def _normalize_rusa_cache_brevet(brevet):
    """Convert one cached RUSA history row to the analysis brevet-match shape."""
    return {
        'event_id': brevet.get('event_id'),
        'status': models.RideStatus.FINISHED.value,
        'name': (brevet.get('name') or brevet.get('route_name') or
                 brevet.get('route') or brevet.get('permanent_name') or 'RUSA brevet'),
        'date': _event_date(brevet.get('date')),
        'distance_km': brevet.get('distance_km'),
        'finish_time': brevet.get('finish_time'),
        'region': brevet.get('region'),
        'source': 'rusa_cache',
    }


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


def _fmt_clock(start_time, elapsed_min):
    if elapsed_min is None:
        return None
    try:
        parts = str(start_time or '06:00').split(':')
        start_total = int(parts[0]) * 60 + int(parts[1])
        total = (start_total + int(round(elapsed_min))) % (24 * 60)
    except (TypeError, ValueError, IndexError):
        return None
    hour, minute = divmod(total, 60)
    suffix = 'am' if hour < 12 else 'pm'
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d}{suffix}"


def _fmt_signed_hm(minutes):
    if minutes is None:
        return None
    total = int(round(minutes))
    sign = '-' if total < 0 else ''
    h, m = divmod(abs(total), 60)
    return f"{sign}{h}:{m:02d}"


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


def _rider_finished_brevets(rider_id):
    """Finished brevet history used to classify Strava activities.

    BrevetHub event-signup results carry ``event_id`` and can unlock route-plan
    comparison, so they win on duplicates. The rider's cached RUSA history is still
    authoritative for official completed brevets, and many riders have RUSA cache
    rows without matching local event signups; those must still render as brevets.
    """
    finished = {}
    try:
        for brevet in models.get_rider_past_results(rider_id):
            if brevet.get('status') != models.RideStatus.FINISHED.value:
                continue
            finished[_brevet_history_key(brevet)] = brevet
    except Exception as e:  # noqa: BLE001 - brevet matching is additive
        current_app.logger.warning(
            'analysis event-result brevet match load failed for rider %s: %s',
            rider_id, e)

    try:
        cache_row = models.get_rider_rusa_cache(rider_id) or {}
        for brevet in cache_row.get('rusa_cache') or []:
            normalized = _normalize_rusa_cache_brevet(brevet)
            key = _brevet_history_key(normalized)
            if key[0] and key[1]:
                finished.setdefault(key, normalized)
    except Exception as e:  # noqa: BLE001 - brevet matching is additive
        current_app.logger.warning(
            'analysis RUSA-cache brevet match load failed for rider %s: %s',
            rider_id, e)

    return list(finished.values())


def _match_activity_to_brevet(activity, brevet_events):
    activity_date = (activity.get('start_date_local') or '')[:10]
    activity_km = (activity.get('distance') or 0) / _METERS_PER_KM
    best = None
    best_delta = None
    for event in brevet_events or []:
        event_date = _event_date(event.get('date'))
        event_km = float(event.get('distance_km') or 0)
        if not event_date or event_date != activity_date or not event_km:
            continue
        delta = abs(activity_km - event_km)
        tolerance = max(8.0, event_km * 0.10)
        if delta <= tolerance and (best_delta is None or delta < best_delta):
            best = event
            best_delta = delta
    if not best:
        return None
    return {
        'event_id': best.get('event_id'),
        'name': best.get('name'),
        'date': _event_date(best.get('date')),
        'distance_km': best.get('distance_km'),
        'finish_time': best.get('finish_time'),
        'region': best.get('region'),
    }


def _summarize_for_list(activity, analyzed_ids=frozenset(), brevet=None):
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
        'strava_url': f"https://www.strava.com/activities/{activity['id']}",
        'is_brevet': brevet is not None,
        'brevet': brevet,
        'analyzed': activity['id'] in analyzed_ids,
    }


def _stream_avg(streams, key, start_mi, end_mi):
    values = streams.get(key) or []
    dist_m = streams.get('distance') or []
    if not values or not dist_m or len(values) != len(dist_m):
        return None
    vals = [
        values[i] for i, meters in enumerate(dist_m)
        if start_mi <= meters / METERS_PER_MILE <= end_mi
        and values[i] is not None and values[i] > 0
    ]
    return round(sum(vals) / len(vals)) if vals else None


def _stream_elevation_gain_ft(streams, start_mi, end_mi):
    altitude = streams.get('altitude') or []
    dist_m = streams.get('distance') or []
    if not altitude or not dist_m or len(altitude) != len(dist_m):
        return None
    idx = [i for i, meters in enumerate(dist_m)
           if start_mi <= meters / METERS_PER_MILE <= end_mi]
    if len(idx) < 2:
        return None
    gain_m = 0.0
    for a, b in zip(idx, idx[1:]):
        delta = altitude[b] - altitude[a]
        if delta and delta > 0:
            gain_m += delta
    return round(gain_m * _M_TO_FT)


def _match_stops_to_plan(detected_stops, plan_stops):
    stops = [dict(s) for s in detected_stops or []]
    if not plan_stops:
        for stop in stops:
            stop['matched_stop_name'] = None
            stop['matched_stop_type'] = None
            stop['is_extra'] = True
        return stops

    total_mi = max((float(s.get('distance_miles') or 0) for s in plan_stops), default=0)
    tolerance = min(total_mi * 0.03, 3.0) if total_mi else 3.0
    for stop in stops:
        stop['_matched'] = False
        stop['matched_stop_name'] = None
        stop['matched_stop_type'] = None
        stop['is_extra'] = True

    for ps in plan_stops:
        stop_type = (ps.get('stop_type') or '').lower()
        if stop_type == 'start':
            continue
        ps_dist = float(ps.get('distance_miles') or 0)
        best = None
        best_delta = None
        for stop in stops:
            if stop.get('_matched'):
                continue
            delta = abs((stop.get('distance_miles') or 0) - ps_dist)
            if delta <= tolerance and (best_delta is None or delta < best_delta):
                best = stop
                best_delta = delta
        if best:
            best['_matched'] = True
            best['matched_stop_name'] = ps.get('location')
            best['matched_stop_type'] = stop_type
            best['is_extra'] = False

    for stop in stops:
        stop.pop('_matched', None)
    return stops


def _build_plan_comparison(plan, plan_stops, raw, activity, streams):
    detected = _match_stops_to_plan(raw.get('stops') or [], plan_stops)
    interp = _build_stream_interpolator(streams)
    rows = []
    matched_by_name = {
        s.get('matched_stop_name'): s for s in detected if s.get('matched_stop_name')
    }
    actual_elapsed_min = (activity.get('elapsed_time') or 0) / 60
    actual_moving_min = (activity.get('moving_time') or 0) / 60
    actual_distance_mi = (activity.get('distance') or 0) / METERS_PER_MILE
    actual_elevation_ft = round((activity.get('total_elevation_gain') or 0) * _M_TO_FT)
    start_time = (plan or {}).get('start_time') or '06:00'
    prev_dist = 0.0
    prev_actual_cum = 0

    for idx, stop in enumerate(plan_stops or []):
        stop_type = (stop.get('stop_type') or '').lower()
        distance_mi = float(stop.get('distance_miles') or 0)
        seg_dist = float(stop.get('seg_dist') or max(distance_mi - prev_dist, 0))
        plan_seg = stop.get('segment_time_min') or 0
        plan_stop = stop.get('stop_duration_min') or 0
        plan_cum = stop.get('cum_time_min') or 0
        plan_arrival = stop.get('arrival_time_min')
        if plan_arrival is None and plan_cum is not None:
            plan_arrival = max((plan_cum or 0) - (plan_stop or 0), 0)
        plan_time_bank = stop.get('time_bank_min')
        actual_stop = matched_by_name.get(stop.get('location'))
        actual_stop_min = actual_stop.get('duration_min') if actual_stop else 0
        if stop_type == 'start':
            actual_cum = 0
            actual_stop_min = 0
        elif stop_type == 'finish':
            actual_cum = round(actual_elapsed_min)
            actual_stop_min = 0
        elif interp:
            actual_cum = round(interp(distance_mi))
        else:
            actual_cum = None

        actual_segment_min = None
        actual_speed_mph = None
        actual_arrival = None
        if actual_cum is not None and idx > 0:
            actual_arrival = max(0, actual_cum - (actual_stop_min or 0))
            actual_segment_min = max(0, round(actual_cum - prev_actual_cum - (actual_stop_min or 0)))
            if actual_segment_min and seg_dist:
                actual_speed_mph = round(seg_dist / (actual_segment_min / 60), 1)
        elif actual_cum is not None:
            actual_arrival = actual_cum

        actual_time_bank = None
        if plan_time_bank is not None and plan_arrival is not None and actual_arrival is not None:
            actual_time_bank = round(plan_time_bank + (plan_arrival - actual_arrival))

        actual_elev_gain_ft = _stream_elevation_gain_ft(streams, prev_dist, distance_mi)

        row = {
            'location': stop.get('location') or '',
            'stop_type': stop_type or 'waypoint',
            'distance_miles': distance_mi,
            'distance_km': round(distance_mi * _MILES_TO_KM, 1),
            'plan_segment_min': plan_seg,
            'plan_stop_duration_min': plan_stop,
            'plan_cum_time_min': plan_cum,
            'plan_arrival_time_min': plan_arrival,
            'plan_time_of_day': _fmt_clock(start_time, plan_cum),
            'plan_time_bank': plan_time_bank,
            'plan_time_bank_fmt': _fmt_signed_hm(plan_time_bank),
            'plan_speed_mph': round(seg_dist / (plan_seg / 60), 1) if plan_seg and seg_dist else None,
            'actual_stop_duration_min': actual_stop_min,
            'actual_cum_time_min': actual_cum,
            'actual_arrival_time_min': actual_arrival,
            'actual_time_of_day': _fmt_clock(start_time, actual_cum),
            'actual_time_bank': actual_time_bank,
            'actual_time_bank_fmt': _fmt_signed_hm(actual_time_bank),
            'actual_segment_min': actual_segment_min,
            'actual_speed_mph': actual_speed_mph,
            'actual_speed_kmh': round(actual_speed_mph * _MPH_TO_KMH, 1) if actual_speed_mph else None,
            'actual_avg_hr': _stream_avg(streams, 'heartrate', prev_dist, distance_mi),
            'actual_avg_watts': _stream_avg(streams, 'watts', prev_dist, distance_mi),
            'actual_avg_cadence': _stream_avg(streams, 'cadence', prev_dist, distance_mi),
            'actual_elev_gain_ft': actual_elev_gain_ft,
            'actual_climb_ft_per_mi': round(actual_elev_gain_ft / seg_dist) if actual_elev_gain_ft is not None and seg_dist else None,
            'cum_time_delta_min': round(actual_cum - plan_cum) if actual_cum is not None and plan_cum else None,
            'is_extra': False,
        }
        rows.append(row)
        prev_dist = distance_mi
        if actual_cum is not None:
            prev_actual_cum = actual_cum

    for stop in detected:
        if not stop.get('is_extra'):
            continue
        rows.append({
            'location': f"Unplanned stop @ {round(stop.get('distance_miles') or 0, 1)} mi",
            'stop_type': 'extra',
            'distance_miles': stop.get('distance_miles') or 0,
            'distance_km': round((stop.get('distance_miles') or 0) * _MILES_TO_KM, 1),
            'actual_stop_duration_min': stop.get('duration_min'),
            'is_extra': True,
        })
    rows.sort(key=lambda r: r.get('distance_miles') or 0)

    plan_distance_mi = float((plan or {}).get('total_distance_miles') or 0)
    plan_total_min = (plan or {}).get('total_elapsed_time_min') or (
        rows[-1].get('plan_cum_time_min') if rows else None
    )
    plan_break_min = (plan or {}).get('total_break_time_min')
    return {
        'summary': {
            'plan_name': (plan or {}).get('name'),
            'plan_distance_km': round(plan_distance_mi * _MILES_TO_KM, 1) if plan_distance_mi else None,
            'actual_distance_km': round(actual_distance_mi * _MILES_TO_KM, 1),
            'distance_delta_km': round((actual_distance_mi - plan_distance_mi) * _MILES_TO_KM, 1) if plan_distance_mi else None,
            'plan_elevation_ft': (plan or {}).get('total_elevation_ft'),
            'actual_elevation_ft': actual_elevation_ft,
            'plan_total_time_min': plan_total_min,
            'actual_elapsed_time_min': round(actual_elapsed_min),
            'actual_moving_time_min': round(actual_moving_min),
            'plan_break_time_min': plan_break_min,
            'actual_stopped_time_min': round(actual_elapsed_min - actual_moving_min),
            'stops_planned': len([s for s in plan_stops or [] if (s.get('stop_type') or '').lower() not in ('start', 'finish')]),
            'stops_detected': len(detected),
            'stops_extra': len([s for s in detected if s.get('is_extra')]),
        },
        'rows': rows,
        'hr_power': any(r.get('actual_avg_hr') or r.get('actual_avg_watts') for r in rows),
        'detected_stops': detected,
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


def _build_analysis(activity, streams, brevet=None):
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
    plan_bundle = None
    comparison = None
    if brevet and brevet.get('event_id'):
        plan_bundle = models.get_brevet_route_plan_with_stops(brevet['event_id'])
        if plan_bundle:
            comparison = _build_plan_comparison(
                plan_bundle['plan'], plan_bundle['stops'], raw, activity, streams)

    stops = [{
        'distance_km': round((s.get('distance_miles') or 0) * _MILES_TO_KM, 1),
        'duration_min': s.get('duration_min'),
        'lat': s.get('lat'),
        'lng': s.get('lng'),
    } for s in raw['stops']]

    ride_map = None
    if comparison:
        ride_map = build_map_data(streams, comparison, comparison.get('detected_stops'))
    elif raw['map'] and raw['map'].get('track'):
        ride_map = {'track': raw['map']['track'], 'bounds': raw['map'].get('bounds'),
                    'stops': stops, 'segments': []}

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
            'strava_url': f"https://www.strava.com/activities/{activity.get('id')}",
        },
        'brevet': brevet,
        'plan': plan_bundle['plan'] if plan_bundle else None,
        'comparison': comparison,
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


# Ride start estimate for historical arrival-hour selection — a completed ride's
# per-stop times aren't stored, so wind is sampled at a flat-speed arrival from a
# conventional 07:00 grand départ (same heuristic Team Asha's historical path uses).
_HIST_START_HOUR = 7


def _historical_stop_winds(analysis):
    """Per-stop HISTORICAL wind for a completed ride, over the actual ride date.

    A BrevetHub assembler (NOT an extraction of Team Asha's get_historical_stop_wind —
    the two paths differ: km/h units, list-index lookup, no gust window, no persist)
    that composes the SHARED weather primitives — ``fetch_historical_wind`` for the
    archive/forecast fetch, then bearing → head/cross → arrow — so no wind math is
    duplicated. The analysis stops already carry lat/lng (from the GPS map), so it
    fetches per-stop wind directly rather than interpolating a track.

    Index discipline (the redteam-flagged trap): it fetches weather ONLY for stops
    that have coordinates, but returns a ``stop_winds`` list that is EXACTLY the same
    length and order as ``analysis['stops']`` — ``None`` at every coordinate-less
    stop — using an original-index → fetched-index ``valid_map``. The template renders
    ``stop_winds[loop.index0]`` against the original stops, so a coordinate-less stop
    can never shift a fetched forecast onto the wrong displayed stop.

    Returns:
      - ``[]`` for empty stops (no fetch),
      - ``[None, ...]`` (all None, no fetch) when NO stop has coordinates,
      - ``None`` on a fetch error or unparseable ride date (fail-soft: the page still
        renders, just without a Wind column),
      - otherwise a same-length list of per-stop wind dicts / ``None`` placeholders.
    """
    stops = (analysis or {}).get('stops') or []
    if not stops:
        return []

    # Original-order coordinates; None where a stop lacks lat/lng.
    coords = [
        {'lat': s['lat'], 'lng': s['lng']}
        if s.get('lat') is not None and s.get('lng') is not None else None
        for s in stops
    ]

    # original stop index -> index within the coordinate-only fetch list.
    valid_map = {}
    valid_coords = []
    for orig_idx, c in enumerate(coords):
        if c is not None:
            valid_map[orig_idx] = len(valid_coords)
            valid_coords.append(c)

    if not valid_coords:
        # No geometry at all — same-length all-None, no fetch.
        return [None] * len(stops)

    ride_date_str = (analysis.get('activity') or {}).get('date') or ''
    try:
        ride_date = datetime.strptime(ride_date_str[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None

    try:
        weather_data, data_source = fetch_historical_wind(valid_coords, ride_date)
    except Exception as e:  # noqa: BLE001 — fail soft, render without wind
        current_app.logger.warning('Historical wind fetch failed: %s', e)
        return None
    if not weather_data:
        return None

    start_dt = datetime(ride_date.year, ride_date.month, ride_date.day,
                        _HIST_START_HOUR, 0)

    stop_winds = []
    for i, coord in enumerate(coords):
        if coord is None:
            stop_winds.append(None)
            continue
        v_idx = valid_map.get(i)
        if v_idx is None or v_idx >= len(weather_data):
            stop_winds.append(None)
            continue

        hourly = weather_data[v_idx].get('hourly', {})

        # Arrival estimate from route distance at a flat brevet speed.
        dist_km = float(stops[i].get('distance_km') or 0)
        hours_to_arrive = dist_km / _AVG_SPEED_KMH if _AVG_SPEED_KMH > 0 else 0
        arrival_dt = start_dt + timedelta(hours=hours_to_arrive)
        hour_index = get_hour_index(hourly.get('time', []), arrival_dt)

        wind_speed = _safe_get(hourly, 'wind_speed_10m', hour_index, 0.0)
        wind_dir = _safe_get(hourly, 'wind_direction_10m', hour_index, 0)
        temperature = _safe_get(hourly, 'temperature_2m', hour_index, 0.0)

        # Bearing: current -> next stop; for the last stop, previous -> current.
        bearing = 0.0
        if i + 1 < len(coords) and coords[i + 1] is not None:
            bearing = calculate_bearing(coord['lat'], coord['lng'],
                                        coords[i + 1]['lat'], coords[i + 1]['lng'])
        elif i > 0 and coords[i - 1] is not None:
            bearing = calculate_bearing(coords[i - 1]['lat'], coords[i - 1]['lng'],
                                        coord['lat'], coord['lng'])

        hw = headwind_component(wind_speed, wind_dir, bearing)
        cw = crosswind_component(wind_speed, wind_dir, bearing)
        wind_type = classify_wind(hw, cw)
        arrow_rotation = wind_arrow_rotation(hw, cw)
        stop_winds.append({
            'wind_speed_kmh': round(float(wind_speed), 1),
            'wind_type': wind_type,
            'headwind_kmh': round(float(hw), 1),
            'crosswind_kmh': round(float(cw), 1),
            'wind_arrow_deg': arrow_rotation,
            'arrow_rotation': arrow_rotation,
            'arrow_glyph': wind_arrow_glyph(arrow_rotation),
            'compass': compass_label(wind_dir),
            'wind_direction_deg': int(wind_dir),
            'style': wind_cell_style(wind_speed, wind_type),
            'temperature_c': round(float(temperature), 1),
            'data_source': data_source,
        })

    return stop_winds


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
        brevets = _rider_finished_brevets(rider_id)
        activities = [
            _summarize_for_list(a, analyzed, _match_activity_to_brevet(a, brevets))
            for a in owned.values()
        ]
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
        brevets = _rider_finished_brevets(rider_id)
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

    analysis = _build_analysis(activity, streams,
                               _match_activity_to_brevet(activity, brevets))
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
    if analysis and analysis.get('brevet') and not analysis.get('comparison') and cached.get('activity_streams'):
        try:
            streams = _decompress_streams(cached['activity_streams'])
            activity = {
                'id': activity_id,
                'name': (analysis.get('activity') or {}).get('name'),
                'start_date_local': ((analysis.get('activity') or {}).get('date') or '') + 'T00:00:00',
                'distance': ((analysis.get('activity') or {}).get('distance_km') or 0) * _METERS_PER_KM,
                'total_elevation_gain': ((analysis.get('activity') or {}).get('elevation_ft') or 0) / _M_TO_FT,
                'moving_time': 0,
                'elapsed_time': 0,
                'average_speed': 0,
            }
            rebuilt = _build_analysis(activity, streams, analysis.get('brevet'))
            if rebuilt.get('comparison'):
                analysis['plan'] = rebuilt.get('plan')
                analysis['comparison'] = rebuilt.get('comparison')
                analysis['map'] = rebuilt.get('map') or analysis.get('map')
        except Exception as e:  # noqa: BLE001
            current_app.logger.warning('analysis detail: cached comparison rebuild failed: %s', e)
    stop_winds = _historical_stop_winds(analysis) if analysis else None
    stop_wind_by_location = {}
    if analysis and stop_winds and (analysis.get('comparison') or {}).get('detected_stops'):
        for stop, wind in zip(analysis['comparison']['detected_stops'], stop_winds):
            name = stop.get('matched_stop_name')
            if name and wind:
                stop_wind_by_location[name] = wind
    return render_template('analysis_detail.html', analysis=analysis,
                           stop_winds=stop_winds, stop_wind_by_location=stop_wind_by_location,
                           activity_id=activity_id,
                           analyzed=analysis is not None)
