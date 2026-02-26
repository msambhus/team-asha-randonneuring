"""RideWithGPS API service — fetch routes, extract controls, build ride plans."""
import re
import math
import requests as http_requests
from flask import current_app


# ── Constants ──────────────────────────────────────────────────────────

METERS_TO_MILES = 1 / 1609.344
METERS_TO_FEET = 3.28084

# ACP/RUSA standard cutoff hours by brevet distance
_CUTOFF_HOURS = {200: 13.5, 300: 20, 400: 27, 600: 40, 1000: 75, 1200: 90}

# RWGPS course_point type → our stop_type
_RWGPS_TYPE_MAP = {
    'Start': 'start',
    'End': 'finish',
    'Control': 'control',
    'Food': 'rest',
    'Water': 'rest',
    'Summit': 'waypoint',
    'Valley': 'waypoint',
    'Danger': 'waypoint',
    'Generic': None,  # classify by name via detect_stop_type()
}

# Course point types to include as stops (skip navigation cues like Left/Right/Straight)
_CONTROL_TYPES = {'Start', 'End', 'Control', 'Food', 'Water', 'Generic'}


# ── Shared helpers (canonical location) ────────────────────────────────

def extract_rwgps_route_id(url):
    """Extract numeric route ID from an RWGPS URL."""
    if not url:
        return None
    m = re.search(r'/routes/(\d+)', url)
    return m.group(1) if m else None


def slugify(name):
    """Convert a name to a URL-friendly slug."""
    s = name.lower().strip()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return s.strip('-')


def detect_stop_type(location):
    """Classify stop type from location name keywords."""
    loc = location.lower()
    if 'start' in loc and 'finish' not in loc:
        return 'start'
    elif 'finish' in loc:
        return 'finish'
    elif 'control' in loc:
        return 'control'
    elif any(w in loc for w in ['water', 'refill', 'snack', 'lunch', 'dinner',
                                 'food', 'break', 'coffee', 'epp selfie']):
        return 'rest'
    else:
        return 'waypoint'


def _get_cutoff_hours(km):
    """Get ACP/RUSA standard cutoff hours for a brevet distance."""
    if not km:
        return None
    for limit in sorted(_CUTOFF_HOURS):
        if km <= limit:
            return _CUTOFF_HOURS[limit]
    return None


def _extract_distance_km(name):
    """Extract brevet distance in km from a plan name (e.g., '300k' → 300)."""
    match = re.search(r'(\d{3,4})\s*[kK]', name)
    return int(match.group(1)) if match else None


def _compute_difficulty_score(ft_per_mi, notes=''):
    """Compute difficulty score 0-10 from ft/mi and note keywords."""
    if not ft_per_mi:
        return 0.0
    base = min(ft_per_mi / 10.0, 7.0)
    if notes:
        n = notes.lower()
        if 'headwind' in n:
            base += 1.5
        if 'steep' in n or 'steep climb' in n:
            base += 1.0
        if 'exposed' in n or 'gravel' in n:
            base += 0.5
        if 'tailwind' in n:
            base -= 0.5
    return round(min(max(base, 0), 10), 1)


# ── RWGPS API ──────────────────────────────────────────────────────────

def fetch_route(route_id):
    """Fetch full route data from RWGPS API.

    Returns dict with: name, distance (meters), elevation_gain (meters),
    track_points, course_points, and other route metadata.
    """
    api_key = current_app.config.get('RWGPS_API_KEY')
    auth_token = current_app.config.get('RWGPS_AUTH_TOKEN')

    if not api_key or not auth_token:
        raise Exception(
            "RWGPS API credentials not configured. "
            "Set RWGPS_API_KEY and RWGPS_AUTH_TOKEN in environment variables."
        )

    url = f'https://ridewithgps.com/api/v1/routes/{route_id}.json'
    headers = {
        'x-rwgps-api-key': api_key,
        'x-rwgps-auth-token': auth_token,
    }

    resp = http_requests.get(url, headers=headers, timeout=30)

    if resp.status_code == 404:
        raise Exception(f"RWGPS route {route_id} not found.")
    if resp.status_code == 401:
        raise Exception("RWGPS API authentication failed. Check your API key and auth token.")
    if resp.status_code == 429:
        raise Exception("RWGPS API rate limited. Please try again in a few minutes.")
    if not resp.ok:
        raise Exception(f"RWGPS API error (HTTP {resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    # The API may wrap route in a 'route' key or return it directly
    route = data.get('route', data) if isinstance(data, dict) else data
    return route


# ── Control extraction ─────────────────────────────────────────────────

def extract_controls(route_data):
    """Extract control/waypoint stops from RWGPS course_points.

    Returns list of dicts sorted by distance:
        [{'name': str, 'distance_m': float, 'elevation_m': float,
          'stop_type': str, 'rwgps_type': str}, ...]
    """
    course_points = route_data.get('course_points') or []

    # Filter to control-relevant types only
    controls = []
    for cp in course_points:
        cp_type = cp.get('t', '') or cp.get('type', '')
        if cp_type not in _CONTROL_TYPES:
            continue

        name = cp.get('n', '') or cp.get('name', '') or cp_type
        distance_m = cp.get('d', 0) or cp.get('distance', 0) or 0
        elevation_m = cp.get('e', 0) or cp.get('elevation', 0) or 0

        # Map RWGPS type to our stop_type
        stop_type = _RWGPS_TYPE_MAP.get(cp_type)
        if stop_type is None:
            stop_type = detect_stop_type(name)

        controls.append({
            'name': name.strip(),
            'distance_m': float(distance_m),
            'elevation_m': float(elevation_m),
            'stop_type': stop_type,
            'rwgps_type': cp_type,
        })

    # Sort by distance
    controls.sort(key=lambda c: c['distance_m'])

    if not controls:
        raise Exception(
            "This route has no waypoints/POIs. "
            "Please add control points as waypoints in RWGPS first."
        )

    # Ensure first stop is 'start'
    if controls[0]['stop_type'] != 'start':
        # Synthesize start from route data
        controls.insert(0, {
            'name': route_data.get('name', 'Start') + ' (Start)',
            'distance_m': 0.0,
            'elevation_m': 0.0,
            'stop_type': 'start',
            'rwgps_type': 'Start',
        })

    # Ensure last stop is 'finish'
    total_dist_m = route_data.get('distance', 0) or 0
    if controls[-1]['stop_type'] != 'finish':
        controls.append({
            'name': route_data.get('name', 'Finish') + ' (Finish)',
            'distance_m': float(total_dist_m),
            'elevation_m': 0.0,
            'stop_type': 'finish',
            'rwgps_type': 'End',
        })

    return controls


# ── Elevation computation from track points ────────────────────────────

def _compute_segment_elevation(track_points, start_dist_m, end_dist_m):
    """Sum positive elevation changes between two distances using track points.

    Returns elevation gain in feet.
    """
    if not track_points:
        return 0

    # Filter track points in the segment range
    segment_pts = []
    for tp in track_points:
        d = tp.get('d', 0) or tp.get('distance', 0) or 0
        e = tp.get('e', 0) or tp.get('elevation', 0) or 0
        if start_dist_m <= d <= end_dist_m and e is not None and e > 0:
            segment_pts.append(e)

    if len(segment_pts) < 2:
        return 0

    # Sum only positive changes (climbing)
    gain_m = 0.0
    for i in range(1, len(segment_pts)):
        diff = segment_pts[i] - segment_pts[i - 1]
        if diff > 0:
            gain_m += diff

    return int(round(gain_m * METERS_TO_FEET))


# ── Speed model ────────────────────────────────────────────────────────

def calculate_segment_speed(ft_per_mile):
    """Calculate average moving speed based on elevation gradient (ft/mile).

    Piecewise linear model fitted to reference points:
        30 ft/mi -> 13.5 mph
        40 ft/mi -> 12.0 mph  (baseline)
        60 ft/mi -> 11.0 mph
       100 ft/mi ->  9.0 mph

    Returns speed in mph, clamped to [7.0, 15.0].
    """
    if ft_per_mile is None or ft_per_mile < 0:
        return 12.0  # default baseline

    ftm = float(ft_per_mile)

    if ftm <= 30:
        # Flat to easy: 0→15.0, 30→13.5 mph (slope = -0.05/ft)
        speed = 15.0 - 0.05 * ftm
    elif ftm <= 40:
        # Steeper transition: 30→13.5, 40→12.0 (slope = -0.15/ft)
        speed = 13.5 - (ftm - 30) * 0.15
    else:
        # Gradual degradation: 40→12, 60→11, 100→9 (slope = -0.05/ft)
        speed = 12.0 - (ftm - 40) * 0.05

    return round(max(7.0, min(15.0, speed)), 1)


# ── Plan builder ───────────────────────────────────────────────────────

def build_ride_plan(route_data, controls):
    """Assemble a complete ride plan with stops from RWGPS route data.

    Returns:
        {'plan': {ride_plan fields}, 'stops': [{ride_plan_stop fields}, ...]}
    """
    track_points = route_data.get('track_points') or []
    route_name = route_data.get('name', 'Untitled Route')
    route_id = str(route_data.get('id', ''))
    total_dist_m = route_data.get('distance', 0) or 0
    total_dist_miles = round(total_dist_m * METERS_TO_MILES, 1)

    # Extract brevet distance from name, or estimate from total distance
    distance_km = _extract_distance_km(route_name)
    if not distance_km:
        # Round to nearest standard brevet distance
        dist_km_raw = total_dist_m / 1000
        for std in [200, 300, 400, 600, 1000, 1200]:
            if dist_km_raw <= std * 1.05:  # 5% tolerance
                distance_km = std
                break
        if not distance_km:
            distance_km = int(round(dist_km_raw))

    cutoff_hours = _get_cutoff_hours(distance_km)

    # Build stops
    stops = []
    cum_time_min = 0
    total_elevation_ft = 0
    total_moving_time = 0
    prev_dist_miles = 0.0

    for i, ctrl in enumerate(controls):
        dist_miles = round(ctrl['distance_m'] * METERS_TO_MILES, 1)

        # Segment metrics (vs previous stop)
        seg_dist = round(dist_miles - prev_dist_miles, 1)

        # Elevation gain for this segment from track points
        if i > 0:
            prev_dist_m = controls[i - 1]['distance_m']
            elev_gain = _compute_segment_elevation(track_points, prev_dist_m, ctrl['distance_m'])
        else:
            elev_gain = 0

        total_elevation_ft += elev_gain

        # Computed fields
        ft_per_mi = int(round(elev_gain / seg_dist)) if elev_gain and seg_dist > 0 else None
        avg_speed = calculate_segment_speed(ft_per_mi) if seg_dist > 0 else None
        segment_time_min = int(round((seg_dist / avg_speed) * 60)) if avg_speed and seg_dist > 0 else 0

        if segment_time_min > 0:
            cum_time_min += segment_time_min
            if seg_dist > 0:
                total_moving_time += segment_time_min

        # Bookend / time bank
        bookend_time_min = None
        time_bank_min = None
        if cutoff_hours and total_dist_miles > 0 and dist_miles > 0:
            fraction = dist_miles / total_dist_miles
            bookend_time_min = round(fraction * cutoff_hours * 60)
            time_bank_min = bookend_time_min - cum_time_min

        difficulty_score = _compute_difficulty_score(ft_per_mi)

        stops.append({
            'stop_order': i + 1,
            'location': ctrl['name'],
            'stop_type': ctrl['stop_type'],
            'distance_miles': dist_miles,
            'elevation_gain': elev_gain,
            'segment_time_min': segment_time_min,
            'notes': '',
            'seg_dist': seg_dist,
            'ft_per_mi': ft_per_mi,
            'avg_speed': avg_speed,
            'cum_time_min': cum_time_min,
            'bookend_time_min': bookend_time_min,
            'time_bank_min': time_bank_min,
            'difficulty_score': difficulty_score,
        })

        prev_dist_miles = dist_miles

    # Plan-level aggregates
    total_elapsed = cum_time_min
    avg_moving_speed = round(total_dist_miles / (total_moving_time / 60.0), 1) if total_moving_time > 0 else None
    avg_elapsed_speed = round(total_dist_miles / (total_elapsed / 60.0), 1) if total_elapsed > 0 else None
    overall_ft_per_mile = round(total_elevation_ft / total_dist_miles, 1) if total_dist_miles > 0 else 0

    plan = {
        'name': route_name,
        'slug': slugify(route_name),
        'total_distance_miles': total_dist_miles,
        'total_elevation_ft': total_elevation_ft,
        'rwgps_url': f'https://ridewithgps.com/routes/{route_id}' if route_id else None,
        'rwgps_route_id': route_id or None,
        'distance_km': distance_km,
        'cutoff_hours': cutoff_hours,
        'start_time': '07:00',
        'avg_moving_speed': avg_moving_speed,
        'avg_elapsed_speed': avg_elapsed_speed,
        'total_moving_time_min': total_moving_time,
        'total_elapsed_time_min': total_elapsed,
        'total_break_time_min': 0,  # no breaks in initial generation
        'overall_ft_per_mile': overall_ft_per_mile,
    }

    return {'plan': plan, 'stops': stops}
