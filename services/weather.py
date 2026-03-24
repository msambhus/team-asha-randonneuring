"""Weather service — route sampling, bearing math, headwind/tailwind, Open-Meteo API, caching."""
import math
import logging
from datetime import datetime, timedelta, date

import requests

from models import get_ride_wind_data, save_ride_wind_data

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

    wind_from_deg is meteorological convention (direction wind blows FROM).
    """
    if wind_speed == 0:
        return 0
    # Wind travel direction = wind_from + 180
    wind_travel_deg = (wind_from_deg + 180) % 360
    # Cosine projection: positive when wind opposes rider, negative when assisting
    angle = math.radians(wind_travel_deg - rider_bearing_deg)
    return round(wind_speed * math.cos(angle), 1)


def crosswind_component(wind_speed, wind_from_deg, rider_bearing_deg):
    """Return crosswind component (positive=right crosswind, negative=left).

    wind_from_deg is meteorological convention (direction wind blows FROM).
    """
    if wind_speed == 0:
        return 0
    wind_travel_deg = (wind_from_deg + 180) % 360
    angle = math.radians(wind_travel_deg - rider_bearing_deg)
    return round(wind_speed * math.sin(angle), 1)


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
        target_m = stop['distance_miles'] * MILES_TO_METERS

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

def fetch_route_weather(sample_points):
    """Fetch weather forecasts for sample points via Open-Meteo batch API.

    Sends a single GET with comma-separated lat/lng arrays.
    Returns list of per-location forecast dicts.
    """
    if not sample_points:
        return []

    lats = ",".join(str(round(p['lat'], 4)) for p in sample_points)
    lngs = ",".join(str(round(p['lng'], 4)) for p in sample_points)

    params = {
        'latitude': lats,
        'longitude': lngs,
        'hourly': 'temperature_2m,wind_speed_10m,wind_direction_10m,precipitation_probability,weather_code',
        'timezone': 'auto',
    }

    resp = requests.get(OPEN_METEO_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    # Normalize: single-location returns dict, multi returns list
    if isinstance(data, dict):
        return [data]
    return data


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


def get_historical_stop_wind(stops, track_points, ride_date, ride_id=None):
    """Return per-stop wind data for a completed ride using archive or forecast past_days.

    stops: list of stop dicts with 'distance_miles' (and optionally 'arrival_time_min', 'stop_name')
    track_points: list of RWGPS track dicts (y=lat, x=lng, d=distance_m)
    ride_date: datetime.date — date the ride took place
    ride_id: optional int — used for DB check-before-fetch and save-after-fetch (STOR-02)

    Returns (wind_rows, data_source) tuple, or (None, None) on empty track or API error.

    DB-check-before-fetch (STOR-02): If ride_id is given and ride_wind_data rows exist,
    returns stored rows immediately without any API call.

    Save-after-fetch (STOR-02): If ride_id is given and fetch succeeds, persists rows to DB.
    """
    if not track_points:
        return None, None

    # STOR-02: DB check before API call
    if ride_id is not None:
        stored = get_ride_wind_data(ride_id)
        if stored:
            return stored, stored[0]['data_source']

    # Interpolate stop coordinates from RWGPS track points
    coords = get_stop_coordinates(stops, track_points)
    valid_coords = [c for c in coords if c is not None]
    if not valid_coords:
        return None, None

    # Fetch historical wind from archive or forecast past_days
    try:
        weather_data, data_source = fetch_historical_wind(valid_coords, ride_date)
    except Exception:
        logger.exception("get_historical_stop_wind: API error for ride_id=%s", ride_id)
        return None, None

    if not weather_data:
        return None, None

    # Build index mapping: original stop index -> valid_coords index
    valid_map = {}
    valid_idx = 0
    for orig_idx, c in enumerate(coords):
        if c is not None:
            valid_map[orig_idx] = valid_idx
            valid_idx += 1

    # Estimate ride start as 07:00 on ride_date
    start_dt = datetime(ride_date.year, ride_date.month, ride_date.day, 7, 0)

    wind_rows = []
    for i, coord in enumerate(coords):
        if coord is None:
            continue

        v_idx = valid_map.get(i)
        if v_idx is None or v_idx >= len(weather_data):
            continue

        forecast = weather_data[v_idx]
        hourly = forecast.get('hourly', {})

        # Arrival time: use explicit arrival_time_min if available, else estimate from distance
        arrival_time_min = stops[i].get('arrival_time_min')
        if arrival_time_min is not None:
            arrival_dt = start_dt + timedelta(minutes=arrival_time_min)
        else:
            dist_km = stops[i].get('distance_miles', 0) * 1.60934
            hours_to_arrive = dist_km / _AVG_SPEED_KMH if _AVG_SPEED_KMH > 0 else 0
            arrival_dt = start_dt + timedelta(hours=hours_to_arrive)

        hour_index = get_hour_index(hourly.get('time', []), arrival_dt)

        wind_speed = _safe_get(hourly, 'wind_speed_10m', hour_index, 0.0)
        wind_dir = _safe_get(hourly, 'wind_direction_10m', hour_index, 0)
        temperature = _safe_get(hourly, 'temperature_2m', hour_index, 0.0)

        # Bearing: current -> next stop; for last stop: previous -> current
        bearing = 0.0
        if i + 1 < len(coords) and coords[i + 1] is not None:
            bearing = calculate_bearing(
                coord['lat'], coord['lng'],
                coords[i + 1]['lat'], coords[i + 1]['lng'],
            )
        elif i > 0 and coords[i - 1] is not None:
            bearing = calculate_bearing(
                coords[i - 1]['lat'], coords[i - 1]['lng'],
                coord['lat'], coord['lng'],
            )

        hw = headwind_component(wind_speed, wind_dir, bearing)
        cw = crosswind_component(wind_speed, wind_dir, bearing)
        wind_type = classify_wind(hw, cw)

        wind_rows.append({
            'stop_order': i,
            'stop_name': stops[i].get('stop_name', f'Stop {i}'),
            'wind_speed_kmh': round(float(wind_speed), 1),
            'wind_direction_deg': int(wind_dir),
            'headwind_kmh': round(float(hw), 1),
            'crosswind_kmh': round(float(cw), 1),
            'wind_type': wind_type,
            'temperature_c': round(float(temperature), 1),
            'conditions': '',
            'data_source': data_source,
        })

    if not wind_rows:
        return None, None

    # STOR-02: persist after successful fetch
    if ride_id is not None:
        save_ride_wind_data(ride_id, wind_rows)

    return wind_rows, data_source


# ── Caching ──────────────────────────────────────────────────────────

def get_cached_route_weather(route_slug, start_hour_str, sample_points, cache=None):
    """Cache-first weather fetch with 1-hour TTL.

    cache: Flask-Caching cache object (passed explicitly for testability).
    """
    cache_key = f"weather:{route_slug}:{start_hour_str}"

    if cache is not None:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    data = fetch_route_weather(sample_points)

    if cache is not None:
        cache.set(cache_key, data, timeout=3600)

    return data


# ── Per-stop wind pipeline ───────────────────────────────────────────

def fetch_stop_wind(stops, track_points, plan_slug, start_time_str, cache=None):
    """Return per-stop wind data for display in the base ride plan table.

    stops: list of stop dicts with 'distance_miles' key (and optionally 'arrival_time_min')
    track_points: list of RWGPS track dicts (y=lat, x=lng, d=distance_m)
    plan_slug: str used as part of cache key prefix "wind:{plan_slug}:{start_hour}"
    start_time_str: "HH:MM" string for estimated ride start
    cache: Flask-Caching cache object (passed explicitly for testability)

    Returns list of dicts — same length as stops:
        {'wind_speed_kmh': float, 'wind_type': str, 'style': dict, 'label': str}
    None entries for stops whose coordinates could not be resolved.
    Returns None on empty track, all-None coordinates, or API error.
    """
    if not track_points:
        return None

    # Step 1: interpolate stop coordinates from RWGPS track points
    coords = get_stop_coordinates(stops, track_points)
    valid_coords = [c for c in coords if c is not None]
    if not valid_coords:
        return None

    # Step 2: build cache key — "wind:{plan_slug}:{YYYYMMDD}{HH}"
    hour_str = start_time_str[:2]
    date_str = datetime.now().strftime('%Y%m%d')
    cache_key = f"wind:{plan_slug}:{date_str}{hour_str}"

    if cache is not None:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    # Step 3: fetch forecast data for valid coordinates
    try:
        weather_data = fetch_route_weather(valid_coords)
    except Exception:
        logger.exception("fetch_stop_wind: weather API error for plan %s", plan_slug)
        return None

    if not weather_data:
        return None

    # Step 4: build index mapping — valid_index -> original stop index
    # valid_map[i] = index in coords where coords[index] is the i-th non-None entry
    valid_map = {}
    valid_idx = 0
    for orig_idx, c in enumerate(coords):
        if c is not None:
            valid_map[orig_idx] = valid_idx
            valid_idx += 1

    # Step 5: parse start time into a datetime for arrival estimation
    start_hour = int(start_time_str[:2])
    start_minute = int(start_time_str[3:5]) if len(start_time_str) >= 5 else 0
    today = datetime.now().date()
    start_dt = datetime(today.year, today.month, today.day, start_hour, start_minute)

    # Step 6: compute per-stop wind entry
    result = []
    for i, coord in enumerate(coords):
        if coord is None:
            result.append(None)
            continue

        # Map this stop's original index to its weather_data slice
        v_idx = valid_map.get(i)
        if v_idx is None or v_idx >= len(weather_data):
            result.append(None)
            continue

        forecast = weather_data[v_idx]
        hourly = forecast.get('hourly', {})

        # Use arrival_time_min if present; otherwise estimate from distance
        arrival_time_min = stops[i].get('arrival_time_min')
        if arrival_time_min is not None:
            arrival_dt = start_dt + timedelta(minutes=arrival_time_min)
        else:
            dist_km = stops[i].get('distance_miles', 0) * 1.60934
            hours_to_arrive = dist_km / _AVG_SPEED_KMH if _AVG_SPEED_KMH > 0 else 0
            arrival_dt = start_dt + timedelta(hours=hours_to_arrive)

        hour_index = get_hour_index(hourly.get('time', []), arrival_dt)

        wind_speed = _safe_get(hourly, 'wind_speed_10m', hour_index, 0.0)
        wind_dir = _safe_get(hourly, 'wind_direction_10m', hour_index, 0)
        temperature = _safe_get(hourly, 'temperature_2m', hour_index, 0.0)

        # Bearing: current stop -> next stop; for last stop: previous -> current
        bearing = 0.0
        if i + 1 < len(coords) and coords[i + 1] is not None:
            bearing = calculate_bearing(
                coord['lat'], coord['lng'],
                coords[i + 1]['lat'], coords[i + 1]['lng'],
            )
        elif i > 0 and coords[i - 1] is not None:
            bearing = calculate_bearing(
                coords[i - 1]['lat'], coords[i - 1]['lng'],
                coord['lat'], coord['lng'],
            )

        hw = headwind_component(wind_speed, wind_dir, bearing)
        cw = crosswind_component(wind_speed, wind_dir, bearing)
        wind_type = classify_wind(hw, cw)
        style = wind_cell_style(wind_speed, wind_type)

        temp_f = round(float(temperature) * 9 / 5 + 32, 0)
        wind_speed_mph = round(float(wind_speed) * 0.621371, 1)
        result.append({
            'wind_speed_kmh': round(float(wind_speed), 1),
            'wind_speed_mph': wind_speed_mph,
            'headwind_kmh': round(float(hw), 1),
            'wind_type': wind_type,
            'wind_direction_deg': int(wind_dir),
            'rider_bearing_deg': int(bearing),
            'style': style,
            'label': wind_label(hw),
            'temperature_c': round(float(temperature), 1),
            'temperature_f': int(temp_f),
        })

    # Step 7: cache and return
    if cache is not None:
        cache.set(cache_key, result, timeout=3600)

    return result


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
