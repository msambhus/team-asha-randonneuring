"""Strava ride performance analysis.

Matches Strava activities to brevet rides, fetches stream data,
detects stoppages, and builds plan-vs-actual comparison data.
"""

import difflib
import html as html_mod
import json
import statistics
import zlib
from datetime import timedelta
from flask import current_app
import requests as http_requests


# ── Stream compression helpers ──────────────────────────────────────

def _compress_streams(streams_dict):
    """Compress a streams dict to bytes for BYTEA storage."""
    return zlib.compress(json.dumps(streams_dict).encode(), level=6)


def _decompress_streams(blob):
    """Decompress BYTEA blob back to a streams dict."""
    return json.loads(zlib.decompress(bytes(blob)))

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


_STREAM_KEYS = 'time,distance,velocity_smooth,heartrate,watts,cadence,altitude,grade_smooth,latlng'


def fetch_and_analyze(rider_id, match_id, strava_activity_id, plan_stops=None):
    """Fetch Strava streams and run stop detection analysis.

    Streams are fetched from Strava once and cached as compressed BYTEA in the
    ``strava_ride_analysis`` table.  Subsequent page loads decompress from the
    DB and skip the Strava API entirely.

    Returns:
        dict with 'detected_stops', 'stream_summary', 'streams', 'error'
    """
    from models import (get_strava_ride_analysis, upsert_strava_ride_analysis,
                        get_strava_connection)

    # ── 1. Check DB cache ────────────────────────────────────────────
    cached = get_strava_ride_analysis(match_id)

    # If we have cached streams (and no error), serve from DB — no API call
    if cached and cached.get('activity_streams') and not cached.get('strava_api_error'):
        streams = _decompress_streams(cached['activity_streams'])
        detected_stops = cached.get('detected_stops') or detect_stops(streams)
        if plan_stops and detected_stops:
            detected_stops = match_stops_to_plan(detected_stops, plan_stops)
        stream_summary = cached.get('stream_summary') or _build_stream_summary(streams)
        return {
            'detected_stops': detected_stops,
            'stream_summary': stream_summary,
            'streams': streams,
            'error': None,
        }

    # ── 2. No cached streams — fetch from Strava API ─────────────────
    cached_stops = cached['detected_stops'] if cached and not cached.get('strava_api_error') else None

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
                'keys': _STREAM_KEYS,
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

    # ── 3. Detect stops + build summary ──────────────────────────────
    if cached_stops is not None:
        detected_stops = cached_stops
        if plan_stops and detected_stops:
            detected_stops = match_stops_to_plan(detected_stops, plan_stops)
        stream_summary = (cached.get('stream_summary') or {}) if cached else {}
    else:
        detected_stops = detect_stops(streams)
        if plan_stops and detected_stops:
            detected_stops = match_stops_to_plan(detected_stops, plan_stops)
        stream_summary = _build_stream_summary(streams)

    # ── 4. Persist everything (stops + summary + compressed streams) ─
    compressed = _compress_streams(streams)
    upsert_strava_ride_analysis(
        match_id, detected_stops, stream_summary,
        compressed_streams=compressed,
    )

    return {
        'detected_stops': detected_stops,
        'stream_summary': stream_summary,
        'streams': streams,
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


STOP_ABSORPTION_RADIUS_MILES = 0.3  # unmatched stops within this radius of a matched waypoint are absorbed


def match_stops_to_plan(detected_stops, plan_stops):
    """Match detected Strava stops to planned control/rest points by distance.

    Uses plan-first greedy matching: each plan stop claims the nearest unmatched
    detected stop. After matching, extra detected stops within
    STOP_ABSORPTION_RADIUS_MILES of a matched plan waypoint are absorbed into
    that waypoint's duration and removed from the returned list.

    Absorption prevents double-subtraction in segment arithmetic: if a GPS drift
    produces a brief velocity drop just before a real control, that extra stop
    would otherwise appear in both `actual_stop_duration` (via merge) and
    `stops_in_seg` (because it falls inside the segment range). By absorbing it
    into the matched control's duration and removing it from the list entirely,
    the segment calculation stays correct.

    Tolerance for matching: min(3% of total distance, 3.0 miles).

    Returns:
        enriched detected_stops with matched_stop_name, matched_stop_type, is_extra
        (absorbed stops removed from the list)
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

    # Initialise all detected stops as unmatched extras
    for ds in detected_stops:
        ds['_matched'] = False
        ds['matched_stop_name'] = None
        ds['matched_stop_type'] = None
        ds['planned_duration_min'] = 0
        ds['is_extra'] = True

    # Build matchable plan stops (exclude start — finish is a real control)
    matchable = []
    for ps in plan_stops:
        stop_type = (ps.get('stop_type') or '').lower()
        if stop_type == 'start':
            continue
        matchable.append({
            'distance_miles': float(ps.get('distance_miles') or 0),
            'location': ps.get('location', ''),
            'stop_type': stop_type,
            'stop_duration_min': ps.get('stop_duration_min') or 0,
            'matched_ds': None,
        })

    # Plan-first matching: each plan stop claims the nearest unmatched detected stop
    for ps in matchable:
        best_ds = None
        best_diff = float('inf')
        for ds in detected_stops:
            if ds['_matched']:
                continue
            diff = abs(ds['distance_miles'] - ps['distance_miles'])
            if diff <= tolerance and diff < best_diff:
                best_diff = diff
                best_ds = ds

        if best_ds:
            best_ds['_matched'] = True
            best_ds['matched_stop_name'] = ps['location']
            best_ds['matched_stop_type'] = ps['stop_type']
            best_ds['planned_duration_min'] = ps['stop_duration_min']
            best_ds['is_extra'] = False
            ps['matched_ds'] = best_ds

    # Absorption pass: extra stops within STOP_ABSORPTION_RADIUS_MILES of a matched
    # waypoint are folded into that waypoint's duration and dropped from the list.
    absorbed_indices = set()
    for ps in matchable:
        if ps['matched_ds'] is None:
            continue
        matched_ds = ps['matched_ds']
        wp_dist = ps['distance_miles']
        for i, ds in enumerate(detected_stops):
            if ds['_matched']:
                continue
            if abs(ds['distance_miles'] - wp_dist) <= STOP_ABSORPTION_RADIUS_MILES:
                matched_ds['duration_s'] = matched_ds.get('duration_s', 0) + ds.get('duration_s', 0)
                matched_ds['duration_min'] = round(matched_ds['duration_s'] / 60, 1)
                absorbed_indices.add(i)

    # Clean up temp field and remove absorbed stops
    for ds in detected_stops:
        ds.pop('_matched', None)

    if absorbed_indices:
        detected_stops = [ds for i, ds in enumerate(detected_stops) if i not in absorbed_indices]

    return detected_stops


def _build_stream_interpolator(streams):
    """Build a function that interpolates elapsed time (minutes) at a given distance (miles).

    Uses Strava's distance (meters) and time (seconds) streams.
    Returns None if streams are unavailable.
    """
    if not streams:
        return None
    distance_m = streams.get('distance', [])
    time_s = streams.get('time', [])
    if not distance_m or not time_s or len(distance_m) != len(time_s):
        return None

    # Convert distance to miles once
    dist_miles = [d / METERS_PER_MILE for d in distance_m]

    def interpolate(target_miles):
        """Return elapsed time in minutes at the given distance in miles."""
        if target_miles <= 0:
            return 0.0
        if target_miles >= dist_miles[-1]:
            return time_s[-1] / 60.0
        # Binary search for bracket
        lo, hi = 0, len(dist_miles) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if dist_miles[mid] <= target_miles:
                lo = mid
            else:
                hi = mid
        # Linear interpolation
        d0, d1 = dist_miles[lo], dist_miles[hi]
        t0, t1 = time_s[lo], time_s[hi]
        if d1 == d0:
            return t0 / 60.0
        frac = (target_miles - d0) / (d1 - d0)
        return (t0 + frac * (t1 - t0)) / 60.0

    return interpolate


def build_comparison(plan_stops, detected_stops, activity, custom_stops=None,
                     plan_start_time=None, actual_start_time=None, streams=None):
    """Build comparison data structure for template rendering.

    Merges plan stops with detected actual stops into a unified timeline.

    Args:
        plan_stops: base plan stops list
        detected_stops: list from detect_stops() with matching info
        activity: strava activity dict (distance, moving_time, elapsed_time, etc.)
        custom_stops: optional custom plan stops
        plan_start_time: string like "07:00" from ride_plan.start_time
        actual_start_time: datetime from strava_activity.start_date_local
        streams: optional Strava streams dict with 'distance' and 'time' arrays

    Returns:
        dict with 'rows' (list of comparison rows), 'summary' (metrics dict)
    """
    from datetime import datetime as _dt

    actual_distance_miles = (activity.get('distance') or 0) / METERS_PER_MILE
    actual_moving_time_min = (activity.get('moving_time') or 0) / 60
    actual_elapsed_time_min = (activity.get('elapsed_time') or 0) / 60
    actual_stopped_time_min = actual_elapsed_time_min - actual_moving_time_min

    # Build stream interpolator for actual time at any distance
    interp = _build_stream_interpolator(streams)

    # Build segment HR/power averager from streams
    hr_stream = streams.get('heartrate', []) if streams else []
    watts_stream = streams.get('watts', []) if streams else []
    dist_stream_mi = [d / METERS_PER_MILE for d in streams.get('distance', [])] if streams else []

    def avg_stream_in_range(stream, start_mi, end_mi):
        """Average a stream array between two mile markers."""
        if not stream or not dist_stream_mi or len(stream) != len(dist_stream_mi):
            return None
        vals = [stream[i] for i in range(len(dist_stream_mi))
                if start_mi <= dist_stream_mi[i] <= end_mi and stream[i] is not None and stream[i] > 0]
        return round(sum(vals) / len(vals)) if vals else None

    velocity_stream = streams.get('velocity_smooth', []) if streams else []
    time_stream = streams.get('time', []) if streams else []

    def stopped_time_in_range(start_mi, end_mi):
        """Total stopped time (minutes) between two mile markers from velocity stream."""
        if not velocity_stream or not dist_stream_mi or not time_stream:
            return None
        if len(velocity_stream) != len(dist_stream_mi) or len(time_stream) != len(dist_stream_mi):
            return None
        stopped = 0.0
        for i in range(1, len(dist_stream_mi)):
            if not (start_mi <= dist_stream_mi[i] <= end_mi):
                continue
            if velocity_stream[i] < VELOCITY_THRESHOLD:
                dt = time_stream[i] - time_stream[i - 1]
                stopped += dt
        return round(stopped / 60, 1) if stopped > 0 else None

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

        # Actual cumulative time — ALWAYS use interpolated time at exact mile marker.
        # This avoids skew when a detected stop is at a slightly different distance
        # than the planned waypoint (e.g., Taco Bell at 148mi matched to Shell at 145mi).
        actual_cum_time = None
        cum_time_delta = None

        if stop_type == 'start':
            actual_cum_time = 0
            actual_stop_duration = 0
        elif stop_type == 'finish':
            actual_cum_time = round(actual_elapsed_time_min)
            actual_stop_duration = 0
            if plan_cum_time:
                cum_time_delta = round(actual_cum_time - plan_cum_time)
        elif interp and distance_miles > 0:
            # Use stream interpolation at exact mile marker
            actual_cum_time = round(interp(distance_miles))
            if not actual_stop:
                actual_stop_duration = 0
            if plan_cum_time:
                cum_time_delta = round(actual_cum_time - plan_cum_time)

        # Time of day calculations
        plan_tod = None
        if plan_start_dt and plan_cum_time:
            plan_tod_dt = plan_start_dt + timedelta(minutes=plan_cum_time)
            plan_tod = plan_tod_dt.strftime('%-I:%M%p').lower()
        elif stop_type == 'start' and plan_start_dt:
            plan_tod = plan_start_dt.strftime('%-I:%M%p').lower()

        actual_tod = None
        if actual_cum_time is not None and actual_start_time:
            actual_tod_dt = actual_start_time + timedelta(minutes=actual_cum_time)
            actual_tod = actual_tod_dt.strftime('%-I:%M%p').lower()

        # Custom plan data (base plan when custom exists, via the swap)
        custom_data = None
        if custom_stops:
            cs = custom_by_dist.get(distance_miles)
            if cs:
                cs_seg_time = cs.get('segment_time_min') or 0
                cs_seg_dist = float(cs.get('seg_dist') or 0)
                cs_speed = round(cs_seg_dist / (cs_seg_time / 60), 1) if cs_seg_time and cs_seg_dist else None
                cs_cum = cs.get('cum_time_min') or 0
                cs_stop_dur = cs.get('stop_duration_min') or 0
                cs_arrival = (cs_cum - cs_stop_dur) if cs_cum and cs_stop_dur else cs_cum
                cs_tod = None
                if plan_start_dt and cs_cum:
                    cs_tod = (plan_start_dt + timedelta(minutes=cs_cum)).strftime('%-I:%M%p').lower()
                elif stop_type == 'start' and plan_start_dt:
                    cs_tod = plan_start_dt.strftime('%-I:%M%p').lower()
                cs_bookend = cs.get('bookend_time_min')
                cs_time_bank = cs.get('time_bank_min')
                custom_data = {
                    'segment_time_min': cs_seg_time,
                    'stop_duration_min': cs_stop_dur,
                    'cum_time_min': cs_cum,
                    'arrival_time_min': cs_arrival,
                    'speed_mph': cs_speed,
                    'time_of_day': cs_tod,
                    'time_bank': cs_time_bank,
                }

        # Arrival time = cumulative time before the break at this stop
        plan_arrival_time = (plan_cum_time - plan_stop_duration) if plan_cum_time and plan_stop_duration else plan_cum_time
        actual_arrival_time = None
        if actual_cum_time is not None and actual_stop_duration is not None:
            actual_arrival_time = max(0, round(actual_cum_time - actual_stop_duration))

        # Segment speed (mph) from plan
        plan_seg_dist = ps.get('seg_dist') or 0
        plan_speed_mph = round(float(plan_seg_dist) / (plan_segment_min / 60), 1) if plan_segment_min and plan_seg_dist else None

        # Time bank from plan
        plan_time_bank = ps.get('time_bank_min')
        plan_bookend = ps.get('bookend_time_min')
        actual_time_bank = None
        if plan_bookend and actual_arrival_time is not None:
            actual_time_bank = round(plan_bookend - actual_arrival_time)

        row = {
            'location': location,
            'stop_type': stop_type,
            'distance_miles': distance_miles,
            'plan_segment_min': plan_segment_min,
            'plan_stop_duration_min': plan_stop_duration,
            'plan_cum_time_min': plan_cum_time,
            'plan_arrival_time_min': plan_arrival_time,
            'plan_speed_mph': plan_speed_mph,
            'plan_time_bank': plan_time_bank,
            'actual_stop_duration_min': actual_stop_duration,
            'actual_cum_time_min': actual_cum_time,
            'actual_arrival_time_min': actual_arrival_time,
            'actual_time_bank': actual_time_bank,
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

    # Insert extra (unplanned) stops — includes:
    # 1. Stops not matched to any waypoint (is_extra=True)
    # 2. Matched stops >10 min that are >3 mi from their matched waypoint
    #    (e.g., Taco Bell at 148mi matched to Shell at 145mi)
    waypoint_dists = {float(ps.get('distance_miles') or 0) for ps in plan_stops}
    extra_stops = []
    for ds in (detected_stops or []):
        if ds.get('is_extra'):
            extra_stops.append(ds)
        elif ds.get('matched_stop_name') and ds['duration_min'] >= 4:
            # Check if the detected stop is far from the matched waypoint
            matched_wp_dist = None
            for ps in plan_stops:
                if ps.get('location') == ds['matched_stop_name']:
                    matched_wp_dist = float(ps.get('distance_miles') or 0)
                    break
            if matched_wp_dist is not None and abs(ds['distance_miles'] - matched_wp_dist) > 3:
                extra_stops.append(ds)

    for es in extra_stops:
        es_dist = es['distance_miles']
        # Use interpolated time at exact distance for consistency
        if interp:
            actual_cum_time = round(interp(es_dist))
        elif es.get('start_time_s') is not None:
            actual_cum_time = round(es['start_time_s'] / 60)
        else:
            actual_cum_time = None

        actual_tod = None
        if actual_cum_time is not None and actual_start_time:
            actual_tod_dt = actual_start_time + timedelta(minutes=actual_cum_time)
            actual_tod = actual_tod_dt.strftime('%-I:%M%p').lower()

        extra_arrival = max(0, round(actual_cum_time - es['duration_min'])) if actual_cum_time is not None else None

        # Label: show matched name if it was a mislocated match, otherwise generic
        label = f"Unplanned stop @ {es_dist:.1f}mi"
        if es.get('matched_stop_name'):
            label = f"Break near {es['matched_stop_name'][:30]}"

        # Compute delta: interpolate plan cum_time at this distance
        extra_plan_cum = None
        extra_delta = None
        for j in range(1, len(plan_stops)):
            d0 = float(plan_stops[j-1].get('distance_miles') or 0)
            d1 = float(plan_stops[j].get('distance_miles') or 0)
            if d0 <= es_dist <= d1 and d1 > d0:
                t0 = plan_stops[j-1].get('cum_time_min') or 0
                t1 = plan_stops[j].get('cum_time_min') or 0
                frac = (es_dist - d0) / (d1 - d0)
                extra_plan_cum = round(t0 + frac * (t1 - t0))
                break
        if extra_plan_cum and actual_cum_time is not None:
            extra_delta = round(actual_cum_time - extra_plan_cum)

        row = {
            'location': label,
            'stop_type': 'extra',
            'distance_miles': es['distance_miles'],
            'plan_segment_min': None,
            'plan_stop_duration_min': None,
            'plan_cum_time_min': extra_plan_cum,
            'plan_arrival_time_min': extra_plan_cum,
            'plan_speed_mph': None,
            'plan_time_bank': None,
            'actual_stop_duration_min': es['duration_min'],
            'actual_cum_time_min': actual_cum_time,
            'actual_arrival_time_min': extra_arrival,
            'actual_time_bank': None,
            'cum_time_delta_min': extra_delta,
            'plan_time_of_day': None,
            'actual_time_of_day': actual_tod,
            'is_extra': True,
            'stop_delta_min': None,
            'custom': None,
        }
        rows.append(row)

    # Sort all rows by distance
    rows.sort(key=lambda r: r['distance_miles'])

    # Build lookup of all detected stops for segment break calculation
    all_detected = detected_stops or []

    # Calculate actual segment times, speeds, HR, power, and enroute breaks.
    #
    # Two "previous" pointers are maintained:
    #   prev_dist / prev_actual_departure  — advances for every row (extras + planned)
    #   prev_planned_dist / prev_planned_departure — advances only for planned rows
    #
    # For EXTRA rows: segment spans from the adjacent previous row (small sub-segment).
    # For PLANNED rows: segment spans from the last PLANNED row, so distance/time/speed
    #   reflect the full leg between waypoints even when an extra stop sits in between.
    prev_actual_departure = 0   # departure after any stop, advances every row
    prev_planned_departure = 0  # same, but only for planned rows
    prev_dist = 0.0
    prev_planned_dist = 0.0
    for row in rows:
        cur_dist = row['distance_miles']
        is_extra = row.get('is_extra', False)

        # Choose the correct "from" anchor based on row type
        from_dist = prev_dist if is_extra else prev_planned_dist
        from_departure = prev_actual_departure if is_extra else prev_planned_departure

        seg_dist = cur_dist - from_dist

        # Location used to exclude this row's matched stop from stops_in_seg.
        # A matched stop can sit slightly before cur_dist (GPS drift) and would
        # otherwise be double-subtracted — once via actual_arrival calculation
        # and again via stops_in_seg.
        row_location = row.get('location') if not is_extra else None

        # Per-segment HR and power from Strava streams
        row['actual_avg_hr'] = avg_stream_in_range(hr_stream, from_dist, cur_dist) if seg_dist > 0 else None
        row['actual_avg_watts'] = avg_stream_in_range(watts_stream, from_dist, cur_dist) if seg_dist > 0 else None

        if row['actual_cum_time_min'] is not None:
            actual_arrival = row.get('actual_arrival_time_min')
            if actual_arrival is None:
                actual_arrival = row['actual_cum_time_min']
            # Segment elapsed = arrival at this row minus departure from the anchor row
            seg_elapsed = actual_arrival - from_departure
            # Subtract detected stops strictly within this range to get riding time.
            # Exclude the stop matched to this waypoint (see row_location above).
            stops_in_seg = sum(
                ds['duration_min'] for ds in all_detected
                if from_dist < ds['distance_miles'] < cur_dist
                and ds.get('matched_stop_name') != row_location
            )
            actual_riding = seg_elapsed - stops_in_seg
            row['actual_segment_min'] = max(0, round(actual_riding))
            row['actual_speed_mph'] = round(seg_dist / (actual_riding / 60), 1) if actual_riding > 0 and seg_dist > 0 else None
            # Always advance the every-row pointer
            prev_actual_departure = row['actual_cum_time_min']
            # Only advance the planned pointer for planned rows
            if not is_extra:
                prev_planned_departure = row['actual_cum_time_min']
        else:
            row['actual_segment_min'] = None
            row['actual_speed_mph'] = None

        # Enroute: unplanned stopped time in this row's range (from_dist → cur_dist).
        # Subtract ALL detected stops in the range (they appear as their own rows).
        raw_stopped = stopped_time_in_range(from_dist, cur_dist)
        known_stops_in_seg = sum(
            ds['duration_min'] for ds in all_detected
            if from_dist <= ds['distance_miles'] <= cur_dist
            and ds.get('matched_stop_name') != row_location
        )
        unplanned = (raw_stopped - known_stops_in_seg) if raw_stopped else None
        row['actual_seg_break_min'] = round(unplanned, 1) if unplanned and unplanned > 0.5 else None

        # Always advance prev_dist; advance prev_planned_dist only for planned rows
        prev_dist = cur_dist
        if not is_extra:
            prev_planned_dist = cur_dist

    # Plan total time
    plan_total_time_min = plan_stops[-1].get('cum_time_min', 0) if plan_stops else 0
    plan_total_distance = float(plan_stops[-1].get('distance_miles', 0)) if plan_stops else 0
    plan_total_elevation = plan_stops[-1].get('elevation_gain', 0) if plan_stops else 0

    # Base plan total time (when custom_stops provided, that's the base plan)
    base_total_time_min = custom_stops[-1].get('cum_time_min', 0) if custom_stops else None

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
        'base_total_time_min': base_total_time_min,
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


def _dynamic_display_range(metric, min_val, max_val):
    """Compute a padded display range from actual data values.

    Adds ~15% of the data spread as padding on each side (subject to a
    per-metric minimum so the chart has breathing room even when all riders
    have nearly identical values).  A floor is applied so axes never go
    below a physically meaningful minimum.
    """
    spread = max_val - min_val
    # Minimum absolute padding (used when 15% of spread would be too small)
    MIN_PAD = {
        'average_heartrate':      4.0,
        'max_heartrate':          4.0,
        'average_speed':          0.224,   # ~0.5 mph in m/s
        'average_watts':          10.0,
        'weighted_average_watts': 10.0,
        'elapsed_time':           900.0,   # 15 min in seconds
        'moving_time':            900.0,
        'stopped_time':           300.0,   # 5 min
        'total_elevation_gain':   200.0,
        'suffer_score':           25.0,
    }
    # Axis can never go below this value
    FLOOR = {
        'average_heartrate':      50.0,
        'max_heartrate':          80.0,
        'average_speed':          1.0,
        'average_watts':          0.0,
        'weighted_average_watts': 0.0,
        'elapsed_time':           0.0,
        'moving_time':            0.0,
        'stopped_time':           0.0,
        'total_elevation_gain':   0.0,
        'suffer_score':           0.0,
    }
    pad = max(spread * 0.15, MIN_PAD.get(metric, max(spread * 0.1, 1.0)))
    d_min = max(FLOOR.get(metric, 0.0), min_val - pad)
    d_max = max_val + pad
    return d_min, d_max


def build_cohort_stats(riders, current_rider_id, ride_distance_km=None):
    """Compute per-metric group statistics and insight callouts for cohort comparison.

    Args:
        riders: list of dicts from get_ride_cohort_stats()
        current_rider_id: int or None (logged-in rider's DB id)

    Returns:
        dict keyed by metric name:
        {has_data, min, max, mean, median, user_value, bar_position,
         percentile, direction, values_sorted, insight}
    """
    # (metric_key, better_direction) — 'lower'/'higher'/None(reference)
    METRICS = [
        ('elapsed_time',           'lower'),
        ('moving_time',            'lower'),
        ('stopped_time',           'lower'),
        ('average_speed',          'higher'),
        ('average_heartrate',      None),
        ('max_heartrate',          None),
        ('total_elevation_gain',   None),
        ('suffer_score',           None),
        ('average_watts',          'higher'),
        ('weighted_average_watts', 'higher'),
    ]

    result = {}
    for metric, direction in METRICS:
        values = [float(r[metric]) for r in riders if r.get(metric) is not None]
        if not values:
            result[metric] = {'has_data': False}
            continue

        user_value = None
        for r in riders:
            if r['rider_id'] == current_rider_id and r.get(metric) is not None:
                user_value = float(r[metric])
                break

        sorted_vals = sorted(values)
        n = len(sorted_vals)
        median_val = statistics.median(sorted_vals)
        mean_val = statistics.mean(sorted_vals)
        min_val = sorted_vals[0]
        max_val = sorted_vals[-1]

        # Compute display range dynamically from actual data values
        d_min, d_max = _dynamic_display_range(metric, min_val, max_val)

        # Percentile: % of cohort the user beats on this metric
        percentile = None
        if user_value is not None and n > 1:
            if direction == 'lower':
                percentile = round(sum(1 for v in sorted_vals if v > user_value) / n * 100)
            elif direction == 'higher':
                percentile = round(sum(1 for v in sorted_vals if v < user_value) / n * 100)

        # Bar position: 0-100 along the min-max range (for the visual percentile bar)
        bar_position = None
        if user_value is not None and max_val > min_val:
            bar_position = round((user_value - min_val) / (max_val - min_val) * 100)
        elif user_value is not None:
            bar_position = 50

        result[metric] = {
            'has_data': True,
            'min': min_val,
            'max': max_val,
            'display_min': d_min,
            'display_max': d_max,
            'mean': round(mean_val, 1),
            'median': median_val,
            'user_value': user_value,
            'bar_position': bar_position,
            'percentile': percentile,
            'direction': direction,
            'values_sorted': sorted_vals,
            'insight': _get_cohort_insight(metric, user_value, median_val),
        }

    # Normalize HR display range so avg and max charts share the same axis,
    # making the two distributions visually comparable.
    hr_avg = result.get('average_heartrate', {})
    hr_max = result.get('max_heartrate', {})
    if hr_avg.get('has_data') and hr_max.get('has_data'):
        shared_min = min(hr_avg['display_min'], hr_max['display_min'])
        shared_max = max(hr_avg['display_max'], hr_max['display_max'])
        hr_avg['display_min'] = hr_max['display_min'] = shared_min
        hr_avg['display_max'] = hr_max['display_max'] = shared_max

    # Add pre-formatted display strings so templates don't need format functions
    _add_cohort_display_strings(result)
    return result


def _add_cohort_display_strings(cohort_stats):
    """Mutate cohort_stats in place to add min_str/max_str/mean_str/median_str/user_str."""
    def _fmt_time(s):
        if s is None:
            return '\u2014'
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        return f'{h}h {m:02d}m'

    def _fmt_speed(ms):
        if ms is None:
            return '\u2014'
        return f'{ms * 2.23694:.1f} mph'

    def _fmt_bpm(v):
        if v is None:
            return '\u2014'
        return f'{int(v)} bpm'

    def _fmt_watts(v):
        if v is None:
            return '\u2014'
        return f'{int(v)} W'

    def _fmt_elev(v):
        if v is None:
            return '\u2014'
        return f'{int(v * 3.28084):,} ft'

    def _fmt_int(v):
        if v is None:
            return '\u2014'
        return str(int(v))

    formatters = {
        'elapsed_time':           _fmt_time,
        'moving_time':            _fmt_time,
        'stopped_time':           _fmt_time,
        'average_speed':          _fmt_speed,
        'average_heartrate':      _fmt_bpm,
        'max_heartrate':          _fmt_bpm,
        'total_elevation_gain':   _fmt_elev,
        'suffer_score':           _fmt_int,
        'average_watts':          _fmt_watts,
        'weighted_average_watts': _fmt_watts,
    }

    for metric, stat in cohort_stats.items():
        if not stat.get('has_data'):
            continue
        fmt = formatters.get(metric, _fmt_int)
        stat['min_str']         = fmt(stat['min'])
        stat['max_str']         = fmt(stat['max'])
        stat['display_min_str'] = fmt(stat['display_min'])
        stat['display_max_str'] = fmt(stat['display_max'])
        stat['mean_str']        = fmt(stat['mean'])
        stat['median_str']      = fmt(stat['median'])
        stat['user_str']        = fmt(stat['user_value']) if stat['user_value'] is not None else None


def _get_cohort_insight(metric, user_value, median_val):
    """Return an improvement insight string if user is meaningfully off the median."""
    if user_value is None:
        return None

    diff = user_value - median_val

    if metric == 'stopped_time':
        if diff > 900:  # > 15 min more than median
            extra_min = round(diff / 60)
            return (f"You spent {extra_min} more minutes stopped than the average finisher. "
                    "Faster control check-ins and pre-planned food drops can recover this time.")

    elif metric == 'average_speed':
        if diff < -0.45:  # > ~1 mph below median
            mph_diff = round(abs(diff) * 2.23694, 1)
            return (f"Your moving speed was {mph_diff} mph below the group average — "
                    "a targeted base training block can close this gap over the season.")

    elif metric == 'average_heartrate':
        if diff > 10:  # > 10 bpm above median
            bpm_diff = round(diff)
            return (f"Your average HR was {bpm_diff} bpm above the group average. "
                    "More base miles typically lower HR at the same effort level.")

    return None


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
