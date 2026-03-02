"""Strava ride performance analysis.

Matches Strava activities to brevet rides, fetches stream data,
detects stoppages, and builds plan-vs-actual comparison data.
"""

import difflib
import html as html_mod
from datetime import timedelta
from flask import current_app
import requests as http_requests

# Stop detection constants
VELOCITY_THRESHOLD = 0.5   # m/s (~1 mph) - below this = stopped
MIN_STOP_DURATION = 120    # seconds (2 minutes) - ignore shorter stops
METERS_PER_MILE = 1609.34
METERS_PER_KM = 1000


def find_matching_activity(rider_id, ride_date, ride_distance_km, ride_name):
    """Match a brevet ride to a Strava activity for a rider.

    Matching criteria:
    1. Date: activity within +-1 day of ride date
    2. Distance: within +-20% of ride distance
    3. Tiebreaker: fuzzy name match

    Returns:
        strava_activity row dict or None
    """
    from models import get_strava_activities_in_date_range

    date_start = ride_date - timedelta(days=1)
    date_end = ride_date + timedelta(days=1)

    current_app.logger.info(
        f'find_matching_activity: rider={rider_id} date={ride_date} '
        f'dist_km={ride_distance_km} name="{ride_name}" '
        f'range={date_start} to {date_end}'
    )

    activities = get_strava_activities_in_date_range(rider_id, date_start, date_end)
    current_app.logger.info(
        f'find_matching_activity: {len(activities) if activities else 0} activities in date range'
    )
    if not activities:
        return None

    target_distance_m = ride_distance_km * METERS_PER_KM
    tolerance = target_distance_m * 0.20

    # Filter by distance
    candidates = []
    for a in activities:
        dist = a.get('distance') or 0
        diff = abs(dist - target_distance_m)
        current_app.logger.info(
            f'  activity {a.get("strava_activity_id")}: '
            f'dist={dist:.0f}m diff={diff:.0f}m tol={tolerance:.0f}m '
            f'match={diff <= tolerance}'
        )
        if diff <= tolerance:
            candidates.append(a)

    if not candidates:
        current_app.logger.info('find_matching_activity: no candidates after distance filter')
        return None

    if len(candidates) == 1:
        return dict(candidates[0])

    # Multiple candidates — use fuzzy name matching as tiebreaker
    clean_ride_name = html_mod.unescape(ride_name or '').lower().strip()

    best_match = None
    best_score = -1

    for a in candidates:
        activity_name = (a.get('name') or '').lower().strip()
        score = difflib.SequenceMatcher(None, clean_ride_name, activity_name).ratio()
        # Also factor in distance closeness (prefer closer distance match)
        dist_diff = abs((a.get('distance') or 0) - target_distance_m) / target_distance_m
        combined_score = score * 0.7 + (1 - dist_diff) * 0.3

        if combined_score > best_score:
            best_score = combined_score
            best_match = a

    return dict(best_match) if best_match else None


def batch_match_rides(rider_id, participation_list):
    """Batch detect Strava matches for a list of FINISHED rides.

    Only queries local DB — no Strava API calls.

    Args:
        rider_id: int
        participation_list: list of ride dicts with ride_id, date, distance_km, ride_name, status

    Returns:
        dict: {ride_id: {strava_activity_id, strava_url}}
    """
    from models import (get_all_strava_ride_matches, create_strava_ride_match,
                        get_strava_connection)

    # Only match finished rides
    finished_rides = [
        p for p in participation_list
        if p.get('status', '').upper() == 'FINISHED' and p.get('ride_id')
    ]
    if not finished_rides:
        current_app.logger.debug(f'batch_match_rides: no finished rides for rider {rider_id}')
        return {}

    # Check if rider has Strava connected
    conn = get_strava_connection(rider_id)
    if not conn:
        current_app.logger.debug(f'batch_match_rides: no strava connection for rider {rider_id}')
        return {}

    ride_ids = [p['ride_id'] for p in finished_rides]
    current_app.logger.info(f'batch_match_rides: rider {rider_id}, {len(finished_rides)} finished rides, ride_ids={ride_ids}')

    # Get existing matches
    existing = get_all_strava_ride_matches(rider_id, ride_ids)
    current_app.logger.info(f'batch_match_rides: {len(existing)} existing matches')

    # Try to match unmatched rides
    for p in finished_rides:
        rid = p['ride_id']
        if rid in existing:
            continue

        try:
            match = find_matching_activity(
                rider_id=rider_id,
                ride_date=p['date'],
                ride_distance_km=p['distance_km'],
                ride_name=p.get('ride_name', ''),
            )
            current_app.logger.info(
                f'batch_match_rides: ride {rid} ({p.get("ride_name")}) '
                f'date={p["date"]} dist={p["distance_km"]}km -> '
                f'match={"YES " + str(match.get("strava_activity_id")) if match else "NONE"}'
            )
            if match:
                create_strava_ride_match(rider_id, rid, match['strava_activity_id'])
                existing[rid] = {
                    'ride_id': rid,
                    'strava_activity_id': match['strava_activity_id'],
                    'strava_url': match.get('strava_url'),
                }
        except Exception as e:
            current_app.logger.error(f'batch_match_rides: error matching ride {rid}: {e}', exc_info=True)

    return existing


def fetch_and_analyze(rider_id, match_id, strava_activity_id, plan_stops=None):
    """Fetch Strava streams and run stop detection analysis.

    Returns cached results if available. Fetches from Strava API otherwise.

    Args:
        rider_id: int
        match_id: strava_ride_match.id
        strava_activity_id: Strava activity ID (bigint)
        plan_stops: list of plan stop dicts (optional, for stop matching)

    Returns:
        dict with 'detected_stops', 'stream_summary', 'error'
    """
    from models import (get_strava_ride_analysis, upsert_strava_ride_analysis,
                        get_strava_connection)

    # Check cache
    cached = get_strava_ride_analysis(match_id)
    if cached and not cached.get('strava_api_error'):
        return {
            'detected_stops': cached['detected_stops'] or [],
            'stream_summary': cached['stream_summary'] or {},
            'error': None,
        }

    # Fetch streams from Strava API
    connection = get_strava_connection(rider_id)
    if not connection:
        error_msg = 'No Strava connection found'
        upsert_strava_ride_analysis(match_id, [], {}, error=error_msg)
        return {'detected_stops': [], 'stream_summary': {}, 'error': error_msg}

    try:
        from services.strava import _get_valid_token
        token = _get_valid_token(connection)

        resp = http_requests.get(
            f"{current_app.config['STRAVA_API_BASE']}/activities/{strava_activity_id}/streams",
            headers={'Authorization': f'Bearer {token}'},
            params={
                'keys': 'time,distance,velocity_smooth,heartrate,watts',
                'key_type': 'time',
            },
            timeout=15,
        )

        if resp.status_code == 429:
            error_msg = 'Strava rate limit reached. Try again in 15 minutes.'
            upsert_strava_ride_analysis(match_id, [], {}, error=error_msg)
            return {'detected_stops': [], 'stream_summary': {}, 'error': error_msg}

        if resp.status_code == 404:
            error_msg = 'Strava activity not found or is private.'
            upsert_strava_ride_analysis(match_id, [], {}, error=error_msg)
            return {'detected_stops': [], 'stream_summary': {}, 'error': error_msg}

        if not resp.ok:
            error_msg = f'Strava API error ({resp.status_code})'
            upsert_strava_ride_analysis(match_id, [], {}, error=error_msg)
            return {'detected_stops': [], 'stream_summary': {}, 'error': error_msg}

        # Parse streams response — Strava returns list of {type, data, ...}
        streams_raw = resp.json()
        streams = {}
        for s in streams_raw:
            streams[s['type']] = s['data']

    except Exception as e:
        error_msg = f'Failed to fetch streams: {str(e)}'
        upsert_strava_ride_analysis(match_id, [], {}, error=error_msg)
        return {'detected_stops': [], 'stream_summary': {}, 'error': error_msg}

    # Detect stops
    detected_stops = detect_stops(streams)

    # Match to plan if available
    if plan_stops and detected_stops:
        detected_stops = match_stops_to_plan(detected_stops, plan_stops)

    # Build stream summary
    stream_summary = _build_stream_summary(streams)

    # Cache results
    upsert_strava_ride_analysis(match_id, detected_stops, stream_summary)

    return {
        'detected_stops': detected_stops,
        'stream_summary': stream_summary,
        'error': None,
    }


def detect_stops(streams):
    """Detect stoppages from velocity and distance streams.

    Walks velocity_smooth array. When velocity < threshold for > min_duration,
    records a stop with its distance position and duration.

    Returns:
        list of dicts: [{distance_miles, start_time_s, duration_s, duration_min}]
    """
    velocity = streams.get('velocity_smooth', [])
    distance = streams.get('distance', [])
    time_arr = streams.get('time', [])

    if not velocity or not distance or not time_arr:
        return []

    stops = []
    in_stop = False
    stop_start_idx = 0

    for i in range(len(velocity)):
        if velocity[i] < VELOCITY_THRESHOLD:
            if not in_stop:
                in_stop = True
                stop_start_idx = i
        else:
            if in_stop:
                duration_s = time_arr[i] - time_arr[stop_start_idx]
                if duration_s >= MIN_STOP_DURATION:
                    stops.append({
                        'distance_miles': round(distance[stop_start_idx] / METERS_PER_MILE, 1),
                        'start_time_s': time_arr[stop_start_idx],
                        'duration_s': duration_s,
                        'duration_min': round(duration_s / 60, 1),
                    })
                in_stop = False

    # Handle stop at end of ride (finish)
    if in_stop and len(time_arr) > 0:
        duration_s = time_arr[-1] - time_arr[stop_start_idx]
        if duration_s >= MIN_STOP_DURATION:
            stops.append({
                'distance_miles': round(distance[stop_start_idx] / METERS_PER_MILE, 1),
                'start_time_s': time_arr[stop_start_idx],
                'duration_s': duration_s,
                'duration_min': round(duration_s / 60, 1),
            })

    return stops


def match_stops_to_plan(detected_stops, plan_stops):
    """Match detected Strava stops to planned control/rest points by distance.

    Uses greedy matching — each plan stop matched at most once.
    Tolerance: min(3% of total distance, 3.0 miles).

    Returns:
        enriched detected_stops with matched_stop_name, matched_stop_type, is_extra
    """
    if not plan_stops or not detected_stops:
        for stop in detected_stops:
            stop['matched_stop_name'] = None
            stop['matched_stop_type'] = None
            stop['is_extra'] = True
        return detected_stops

    # Get total distance for tolerance calculation
    total_dist = max((float(s.get('distance_miles') or 0) for s in plan_stops), default=0)
    tolerance = min(total_dist * 0.03, 3.0) if total_dist > 0 else 3.0

    # Build matchable plan stops (exclude start/finish — those aren't "controls")
    matchable = []
    for ps in plan_stops:
        stop_type = (ps.get('stop_type') or '').lower()
        if stop_type in ('start', 'finish'):
            continue
        matchable.append({
            'distance_miles': float(ps.get('distance_miles') or 0),
            'location': ps.get('location', ''),
            'stop_type': stop_type,
            'stop_duration_min': ps.get('stop_duration_min') or 0,
            'matched': False,
        })

    # Greedy match: for each detected stop, find nearest unmatched plan stop
    for ds in detected_stops:
        ds_dist = ds['distance_miles']
        best_plan = None
        best_diff = float('inf')

        for ps in matchable:
            if ps['matched']:
                continue
            diff = abs(ds_dist - ps['distance_miles'])
            if diff <= tolerance and diff < best_diff:
                best_diff = diff
                best_plan = ps

        if best_plan:
            best_plan['matched'] = True
            ds['matched_stop_name'] = best_plan['location']
            ds['matched_stop_type'] = best_plan['stop_type']
            ds['planned_duration_min'] = best_plan['stop_duration_min']
            ds['is_extra'] = False
        else:
            ds['matched_stop_name'] = None
            ds['matched_stop_type'] = None
            ds['planned_duration_min'] = 0
            ds['is_extra'] = True

    return detected_stops


def build_comparison(plan_stops, detected_stops, activity, custom_stops=None,
                     plan_start_time=None, actual_start_time=None):
    """Build comparison data structure for template rendering.

    Merges plan stops with detected actual stops into a unified timeline.

    Args:
        plan_stops: base plan stops list
        detected_stops: list from detect_stops() with matching info
        activity: strava activity dict (distance, moving_time, elapsed_time, etc.)
        custom_stops: optional custom plan stops
        plan_start_time: string like "07:00" from ride_plan.start_time
        actual_start_time: datetime from strava_activity.start_date_local

    Returns:
        dict with 'rows' (list of comparison rows), 'summary' (metrics dict)
    """
    from datetime import datetime as _dt

    actual_distance_miles = (activity.get('distance') or 0) / METERS_PER_MILE
    actual_moving_time_min = (activity.get('moving_time') or 0) / 60
    actual_elapsed_time_min = (activity.get('elapsed_time') or 0) / 60
    actual_stopped_time_min = actual_elapsed_time_min - actual_moving_time_min
    actual_elevation_ft = (activity.get('total_elevation_gain') or 0) * 3.28084
    actual_avg_speed_mph = (activity.get('average_speed') or 0) * 2.23694

    # Parse plan start time for TOD calculations
    plan_start_dt = None
    if plan_start_time:
        try:
            h, m = map(int, str(plan_start_time).split(':'))
            plan_start_dt = _dt(2000, 1, 1, h, m)
        except (ValueError, AttributeError):
            plan_start_dt = None

    # Build a map of detected stops by approximate distance for matching
    detected_by_dist = {}
    for ds in (detected_stops or []):
        detected_by_dist[ds['distance_miles']] = ds

    # Build matched stop lookup: plan stop location → detected stop
    matched_stops_by_name = {}
    for ds in (detected_stops or []):
        if ds.get('matched_stop_name'):
            matched_stops_by_name[ds['matched_stop_name']] = ds

    # Build custom stop lookup if available
    custom_by_dist = {}
    if custom_stops:
        for cs in custom_stops:
            dist = float(cs.get('distance_miles') or 0)
            custom_by_dist[dist] = cs

    # Build comparison rows from plan stops
    rows = []

    for ps in plan_stops:
        location = ps.get('location', '')
        stop_type = (ps.get('stop_type') or '').lower()
        distance_miles = float(ps.get('distance_miles') or 0)
        plan_segment_min = ps.get('segment_time_min') or 0
        plan_stop_duration = ps.get('stop_duration_min') or 0
        plan_cum_time = ps.get('cum_time_min') or 0

        # Look for matched actual stop
        actual_stop = matched_stops_by_name.get(location)
        actual_stop_duration = actual_stop['duration_min'] if actual_stop else None

        # Actual cumulative time (seconds from ride start → minutes)
        actual_cum_time = None
        cum_time_delta = None
        if actual_stop and actual_stop.get('start_time_s') is not None:
            actual_cum_time = round(actual_stop['start_time_s'] / 60)
            if plan_cum_time:
                cum_time_delta = round(actual_cum_time - plan_cum_time)

        # Time of day calculations
        plan_tod = None
        if plan_start_dt and plan_cum_time:
            plan_tod_dt = plan_start_dt + timedelta(minutes=plan_cum_time)
            plan_tod = plan_tod_dt.strftime('%H:%M')

        actual_tod = None
        if actual_stop and actual_start_time and actual_stop.get('start_time_s') is not None:
            actual_tod_dt = actual_start_time + timedelta(seconds=actual_stop['start_time_s'])
            actual_tod = actual_tod_dt.strftime('%H:%M')

        # Custom plan data
        custom_data = None
        if custom_stops:
            cs = custom_by_dist.get(distance_miles)
            if cs:
                custom_data = {
                    'segment_time_min': cs.get('segment_time_min') or 0,
                    'stop_duration_min': cs.get('stop_duration_min') or 0,
                    'cum_time_min': cs.get('cum_time_min') or 0,
                }

        row = {
            'location': location,
            'stop_type': stop_type,
            'distance_miles': distance_miles,
            'plan_segment_min': plan_segment_min,
            'plan_stop_duration_min': plan_stop_duration,
            'plan_cum_time_min': plan_cum_time,
            'actual_stop_duration_min': actual_stop_duration,
            'actual_cum_time_min': actual_cum_time,
            'cum_time_delta_min': cum_time_delta,
            'plan_time_of_day': plan_tod,
            'actual_time_of_day': actual_tod,
            'is_extra': False,
            'custom': custom_data,
        }

        # Time delta for stop duration
        if actual_stop_duration is not None and plan_stop_duration:
            row['stop_delta_min'] = round(actual_stop_duration - plan_stop_duration, 1)
        else:
            row['stop_delta_min'] = None

        rows.append(row)

    # Insert extra (unplanned) stops at their correct distance position
    extra_stops = [ds for ds in (detected_stops or []) if ds.get('is_extra')]
    for es in extra_stops:
        actual_cum_time = round(es['start_time_s'] / 60) if es.get('start_time_s') is not None else None

        actual_tod = None
        if actual_start_time and es.get('start_time_s') is not None:
            actual_tod_dt = actual_start_time + timedelta(seconds=es['start_time_s'])
            actual_tod = actual_tod_dt.strftime('%H:%M')

        row = {
            'location': f"Unplanned stop",
            'stop_type': 'extra',
            'distance_miles': es['distance_miles'],
            'plan_segment_min': None,
            'plan_stop_duration_min': None,
            'plan_cum_time_min': None,
            'actual_stop_duration_min': es['duration_min'],
            'actual_cum_time_min': actual_cum_time,
            'cum_time_delta_min': None,
            'plan_time_of_day': None,
            'actual_time_of_day': actual_tod,
            'is_extra': True,
            'stop_delta_min': None,
            'custom': None,
        }
        rows.append(row)

    # Sort all rows by distance
    rows.sort(key=lambda r: r['distance_miles'])

    # Plan total time
    plan_total_time_min = plan_stops[-1].get('cum_time_min', 0) if plan_stops else 0
    plan_total_distance = float(plan_stops[-1].get('distance_miles', 0)) if plan_stops else 0
    plan_total_elevation = plan_stops[-1].get('elevation_gain', 0) if plan_stops else 0

    # Planned avg speed
    plan_moving_time = sum(s.get('segment_time_min', 0) or 0 for s in plan_stops)
    plan_avg_speed = (plan_total_distance / (plan_moving_time / 60)) if plan_moving_time > 0 else 0

    # Total planned break time
    plan_break_time = sum(s.get('stop_duration_min', 0) or 0 for s in plan_stops)
    actual_total_stops_min = sum(ds['duration_min'] for ds in (detected_stops or []))

    summary = {
        'plan_distance_miles': round(plan_total_distance, 1),
        'actual_distance_miles': round(actual_distance_miles, 1),
        'plan_elevation_ft': plan_total_elevation,
        'actual_elevation_ft': round(actual_elevation_ft),
        'plan_total_time_min': plan_total_time_min,
        'actual_elapsed_time_min': round(actual_elapsed_time_min),
        'actual_moving_time_min': round(actual_moving_time_min),
        'plan_break_time_min': plan_break_time,
        'actual_stopped_time_min': round(actual_stopped_time_min),
        'actual_stops_from_streams_min': round(actual_total_stops_min, 1),
        'plan_avg_speed_mph': round(plan_avg_speed, 1),
        'actual_avg_speed_mph': round(actual_avg_speed_mph, 1),
        'stops_planned': len([s for s in plan_stops if (s.get('stop_duration_min') or 0) > 0]),
        'stops_detected': len(detected_stops or []),
        'stops_extra': len(extra_stops),
        # Deltas (positive = over plan, negative = under plan)
        'distance_delta_miles': round(actual_distance_miles - plan_total_distance, 1) if plan_total_distance else None,
        'elevation_delta_ft': round(actual_elevation_ft - (plan_total_elevation or 0)) if plan_total_elevation else None,
        'time_delta_min': round(actual_elapsed_time_min - plan_total_time_min) if plan_total_time_min else None,
        'speed_delta_mph': round(actual_avg_speed_mph - plan_avg_speed, 1) if plan_avg_speed else None,
        'break_delta_min': round(actual_stopped_time_min - plan_break_time) if plan_break_time else None,
    }

    # HR/Power data from activity
    hr_power = {}
    if activity.get('has_heartrate'):
        hr_power['avg_hr'] = activity.get('average_heartrate')
        hr_power['max_hr'] = activity.get('max_heartrate')
    if activity.get('device_watts'):
        hr_power['avg_watts'] = activity.get('average_watts')
        hr_power['max_watts'] = activity.get('max_watts')
        hr_power['weighted_avg_watts'] = activity.get('weighted_average_watts')
        hr_power['kilojoules'] = activity.get('kilojoules')
    if activity.get('suffer_score'):
        hr_power['suffer_score'] = activity['suffer_score']

    return {
        'rows': rows,
        'summary': summary,
        'hr_power': hr_power,
    }


def _build_stream_summary(streams):
    """Build summary metrics from raw stream data."""
    summary = {}

    time_arr = streams.get('time', [])
    if time_arr:
        summary['total_time_s'] = time_arr[-1] if time_arr else 0

    distance = streams.get('distance', [])
    if distance:
        summary['total_distance_m'] = distance[-1] if distance else 0

    velocity = streams.get('velocity_smooth', [])
    if velocity:
        moving_velocities = [v for v in velocity if v > VELOCITY_THRESHOLD]
        if moving_velocities:
            summary['avg_moving_speed_mph'] = round(
                (sum(moving_velocities) / len(moving_velocities)) * 2.23694, 1
            )

    hr = streams.get('heartrate', [])
    if hr:
        summary['avg_hr'] = round(sum(hr) / len(hr), 1)
        summary['max_hr'] = max(hr)

    watts = streams.get('watts', [])
    if watts:
        non_zero = [w for w in watts if w > 0]
        if non_zero:
            summary['avg_watts'] = round(sum(non_zero) / len(non_zero), 1)
            summary['max_watts'] = max(non_zero)

    return summary
