"""Shared weather engine — club-agnostic route sampling, bearing math,
headwind/tailwind, Open-Meteo fetch/parse, and point forecasts.

Extracted from Team Asha's ``services/weather.py`` so both the Team Asha web app
and BrevetHub can reuse the identical math and Open-Meteo primitives. This module
is standalone: it imports only stdlib + ``requests`` and nothing from the Team
Asha app (no ``models``/``routes``/``db``/``config``/``app``), so
``tests/brevethub/test_shared_isolation.py`` stays green. The three Team
Asha-only, model-coupled functions (``load_stored_route_weather``,
``get_historical_stop_wind``, ``fetch_stop_wind``) remain in ``services/weather.py``,
which re-exports everything here as a compatibility shim."""
import math
import logging
from datetime import datetime, timedelta, date

import requests


logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ARCHIVE_LAG_DAYS = 5

ATTRIBUTION = "*Weather data: [Open-Meteo](https://open-meteo.com)*"

HEAVY_WIND_MAX_KMH = 30
HEAVY_WIND_AVG_HEADWIND_KMH = 15

# Unit conversion: ride plan stops use miles, RWGPS track points use meters
MILES_TO_METERS = 1609.344

# WMO Weather interpretation codes (subset)
_WMO_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "dense drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "light showers",
    81: "showers",
    82: "heavy showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}

_WMO_ICONS = {
    0: "\u2600\ufe0f",       # clear sky — sunny
    1: "\U0001f324\ufe0f",   # mainly clear
    2: "\u26c5",             # partly cloudy
    3: "\u2601\ufe0f",       # overcast
    45: "\U0001f32b\ufe0f",  # fog
    48: "\U0001f32b\ufe0f",  # rime fog
    51: "\U0001f326\ufe0f",  # light drizzle
    53: "\U0001f326\ufe0f",  # drizzle
    55: "\U0001f327\ufe0f",  # dense drizzle
    61: "\U0001f326\ufe0f",  # light rain
    63: "\U0001f327\ufe0f",  # rain
    65: "\U0001f327\ufe0f",  # heavy rain
    71: "\U0001f328\ufe0f",  # light snow
    73: "\u2744\ufe0f",      # snow
    75: "\u2744\ufe0f",      # heavy snow
    80: "\U0001f326\ufe0f",  # light showers
    81: "\U0001f327\ufe0f",  # showers
    82: "\U0001f327\ufe0f",  # heavy showers
    95: "\u26c8\ufe0f",      # thunderstorm
    96: "\u26c8\ufe0f",      # thunderstorm with hail
    99: "\u26c8\ufe0f",      # thunderstorm with heavy hail
}

# Average brevet speed for arrival-time estimation (km/h)
_AVG_SPEED_KMH = 22


# ── Pure functions ───────────────────────────────────────────────────

def calculate_bearing(lat1, lng1, lat2, lng2):
    """Return forward bearing in degrees [0, 360) from point 1 to point 2."""
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    d_lng = math.radians(lng2 - lng1)
    x = math.sin(d_lng) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(d_lng)
    bearing = math.degrees(math.atan2(x, y))
    return bearing % 360


def headwind_component(wind_speed, wind_from_deg, rider_bearing_deg):
    """Return headwind component (positive=headwind, negative=tailwind).

    wind_from_deg: meteorological convention — direction wind blows FROM
                   (e.g. 0°=north wind blows southward, 90°=east wind blows westward).
    rider_bearing_deg: compass bearing the rider is traveling.

    Positive means wind is opposing the rider (headwind).
    Negative means wind is assisting the rider (tailwind).
    """
    if wind_speed == 0:
        return 0
    # Angle of the wind's source direction relative to the rider's heading.
    # cos > 0 when wind comes from ahead (headwind); < 0 when from behind (tailwind).
    angle = math.radians(wind_from_deg - rider_bearing_deg)
    return round(wind_speed * math.cos(angle), 1)


def crosswind_component(wind_speed, wind_from_deg, rider_bearing_deg):
    """Return crosswind component (positive=from rider's right, negative=from rider's left).

    wind_from_deg: meteorological convention — direction wind blows FROM.
    rider_bearing_deg: compass bearing the rider is traveling.
    """
    if wind_speed == 0:
        return 0
    angle = math.radians(wind_from_deg - rider_bearing_deg)
    return round(wind_speed * math.sin(angle), 1)


def wind_arrow_rotation(headwind_kmh, crosswind_kmh):
    """Return SVG rotation degrees (0–360) for a wind arrow pointing in the wind's travel direction.

    The arrow SVG default is pointing UP (↑).  The returned rotation orients it so it
    shows where the wind is *going* in the rider's frame:
      - Pure headwind  → 180° (↓, wind blowing into rider's face from front)
      - Pure tailwind  → 0°  (↑, wind blowing from behind toward front)
      - Right crosswind → 270° (←, wind from right travels left)
      - Left crosswind  → 90° (→, wind from left travels right)
    """
    angle_deg = math.degrees(math.atan2(float(crosswind_kmh), float(headwind_kmh)))
    return round((angle_deg + 180) % 360)


_WIND_ARROWS = ['↑', '↗', '→', '↘', '↓', '↙', '←', '↖']


def wind_arrow_glyph(rotation_deg):
    """8-way Unicode arrow for a wind-arrow rotation (see wind_arrow_rotation).

    Rotation 0° points up (↑, tailwind pushing the rider forward); 180° points
    down (↓, headwind into the face); 90°/270° are right/left crosswinds. Used
    where an SVG can't be rendered (plain-text live-tracking metric cells)."""
    return _WIND_ARROWS[round((rotation_deg % 360) / 45) % 8]


def classify_wind(headwind_kmh, crosswind_kmh):
    """Classify wind type using 45-degree threshold rule.

    Returns 'headwind', 'tailwind', or 'crosswind'.
    Uses strict > so equal magnitudes go to crosswind.
    """
    if abs(headwind_kmh) > abs(crosswind_kmh):
        return 'tailwind' if headwind_kmh < 0 else 'headwind'
    return 'crosswind'


_WIND_COLORS = {
    'headwind': (220, 38, 38),
    'tailwind': (22, 163, 74),
    'crosswind': (37, 99, 235),
}


def wind_cell_style(wind_speed_kmh, wind_type):
    """Return inline style dict for a wind table cell."""
    r, g, b = _WIND_COLORS.get(wind_type, (37, 99, 235))
    if wind_speed_kmh < 5:
        opacity = 0.15
        font_size = '0.75rem'
    elif wind_speed_kmh < 15:
        opacity = 0.35
        font_size = '0.875rem'
    else:
        opacity = 0.65
        font_size = '1.0rem'
    return {
        'color': f'#{r:02X}{g:02X}{b:02X}',
        'background': f'rgba({r},{g},{b},{opacity})',
        'font_size': font_size,
    }


def wind_label(headwind_kmh):
    """Human-readable wind assessment from headwind component value."""
    if headwind_kmh >= 15:
        return "strong headwind"
    elif headwind_kmh >= 5:
        return "headwind"
    elif headwind_kmh > -5:
        return "crosswind / light"
    elif headwind_kmh > -15:
        return "tailwind"
    else:
        return "strong tailwind"


def wmo_to_text(code):
    """Convert WMO weather code to human-readable text."""
    return _WMO_CODES.get(code, f"code {code}")


def wmo_to_icon(code):
    """Convert WMO weather code to emoji icon."""
    return _WMO_ICONS.get(code, "")


def get_hour_index(hourly_times, arrival_dt):
    """Select the forecast hour index closest to (but not after) arrival_dt.

    hourly_times: list of ISO strings like '2026-03-17T14:00'
    arrival_dt: datetime of estimated arrival
    """
    if not hourly_times:
        return 0
    target = arrival_dt.strftime("%Y-%m-%dT%H:00")
    for i, t in enumerate(hourly_times):
        if t >= target:
            return i
    return len(hourly_times) - 1


def _window_values(hourly, key, lo, hi):
    """Non-None values of an hourly series over the inclusive index window [lo, hi]."""
    series = hourly.get(key) or []
    if not series:
        return []
    lo = max(0, min(lo, hi))
    hi = min(max(lo, hi), len(series) - 1)
    return [v for v in series[lo:hi + 1] if v is not None]


def _window_max(hourly, key, lo, hi):
    """Peak value of an hourly series over the window, or None when absent."""
    vals = _window_values(hourly, key, lo, hi)
    return round(float(max(vals)), 1) if vals else None


def _window_min_max(hourly, key, lo, hi):
    """(min, max) of an hourly series over the window, or (None, None) when absent."""
    vals = _window_values(hourly, key, lo, hi)
    if not vals:
        return None, None
    return round(float(min(vals)), 1), round(float(max(vals)), 1)


# ── Track point sampling ────────────────────────────────────────────

def sample_track_points(track_points, interval_m=50000):
    """Sample track points at regular intervals for weather queries.

    Uses RWGPS field names: y=lat, x=lng, d=distance_m.
    Returns list of dicts with lat, lng, distance_m keys.
    """
    if not track_points:
        return []

    # Filter out points with None lat/lng
    valid = [p for p in track_points if p.get('y') is not None and p.get('x') is not None]
    if not valid:
        return []

    result = [_track_to_sample(valid[0])]
    next_dist = interval_m

    for pt in valid[1:]:
        if pt['d'] >= next_dist:
            result.append(_track_to_sample(pt))
            next_dist = pt['d'] + interval_m

    # Include final point if gap > 10% of interval
    last = valid[-1]
    if result[-1]['distance_m'] != last['d']:
        gap = last['d'] - result[-1]['distance_m']
        if gap > interval_m * 0.1:
            result.append(_track_to_sample(last))

    return result


def _track_to_sample(pt):
    return {'lat': pt['y'], 'lng': pt['x'], 'distance_m': pt['d']}


def get_stop_coordinates(stops, track_points):
    """Return lat/lng for each stop by interpolating RWGPS track points.

    stops: list of dicts with 'distance_miles' key (from ride_plan_stop)
    track_points: list of RWGPS track dicts with y=lat, x=lng, d=distance_meters

    Returns list of {'lat': float, 'lng': float} in same order as stops.
    Stops beyond the end of the track are clamped to the final track point.
    Stops at or before the start are clamped to the first track point.
    """
    if not track_points:
        return [None] * len(stops)

    # Filter to points with valid coordinates — same pattern as sample_track_points()
    valid = [tp for tp in track_points
             if tp.get('y') is not None and tp.get('x') is not None]
    if not valid:
        return [None] * len(stops)

    result = []
    for stop in stops:
        target_m = float(stop['distance_miles'] or 0) * MILES_TO_METERS

        # Clamp to first point if stop is at or before track start
        if target_m <= valid[0]['d']:
            result.append({'lat': valid[0]['y'], 'lng': valid[0]['x']})
            continue

        # Clamp to final point if stop is beyond track end
        if target_m >= valid[-1]['d']:
            result.append({'lat': valid[-1]['y'], 'lng': valid[-1]['x']})
            continue

        # Find bounding segment via linear scan and interpolate
        for i in range(1, len(valid)):
            if valid[i]['d'] >= target_m:
                prev, curr = valid[i - 1], valid[i]
                seg_len = curr['d'] - prev['d']
                if seg_len == 0:
                    # Zero-length segment: snap to current point to avoid divide-by-zero
                    result.append({'lat': curr['y'], 'lng': curr['x']})
                else:
                    t = (target_m - prev['d']) / seg_len
                    result.append({
                        'lat': prev['y'] + t * (curr['y'] - prev['y']),
                        'lng': prev['x'] + t * (curr['x'] - prev['x']),
                    })
                break

    return result


# ── Open-Meteo API ──────────────────────────────────────────────────

_BATCH_SIZE = 5   # Locations per Open-Meteo request (small to avoid 504s)
_MAX_RETRIES = 2  # Retry count per batch on transient errors


def _fetch_batch(batch_points, forecast_days=None):
    """Fetch weather for a small batch of points with retry on transient errors.

    forecast_days: optional int (1-16). When set, request that many days of hourly data
    so the arrays span a ride further out than Open-Meteo's 7-day default. The
    fetch-route-weather cron passes the horizon value; other callers omit it.
    """
    lats = ",".join(str(round(p['lat'], 4)) for p in batch_points)
    lngs = ",".join(str(round(p['lng'], 4)) for p in batch_points)

    params = {
        'latitude': lats,
        'longitude': lngs,
        'hourly': 'temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,wind_gusts_10m,wind_direction_10m,precipitation_probability,precipitation,cloud_cover,weather_code',
        'timezone': 'auto',
    }
    if forecast_days:
        params['forecast_days'] = forecast_days

    last_error = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            # Short timeout so a slow/hanging Open-Meteo fails fast — this fetch is
            # on the brevet-calendar critical path, and a long timeout × retries
            # used to stack past the serverless function limit (calendar wouldn't
            # load). See routes/riders.py wind-warning budget.
            resp = requests.get(OPEN_METEO_URL, params=params, timeout=6)
            if resp.status_code in (502, 503, 504) and attempt < _MAX_RETRIES:
                import time
                time.sleep(1)
                continue
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                return [data]
            return data
        except requests.exceptions.Timeout:
            # Don't retry a timeout: the API is slow/unreachable, so retrying just
            # multiplies the wall-clock. Fail immediately; callers degrade gracefully.
            raise
        except requests.exceptions.ConnectionError as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                import time
                time.sleep(1)
                continue
            raise

    raise last_error


def fetch_route_weather(sample_points, forecast_days=None):
    """Fetch weather forecasts for sample points via Open-Meteo API.

    Splits into small batches with retry to handle transient 504/connection errors.
    Returns list of per-location forecast dicts.

    forecast_days: optional int (1-16) passed through to each batch so the hourly arrays
    span a ride up to 16 days out. After TA-237 this live-fetch primitive is called only
    by the fetch-route-weather cron; request paths read from route_weather_cache via
    load_stored_route_weather instead.
    """
    if not sample_points:
        return []

    all_results = []
    for i in range(0, len(sample_points), _BATCH_SIZE):
        batch = sample_points[i:i + _BATCH_SIZE]
        all_results.extend(_fetch_batch(batch, forecast_days=forecast_days))

    return all_results


def _nearest_sample_index(sample_points, target_m):
    """Index of the stored sample point whose route distance is closest to target_m."""
    if not sample_points:
        return None
    best_idx, best_diff = None, None
    for i, sp in enumerate(sample_points):
        diff = abs(float(sp.get('distance_m') or 0) - target_m)
        if best_diff is None or diff < best_diff:
            best_idx, best_diff = i, diff
    return best_idx


def _sample_bearing(sample_points, idx):
    """Forward bearing at stored sample `idx` (idx→idx+1; for the last sample idx-1→idx)."""
    n = len(sample_points)
    if n < 2:
        return 0.0
    if idx + 1 < n:
        a, b = sample_points[idx], sample_points[idx + 1]
    else:
        a, b = sample_points[idx - 1], sample_points[idx]
    return calculate_bearing(a['lat'], a['lng'], b['lat'], b['lng'])


# ── Historical Wind (Archive API + forecast past_days fallback) ──────

def _fetch_archive_wind(stop_coords, ride_date):
    """Fetch wind data from Open-Meteo Archive API for a completed ride.

    stop_coords: list of dicts with 'lat' and 'lng' keys
    ride_date: datetime.date — the date of the completed ride

    Returns list of per-location hourly dicts (normalized from single-dict if needed).
    Raises requests.HTTPError on non-2xx responses.
    """
    lats = ",".join(str(round(c['lat'], 4)) for c in stop_coords)
    lngs = ",".join(str(round(c['lng'], 4)) for c in stop_coords)
    date_str = ride_date.strftime('%Y-%m-%d')

    params = {
        'latitude': lats,
        'longitude': lngs,
        'start_date': date_str,
        'end_date': date_str,
        'hourly': 'wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m',
        'timezone': 'auto',
    }

    resp = requests.get(OPEN_METEO_ARCHIVE_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return [data] if isinstance(data, dict) else data


def _fetch_forecast_past_days_wind(stop_coords, days_ago):
    """Fetch recent-past wind data via forecast API past_days parameter.

    stop_coords: list of dicts with 'lat' and 'lng' keys
    days_ago: int — how many days ago the ride occurred

    Returns list of per-location hourly dicts (normalized from single-dict if needed).
    Raises requests.HTTPError on non-2xx responses.
    """
    lats = ",".join(str(round(c['lat'], 4)) for c in stop_coords)
    lngs = ",".join(str(round(c['lng'], 4)) for c in stop_coords)

    params = {
        'latitude': lats,
        'longitude': lngs,
        'past_days': max(days_ago + 1, 1),
        'hourly': 'wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m',
        'timezone': 'auto',
    }

    resp = requests.get(OPEN_METEO_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return [data] if isinstance(data, dict) else data


def fetch_historical_wind(stop_coords, ride_date):
    """Route a historical wind fetch to archive API or forecast past_days fallback.

    stop_coords: list of dicts with 'lat' and 'lng' keys
    ride_date: datetime.date — the date the ride took place

    Returns (weather_data_list, data_source) where data_source is
    'archive' or 'forecast_past_days'.

    The archive API (ERA5 reanalysis) is available with a ~5-day lag.
    Rides with ride_date <= today - ARCHIVE_LAG_DAYS use the archive;
    more recent rides fall back to the forecast API past_days parameter.
    """
    lag_cutoff = date.today() - timedelta(days=ARCHIVE_LAG_DAYS)
    if ride_date <= lag_cutoff:
        return _fetch_archive_wind(stop_coords, ride_date), 'archive'
    days_ago = (date.today() - ride_date).days
    return _fetch_forecast_past_days_wind(stop_coords, days_ago), 'forecast_past_days'


# ── Segment + chart builders (shared by the weather page AND the live map) ──
# These were extracted from routes/weather.py so the live route can build its
# route-ahead charts from the EXACT same time-aware pipeline (sample → forecast →
# arrival-hour selection) as the weather page — the two surfaces can never diverge.

_KMH_TO_MPH = 0.621371
_MI_TO_M = 1609.344
_M_TO_FT = 3.28084


def _c_to_f(c):
    """Celsius to Fahrenheit."""
    return round(c * 9 / 5 + 32, 1)


def _kmh_to_mph(kmh):
    """km/h to mph."""
    return round(kmh * _KMH_TO_MPH, 1)


def _km_to_mi(km):
    """Kilometers to miles."""
    return round(km * 0.621371, 1)


def _interpolate_elevation(track_points, distance_m):
    """Interpolate elevation (ft) at a given distance along the route."""
    if not track_points:
        return 0
    prev = track_points[0]
    for pt in track_points[1:]:
        d = pt.get('d', 0) or 0
        if d >= distance_m:
            prev_d = prev.get('d', 0) or 0
            prev_e = prev.get('e', 0) or 0
            cur_e = pt.get('e', 0) or 0
            seg_len = d - prev_d
            if seg_len > 0:
                frac = (distance_m - prev_d) / seg_len
                elev_m = prev_e + frac * (cur_e - prev_e)
            else:
                elev_m = cur_e
            return round(elev_m * _M_TO_FT)
        prev = pt
    last_e = track_points[-1].get('e', 0) or 0
    return round(last_e * _M_TO_FT)


def build_arrival_interpolator(plan_stops, start_dt):
    """Build a function that returns arrival datetime for a given distance_m.

    Uses the plan's cumulative stop times (segment speeds + break durations)
    to interpolate arrival time at any point along the route, rather than
    assuming a flat speed.

    plan_stops: list of dicts with 'distance_miles' and 'cum_time_min'
    start_dt: datetime of ride start
    """
    # Build sorted list of (distance_m, cum_time_min) pairs
    points = []
    for s in plan_stops:
        dist_mi = float(s.get('distance_miles') or 0)
        cum_min = float(s.get('cum_time_min') or 0)
        points.append((dist_mi * _MI_TO_M, cum_min))

    if not points:
        return None

    # Sort by distance (should already be sorted, but be safe)
    points.sort(key=lambda p: p[0])

    def interpolate(distance_m):
        """Return estimated arrival datetime at this distance."""
        # Before first stop
        if distance_m <= points[0][0]:
            return start_dt + timedelta(minutes=points[0][1])

        # After last stop — extrapolate using last segment speed
        if distance_m >= points[-1][0]:
            return start_dt + timedelta(minutes=points[-1][1])

        # Interpolate between stops
        for j in range(1, len(points)):
            if points[j][0] >= distance_m:
                d0, t0 = points[j - 1]
                d1, t1 = points[j]
                seg_len = d1 - d0
                if seg_len > 0:
                    frac = (distance_m - d0) / seg_len
                    cum_min = t0 + frac * (t1 - t0)
                else:
                    cum_min = t1
                return start_dt + timedelta(minutes=cum_min)

        return start_dt + timedelta(minutes=points[-1][1])

    return interpolate


def build_weather_segments(sample_points, weather_data, bearings, start_dt,
                           speed_mph=None, track_points=None, arrival_fn=None):
    """Build weather segments with imperial units, chart-ready fields, and elevation.

    arrival_fn: function(distance_m) -> datetime. If provided, uses plan-aware
                arrival times instead of flat speed. Falls back to flat speed if None.
    track_points: RWGPS track points for elevation interpolation (optional).
    """
    speed_kmh = (speed_mph / _KMH_TO_MPH) if speed_mph else _AVG_SPEED_KMH
    segments = []
    for i in range(len(weather_data)):
        if i >= len(sample_points):
            break

        pt = sample_points[i]
        forecast = weather_data[i]
        hourly = forecast.get('hourly', {})
        times = hourly.get('time', [])

        # Use plan-aware arrival time if available, else flat speed
        if arrival_fn:
            arrival = arrival_fn(pt['distance_m'])
        else:
            dist_km = pt['distance_m'] / 1000
            hours_to_arrive = dist_km / speed_kmh if speed_kmh > 0 else 0
            arrival = start_dt + timedelta(hours=hours_to_arrive)

        idx = get_hour_index(times, arrival)

        temp_c = _safe_get(hourly, 'temperature_2m', idx, 0.0)
        feels_c = _safe_get(hourly, 'apparent_temperature', idx, temp_c)
        wind_speed_kmh = _safe_get(hourly, 'wind_speed_10m', idx, 0.0)
        wind_gust_kmh = _safe_get(hourly, 'wind_gusts_10m', idx, 0.0)
        wind_dir = _safe_get(hourly, 'wind_direction_10m', idx, 0)
        precip = _safe_get(hourly, 'precipitation_probability', idx, 0)
        precip_mm = _safe_get(hourly, 'precipitation', idx, 0.0)
        cloud = _safe_get(hourly, 'cloud_cover', idx, 0)
        humidity = _safe_get(hourly, 'relative_humidity_2m', idx, 0)
        wmo_code = _safe_get(hourly, 'weather_code', idx, 0)

        bearing = bearings[i] if i < len(bearings) else (bearings[-1] if bearings else 0)
        hw_kmh = headwind_component(wind_speed_kmh, wind_dir, bearing)

        elev_ft = _interpolate_elevation(track_points, pt['distance_m']) if track_points else 0

        segments.append({
            'distance_mi': _km_to_mi(pt['distance_m'] / 1000),
            'arrival_time': arrival.strftime('%-I:%M %p'),
            'temperature_f': _c_to_f(temp_c),
            'feels_like_f': _c_to_f(feels_c),
            'wind_speed_mph': _kmh_to_mph(wind_speed_kmh),
            'wind_gust_mph': _kmh_to_mph(wind_gust_kmh),
            'wind_direction_deg': wind_dir,
            'headwind_mph': _kmh_to_mph(hw_kmh),
            'wind_label': wind_label(hw_kmh),
            'precip_percent': precip,
            'precipitation_mm': round(precip_mm, 1),
            'cloud_cover': cloud,
            'humidity': humidity,
            'conditions': wmo_to_text(wmo_code),
            'conditions_icon': wmo_to_icon(wmo_code),
            'elevation_ft': elev_ft,
            'lat': pt['lat'],
            'lng': pt['lng'],
            'rider_bearing_deg': bearing,
        })

    return segments


def build_chart_data(segments):
    """Extract arrays from segments for Chart.js rendering."""
    return {
        'labels': [s['distance_mi'] for s in segments],
        'times': [s['arrival_time'] for s in segments],
        'temperature_f': [s['temperature_f'] for s in segments],
        'feels_like_f': [s['feels_like_f'] for s in segments],
        'wind_speed_mph': [s['wind_speed_mph'] for s in segments],
        'wind_gust_mph': [s['wind_gust_mph'] for s in segments],
        'headwind_mph': [s['headwind_mph'] for s in segments],
        'precip_probability': [s['precip_percent'] for s in segments],
        'precipitation_mm': [s['precipitation_mm'] for s in segments],
        'cloud_cover': [s['cloud_cover'] for s in segments],
        'elevation_ft': [s['elevation_ft'] for s in segments],
        'humidity': [s['humidity'] for s in segments],
    }


# ── Per-stop wind pipeline ───────────────────────────────────────────

def detect_heavy_wind(stop_wind):
    """Evaluate per-stop wind data and return a warning dict if conditions are heavy.

    stop_wind: list of stop wind dicts (from fetch_stop_wind), may include None entries.

    Returns a dict with max_wind_kmh, avg_headwind_kmh, is_heavy=True, and description
    if max wind exceeds HEAVY_WIND_MAX_KMH (30) or avg headwind exceeds
    HEAVY_WIND_AVG_HEADWIND_KMH (15). Returns None otherwise.
    Uses strict > comparisons (not >=) — consistent with classify_wind convention.
    """
    if not stop_wind:
        return None

    valid = [s for s in stop_wind if s is not None]
    if not valid:
        return None

    max_wind = max(s['wind_speed_kmh'] for s in valid)
    avg_headwind = sum(s['headwind_kmh'] for s in valid) / len(valid)

    if max_wind > HEAVY_WIND_MAX_KMH or avg_headwind > HEAVY_WIND_AVG_HEADWIND_KMH:
        avg_hw_rounded = round(avg_headwind, 1)
        max_wind_rounded = round(max_wind, 1)
        return {
            'max_wind_kmh': max_wind_rounded,
            'avg_headwind_kmh': avg_hw_rounded,
            'is_heavy': True,
            'description': (
                f"Strong headwinds expected -- avg {avg_hw_rounded} km/h headwind, "
                f"gusts to {max_wind_rounded} km/h"
            ),
        }

    return None


# ── Response formatting ─────────────────────────────────────────────

def format_weather_response(sample_points, weather_data, bearings, start_dt):
    """Assemble segment-by-segment weather summary.

    sample_points: list of {lat, lng, distance_m}
    weather_data: list of Open-Meteo forecast dicts (one per sample point)
    bearings: list of forward bearings between consecutive sample points
    start_dt: datetime of ride start (for arrival-time estimation)
    """
    segments = []
    temps = []

    for i in range(len(weather_data)):
        if i >= len(sample_points):
            break

        pt = sample_points[i]
        forecast = weather_data[i]
        hourly = forecast.get('hourly', {})
        times = hourly.get('time', [])

        # Estimate arrival time at this point
        dist_km = pt['distance_m'] / 1000
        hours_to_arrive = dist_km / _AVG_SPEED_KMH if _AVG_SPEED_KMH > 0 else 0
        arrival = start_dt + timedelta(hours=hours_to_arrive)
        idx = get_hour_index(times, arrival)

        temp = _safe_get(hourly, 'temperature_2m', idx, 0.0)
        wind_speed = _safe_get(hourly, 'wind_speed_10m', idx, 0.0)
        wind_dir = _safe_get(hourly, 'wind_direction_10m', idx, 0)
        precip = _safe_get(hourly, 'precipitation_probability', idx, 0)
        wmo_code = _safe_get(hourly, 'weather_code', idx, 0)

        # Bearing for this segment (use previous segment's if we're at last point)
        bearing = bearings[i] if i < len(bearings) else (bearings[-1] if bearings else 0)
        hw = headwind_component(wind_speed, wind_dir, bearing)

        temps.append(temp)
        segments.append({
            'distance_km': round(dist_km, 1),
            'temperature_c': round(temp, 1),
            'wind_speed_kmh': round(wind_speed, 1),
            'wind_direction_deg': wind_dir,
            'headwind_kmh': hw,
            'wind_label': wind_label(hw),
            'precip_percent': precip,
            'conditions': wmo_to_text(wmo_code),
        })

    # Overall assessment
    headwinds = [s['headwind_kmh'] for s in segments]
    avg_hw = sum(headwinds) / len(headwinds) if headwinds else 0
    precip_risks = [s for s in segments if s['precip_percent'] > 30]

    return {
        'segments': segments,
        'overall_assessment': wind_label(avg_hw),
        'temp_range': {
            'min_c': round(min(temps), 1) if temps else 0,
            'max_c': round(max(temps), 1) if temps else 0,
        },
        'precip_risk_segments': len(precip_risks),
        'attribution': ATTRIBUTION,
    }


def _safe_get(hourly_dict, key, index, default=0):
    """Safely get a value from hourly forecast arrays."""
    values = hourly_dict.get(key, [])
    if not values or index >= len(values):
        return default
    return values[index]


# ── Scope-A point forecast (start-point weather for a brevet date) ──────────
# BrevetHub's first weather slice is a keyless, single-point forecast for an
# upcoming brevet: no RWGPS route geometry, no per-segment wind — just the
# temperature / precipitation / wind at the event's approximate start coordinate
# on its date. These helpers are pure + Open-Meteo-only, so BrevetHub can warm a
# cache off the request path and render from it. The route/segment primitives
# above are preserved so along-route wind (scope B) can layer on later.

FORECAST_HORIZON_DAYS = 16   # Open-Meteo forecast horizon; beyond this, no forecast

# Approximate geographic centers (lat, lng) for US states + DC. RUSA region
# labels are ``"<STATE>: <city/region>"`` (e.g. "CA: San Francisco"); scope A
# resolves only the two-letter STATE prefix to a state-level centroid — an honest
# approximation, NOT the exact brevet start. The city half is deliberately not
# geocoded (no geocoding dependency in scope A). Unknown/blank → no coordinate →
# no forecast (the calendar shows the honest "not available" state).
_US_STATE_CENTROIDS = {
    'AL': (32.8, -86.8), 'AK': (64.2, -152.3), 'AZ': (34.3, -111.7),
    'AR': (34.9, -92.4), 'CA': (37.2, -119.4), 'CO': (39.0, -105.5),
    'CT': (41.6, -72.7), 'DE': (39.0, -75.5), 'DC': (38.9, -77.0),
    'FL': (28.6, -82.4), 'GA': (32.6, -83.4), 'HI': (20.3, -156.4),
    'ID': (44.4, -114.6), 'IL': (40.1, -89.2), 'IN': (39.9, -86.3),
    'IA': (42.1, -93.5), 'KS': (38.5, -98.4), 'KY': (37.5, -85.3),
    'LA': (31.0, -92.0), 'ME': (45.4, -69.2), 'MD': (39.0, -76.8),
    'MA': (42.3, -71.8), 'MI': (44.3, -85.4), 'MN': (46.3, -94.3),
    'MS': (32.7, -89.7), 'MO': (38.4, -92.5), 'MT': (47.0, -109.6),
    'NE': (41.5, -99.8), 'NV': (39.3, -116.6), 'NH': (43.7, -71.6),
    'NJ': (40.2, -74.7), 'NM': (34.4, -106.1), 'NY': (42.9, -75.6),
    'NC': (35.6, -79.4), 'ND': (47.5, -100.5), 'OH': (40.3, -82.8),
    'OK': (35.6, -97.5), 'OR': (44.0, -120.6), 'PA': (40.9, -77.8),
    'RI': (41.7, -71.6), 'SC': (33.9, -80.9), 'SD': (44.4, -100.2),
    'TN': (35.9, -86.4), 'TX': (31.5, -99.3), 'UT': (39.3, -111.7),
    'VT': (44.1, -72.7), 'VA': (37.5, -78.9), 'WA': (47.4, -120.5),
    'WV': (38.6, -80.6), 'WI': (44.6, -89.9), 'WY': (43.0, -107.6),
}

_COMPASS_16 = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
               'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']


def resolve_region_coordinates(region_label):
    """Approximate (lat, lng) for a RUSA region label, or None when unresolvable.

    ``region_label`` is the raw RUSA label like ``"CA: San Francisco"``. Scope A
    resolves the leading two-letter US state code to that state's approximate
    geographic center — an honest, documented approximation (not the exact start).
    Returns None for a blank label or an unknown/foreign region so the caller
    skips it rather than fabricating a location.
    """
    if not region_label:
        return None
    head = str(region_label).strip()[:2].upper()
    return _US_STATE_CENTROIDS.get(head)


def compass_label(wind_from_deg):
    """16-point compass label (e.g. 'NW') for a meteorological wind-FROM direction."""
    idx = int((float(wind_from_deg) % 360) / 22.5 + 0.5) % 16
    return _COMPASS_16[idx]


def wind_travel_rotation(wind_from_deg):
    """SVG rotation (0–360) for an up-pointing arrow to show where the wind is GOING.

    Open-Meteo reports wind direction meteorologically (the direction it blows
    FROM). An up-pointing (north) arrow is rotated by ``wind_from_deg + 180`` so it
    points the way the wind travels: a north wind (from 0°) travels south → 180°;
    an east wind (from 90°) travels west → 270°."""
    return round((float(wind_from_deg) + 180) % 360)


def fetch_point_forecast(lat, lng, forecast_date):
    """Fetch a single-point Open-Meteo daily forecast for one date. No API key.

    Returns the raw Open-Meteo JSON dict (stored verbatim in the BrevetHub weather
    cache and summarized at read time by :func:`summarize_point_forecast`), or None
    when the date is outside the ~16-day forecast horizon (Open-Meteo has no data,
    so there is nothing honest to store). Raises ``requests`` errors on a transient
    fetch failure so the caller can fail soft per event and keep last-good cache.

    Uses ``start_date=end_date=forecast_date`` to pin exactly the brevet's day and
    ``daily`` aggregates (min/max temp, precip sum + probability, max wind + dominant
    direction, WMO code) — the compact set a calendar badge needs.
    """
    from datetime import date as _date
    today = _date.today()
    fd = forecast_date
    if isinstance(fd, str):
        fd = datetime.strptime(fd[:10], '%Y-%m-%d').date()
    if fd < today or (fd - today).days > FORECAST_HORIZON_DAYS:
        return None

    date_str = fd.strftime('%Y-%m-%d')
    params = {
        'latitude': round(float(lat), 4),
        'longitude': round(float(lng), 4),
        'daily': ('temperature_2m_max,temperature_2m_min,precipitation_sum,'
                  'precipitation_probability_max,wind_speed_10m_max,'
                  'wind_direction_10m_dominant,weather_code'),
        'start_date': date_str,
        'end_date': date_str,
        'timezone': 'auto',
    }
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=6)
    resp.raise_for_status()
    return resp.json()


def summarize_point_forecast(weather_data):
    """Turn a stored point-forecast JSON dict into a compact display payload, or None.

    Pure: no network. Reads the first day of the ``daily`` arrays and returns a dict
    of preformatted metric/imperial values a neutral calendar badge renders directly
    (so the template needs no unit math and no Team Asha Jinja filters). Returns None
    when the payload is missing/empty so the caller shows the honest "no forecast"
    state instead of a half-empty badge.
    """
    if not weather_data or not isinstance(weather_data, dict):
        return None
    daily = weather_data.get('daily') or {}
    codes = daily.get('weather_code') or []
    if not codes:
        return None

    def _first(key):
        arr = daily.get(key) or []
        return arr[0] if arr else None

    code = codes[0]
    tmax_c = _first('temperature_2m_max')
    tmin_c = _first('temperature_2m_min')
    precip_mm = _first('precipitation_sum')
    precip_prob = _first('precipitation_probability_max')
    wind_kmh = _first('wind_speed_10m_max')
    wind_dir = _first('wind_direction_10m_dominant')

    summary = {
        'weather_code': code,
        'condition': wmo_to_text(code),
        'condition_icon': wmo_to_icon(code),
        'temp_min_c': round(float(tmin_c), 1) if tmin_c is not None else None,
        'temp_max_c': round(float(tmax_c), 1) if tmax_c is not None else None,
        'temp_min_f': _c_to_f(tmin_c) if tmin_c is not None else None,
        'temp_max_f': _c_to_f(tmax_c) if tmax_c is not None else None,
        'precip_mm': round(float(precip_mm), 1) if precip_mm is not None else None,
        'precip_prob': int(precip_prob) if precip_prob is not None else None,
        'wind_speed_kmh': round(float(wind_kmh), 1) if wind_kmh is not None else None,
        'wind_speed_mph': _kmh_to_mph(wind_kmh) if wind_kmh is not None else None,
        'wind_dir_deg': int(wind_dir) if wind_dir is not None else None,
        'wind_dir_label': compass_label(wind_dir) if wind_dir is not None else None,
        'wind_travel_deg': wind_travel_rotation(wind_dir) if wind_dir is not None else None,
    }
    return summary
