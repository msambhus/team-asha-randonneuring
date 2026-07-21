"""RideWithGPS engine — fetch routes, extract controls, build ride plans.

Club-agnostic and framework-agnostic: it imports only the stdlib + ``requests``
and touches no Flask application globals, so both apps in this monorepo can reuse
it. This is the SINGLE implementation — ``services/rwgps.py`` is a pure re-export
shim of this module (see ``tests/test_rwgps_shim.py``), so Team Asha and BrevetHub
share one engine that cannot drift.

Credentials for ``fetch_route`` may be passed in explicitly, or (when omitted) fall
back to ``RWGPS_API_KEY`` / ``RWGPS_AUTH_TOKEN`` in the environment — the exact env
vars both apps' configs already read. That keeps the module free of any
request-context global (the ``shared/`` isolation contract forbids them) while every
existing ``fetch_route(route_id)`` caller keeps working unchanged.

The plan math (``extract_controls`` → ``build_ride_plan``) is unchanged from the
proven implementation: distances are miles, speeds mph, elevation feet. Callers
that display in km/km-h convert at their own boundary.
"""
import os
import re
import requests as http_requests


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

def fetch_route(route_id, api_key=None, auth_token=None):
    """Fetch full route data from RWGPS API.

    ``api_key`` / ``auth_token`` may be passed in by the caller; when omitted they
    fall back to the ``RWGPS_API_KEY`` / ``RWGPS_AUTH_TOKEN`` environment variables
    (the same values both apps' configs read). Returns dict with: name, distance
    (meters), elevation_gain (meters), track_points, course_points, and other
    route metadata.
    """
    api_key = api_key or os.environ.get('RWGPS_API_KEY')
    auth_token = auth_token or os.environ.get('RWGPS_AUTH_TOKEN')

    if not api_key or not auth_token:
        missing = []
        if not api_key:
            missing.append('RWGPS_API_KEY')
        if not auth_token:
            missing.append('RWGPS_AUTH_TOKEN')
        raise Exception(
            f"RWGPS API credentials not configured — missing: {', '.join(missing)}. "
            "Go to ridewithgps.com → Account Settings → Developers tab → "
            "create an API client to get your api_key, then generate an auth token. "
            "Add both as environment variables."
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
        raise Exception(
            "RWGPS API authentication failed (401). "
            "Verify RWGPS_API_KEY and RWGPS_AUTH_TOKEN are correct in the environment. "
            "Generate a fresh auth token at ridewithgps.com → Account Settings → Developers tab."
        )
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


# ── Pacing profiles ────────────────────────────────────────────────────
#
# A pacing profile is a piecewise-linear speed-by-gradient curve the segment-speed
# function interpolates over, clamped to [floor, ceil]. Every number below is a
# named, owner-tweakable constant.
#
# ``default`` is the legacy formula (``calculate_segment_speed``) reproduced
# byte-for-byte — the parent-app callers (routes/admin.py, services/chat_tools.py)
# stay on it and their output never changes. ``conservative`` and ``aggressive`` are
# the two BrevetHub brevet-plan variants: a realistic "normal" pace and a fast pace
# 1.5 mph quicker across the board.
#
# Anchor points (ft/mi → mph):
#   conservative:  <=40 → 13.00,  100 → 10.25,  floor 8.5
#   aggressive:    <=40 → 14.50,  100 → 11.75,  floor 9.5, ceil 15.0

# Conservative curve.
CONSERVATIVE_FLAT_FTM = 40.0     # at/below this gradient the rider holds the flat speed
CONSERVATIVE_FLAT_MPH = 13.0     # flat-terrain cruising speed (also the ceiling)
CONSERVATIVE_STEEP_FTM = 100.0   # the steep anchor gradient
CONSERVATIVE_STEEP_MPH = 10.25   # speed at the steep anchor
CONSERVATIVE_FLOOR_MPH = 8.5     # slowest the model ever grades a pitch
CONSERVATIVE_CEIL_MPH = 13.0     # fastest (the flat speed — never faster than flat)

# Aggressive curve — conservative shifted up by a fixed offset, its own floor/ceil.
AGGRESSIVE_OFFSET_MPH = 1.5      # +mph across the board vs conservative
AGGRESSIVE_FLAT_FTM = 40.0
AGGRESSIVE_FLAT_MPH = CONSERVATIVE_FLAT_MPH + AGGRESSIVE_OFFSET_MPH    # 14.5
AGGRESSIVE_STEEP_FTM = 100.0
AGGRESSIVE_STEEP_MPH = CONSERVATIVE_STEEP_MPH + AGGRESSIVE_OFFSET_MPH  # 11.75
AGGRESSIVE_FLOOR_MPH = 9.5
AGGRESSIVE_CEIL_MPH = 15.0

# A profile spec is (flat_ftm, flat_mph, steep_ftm, steep_mph, floor_mph, ceil_mph).
# ``default`` is the sentinel None: use the legacy ``calculate_segment_speed`` verbatim.
PACING_PROFILES = {
    'default': None,
    'conservative': (CONSERVATIVE_FLAT_FTM, CONSERVATIVE_FLAT_MPH,
                     CONSERVATIVE_STEEP_FTM, CONSERVATIVE_STEEP_MPH,
                     CONSERVATIVE_FLOOR_MPH, CONSERVATIVE_CEIL_MPH),
    'aggressive': (AGGRESSIVE_FLAT_FTM, AGGRESSIVE_FLAT_MPH,
                   AGGRESSIVE_STEEP_FTM, AGGRESSIVE_STEEP_MPH,
                   AGGRESSIVE_FLOOR_MPH, AGGRESSIVE_CEIL_MPH),
}


def profile_segment_speed(ft_per_mile, profile='default'):
    """Average moving speed (mph) for a gradient under a named pacing ``profile``.

    ``default`` delegates to :func:`calculate_segment_speed` (byte-for-byte the legacy
    model, so parent-app plan output is unchanged). ``conservative`` / ``aggressive``
    interpolate their piecewise-linear curve from ``PACING_PROFILES`` and clamp to the
    profile's [floor, ceil]. An unknown/negative gradient grades at the flat speed for
    the profile curves, mirroring the legacy baseline fallback.
    """
    spec = PACING_PROFILES.get(profile)
    if spec is None:
        # 'default' (or any unknown key) → the proven legacy formula, unchanged.
        return calculate_segment_speed(ft_per_mile)

    flat_ftm, flat_mph, steep_ftm, steep_mph, floor_mph, ceil_mph = spec
    if ft_per_mile is None or ft_per_mile < 0:
        return round(min(ceil_mph, flat_mph), 1)

    ftm = float(ft_per_mile)
    if ftm <= flat_ftm:
        speed = flat_mph
    else:
        # Linear from the flat anchor through the steep anchor, extrapolated beyond.
        slope = (steep_mph - flat_mph) / (steep_ftm - flat_ftm)
        speed = flat_mph + slope * (ftm - flat_ftm)
    return round(max(floor_mph, min(ceil_mph, speed)), 1)


# ── Meal-break insertion ───────────────────────────────────────────────
#
# When ``build_ride_plan(..., insert_meals=True)`` runs, a post-pass drops a rest row
# roughly every ``MEAL_BREAK_INTERVAL_MI`` miles between controls (never at the start
# or finish). Each break is clock-typed from the rider's projected time of day so the
# label and dwell match when they actually stop. Every duration below is a named,
# owner-tweakable constant.

MEAL_BREAK_INTERVAL_MI = 60.0    # target spacing between meal breaks

# Dwell (minutes off the bike) per meal type.
BREAKFAST_DWELL_MIN = 20
LUNCH_DWELL_MIN = 30
SNACK_DWELL_MIN = 15
DINNER_DWELL_MIN = 30

# Clock windows (local hour of day, [start, end)) → (label, dwell). Chosen by the
# rider's projected clock time at the stop (start_time + elapsed).
MEAL_WINDOWS = (
    (4, 10, 'Breakfast + refill', BREAKFAST_DWELL_MIN),
    (10, 14, 'Lunch', LUNCH_DWELL_MIN),
    (14, 17, 'Afternoon snack', SNACK_DWELL_MIN),
    (17, 22, 'Dinner', DINNER_DWELL_MIN),
)
# Outside every window (late night / pre-dawn) — a light snack.
MEAL_DEFAULT = ('Night snack', SNACK_DWELL_MIN)


def _parse_start_minutes(start_time):
    """Minutes past midnight for an 'HH:MM' start time; 07:00 (420) on any bad input."""
    try:
        parts = str(start_time).split(':')
        return int(parts[0]) * 60 + int(parts[1])
    except (TypeError, ValueError, IndexError):
        return 7 * 60


def _meal_for_clock(minutes):
    """(label, dwell_min) for a meal break at ``minutes`` past the start-day midnight.

    Wraps modulo 24h so an overnight brevet maps its post-midnight hours back onto a
    normal clock (a 02:00 stop is a night snack, not a dinner).
    """
    hour = int((minutes % (24 * 60)) // 60)
    for start_h, end_h, label, dwell in MEAL_WINDOWS:
        if start_h <= hour < end_h:
            return label, dwell
    return MEAL_DEFAULT


def _insert_meal_breaks(stops, start_minutes):
    """Return ``(stops_with_breaks, total_break_min)`` for a meal-free control list.

    Walks the control stops (whose ``cum_time_min`` is the moving-elapsed time) and,
    each time cumulative distance passes the next ``MEAL_BREAK_INTERVAL_MI`` mark,
    drops a ``stop_type='meal'`` row after that control — unless the control is the
    start, the finish, or already a rest/food stop (which serves as the break itself).
    Dwell accrues into every following row's displayed ``cum_time_min`` and into the
    returned break total, but NEVER into moving time. Control rows keep their
    moving-elapsed math except ``cum_time_min`` (now break-inclusive) and the derived
    ``time_bank_min`` (recomputed against the fixed bookend).
    """
    result = []
    cum_dwell = 0
    total_break = 0
    next_threshold = MEAL_BREAK_INTERVAL_MI
    order = 0

    for s in stops:
        order += 1
        control = dict(s)
        control['stop_order'] = order
        control['cum_time_min'] = s['cum_time_min'] + cum_dwell
        if control.get('bookend_time_min') is not None:
            control['time_bank_min'] = control['bookend_time_min'] - control['cum_time_min']
        result.append(control)

        dist = s.get('distance_miles') or 0.0
        stop_type = s.get('stop_type')
        if stop_type in ('start', 'finish'):
            continue
        if dist < next_threshold:
            continue

        # This control passes a 60-mi mark: advance the threshold past it so we never
        # double up on the very next control...
        while next_threshold <= dist:
            next_threshold += MEAL_BREAK_INTERVAL_MI
        # ...but skip a redundant break when the rider is already at a rest/food stop.
        if stop_type == 'rest':
            continue

        label, dwell = _meal_for_clock(start_minutes + control['cum_time_min'])
        cum_dwell += dwell
        total_break += dwell
        order += 1
        result.append({
            'stop_order': order,
            'location': label,
            'stop_type': 'meal',
            'distance_miles': dist,
            'elevation_gain': 0,
            'segment_time_min': dwell,   # the dwell, NOT moving time (seg_dist is 0)
            'notes': label,
            'seg_dist': 0.0,
            'ft_per_mi': None,
            'avg_speed': None,
            'cum_time_min': control['cum_time_min'] + dwell,
            'bookend_time_min': None,
            'time_bank_min': None,
            'difficulty_score': 0.0,
        })

    return result, total_break


# ── Plan builder ───────────────────────────────────────────────────────

def build_ride_plan(route_data, controls, *, profile='default',
                    insert_meals=False, start_time=None):
    """Assemble a complete ride plan with stops from RWGPS route data.

    Keyword-only options (their DEFAULTS reproduce the legacy output byte-for-byte,
    so every existing caller is unaffected):
      - ``profile``: pacing profile name (``'default'`` / ``'conservative'`` /
        ``'aggressive'``) — see :data:`PACING_PROFILES`. ``'default'`` is the proven
        legacy speed model.
      - ``insert_meals``: when True, drop clock-typed meal-break rows every
        ~``MEAL_BREAK_INTERVAL_MI`` miles (see :func:`_insert_meal_breaks`); their
        dwell lands in ``total_break_time_min`` and elapsed time, never moving time.
      - ``start_time``: the brevet start clock (``'HH:MM'``) used to clock-type meal
        breaks; ``None`` keeps the legacy ``'07:00'`` default.

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

    # Use RWGPS corrected elevation_gain (smoothed, more accurate than raw
    # track point summation which over-counts due to GPS jitter)
    corrected_elev_m = route_data.get('elevation_gain', 0) or 0
    corrected_elev_ft = int(round(corrected_elev_m * METERS_TO_FEET))

    # Compute raw segment gains from track points for proportional distribution
    raw_segment_gains = []
    raw_total = 0
    for i, ctrl in enumerate(controls):
        if i > 0:
            prev_dist_m = controls[i - 1]['distance_m']
            raw_gain = _compute_segment_elevation(track_points, prev_dist_m, ctrl['distance_m'])
        else:
            raw_gain = 0
        raw_segment_gains.append(raw_gain)
        raw_total += raw_gain

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

        # Scale segment elevation to match RWGPS corrected total
        if raw_total > 0 and corrected_elev_ft > 0:
            elev_gain = int(round(raw_segment_gains[i] * corrected_elev_ft / raw_total))
        else:
            elev_gain = raw_segment_gains[i]

        total_elevation_ft += elev_gain

        # Computed fields
        ft_per_mi = int(round(elev_gain / seg_dist)) if elev_gain and seg_dist > 0 else None
        avg_speed = profile_segment_speed(ft_per_mi, profile) if seg_dist > 0 else None
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

    # Meal breaks (optional post-pass). Default OFF, so the stop list, elapsed time,
    # and break total below are all identical to the legacy output.
    plan_start = start_time or '07:00'
    total_break_time_min = 0
    if insert_meals:
        stops, total_break_time_min = _insert_meal_breaks(
            stops, _parse_start_minutes(plan_start))

    # Plan-level aggregates. Elapsed now folds in meal dwell (0 when no breaks), moving
    # time never does — so the default path stays byte-for-byte the legacy plan.
    total_elapsed = cum_time_min + total_break_time_min
    avg_moving_speed = round(total_dist_miles / (total_moving_time / 60.0), 1) if total_moving_time > 0 else None
    avg_elapsed_speed = round(total_dist_miles / (total_elapsed / 60.0), 1) if total_elapsed > 0 else None
    overall_ft_per_mile = round(total_elevation_ft / total_dist_miles, 1) if total_dist_miles > 0 else 0

    # Prefer RWGPS corrected elevation over summed segments (avoids rounding drift)
    final_elevation_ft = corrected_elev_ft if corrected_elev_ft > 0 else total_elevation_ft

    plan = {
        'name': route_name,
        'slug': slugify(route_name),
        'total_distance_miles': total_dist_miles,
        'total_elevation_ft': final_elevation_ft,
        'rwgps_url': f'https://ridewithgps.com/routes/{route_id}' if route_id else None,
        'rwgps_route_id': route_id or None,
        'distance_km': distance_km,
        'cutoff_hours': cutoff_hours,
        'start_time': plan_start,
        'avg_moving_speed': avg_moving_speed,
        'avg_elapsed_speed': avg_elapsed_speed,
        'total_moving_time_min': total_moving_time,
        'total_elapsed_time_min': total_elapsed,
        'total_break_time_min': total_break_time_min,
        'overall_ft_per_mile': overall_ft_per_mile,
    }

    return {'plan': plan, 'stops': stops}
