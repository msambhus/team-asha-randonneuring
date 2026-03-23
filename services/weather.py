"""Weather service — route sampling, bearing math, headwind/tailwind, Open-Meteo API, caching."""
import math
import logging
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

ATTRIBUTION = "*Weather data: [Open-Meteo](https://open-meteo.com)*"

HEAVY_WIND_MAX_KMH = 30
HEAVY_WIND_AVG_HEADWIND_KMH = 15

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
