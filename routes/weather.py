"""Weather routes: standalone weather + wind map page."""
import math
import logging
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, jsonify, current_app

from services.rwgps import fetch_route, extract_controls, extract_rwgps_route_id
from services.weather import (
    sample_track_points, calculate_bearing, headwind_component,
    get_cached_route_weather, format_weather_response,
    wind_label, wmo_to_text, get_hour_index, _safe_get,
)
from cache import cache

logger = logging.getLogger(__name__)

weather_bp = Blueprint('weather', __name__)

# Polyline decimation: keep every Nth track point for map rendering
_POLYLINE_DECIMATION = 20

# Sampling intervals
_TABLE_INTERVAL_M = 50000   # 50km between table rows
_MAP_INTERVAL_M = 10000     # 10km between map arrows

# Unit conversion
_KMH_TO_MPH = 0.621371
_AVG_SPEED_KMH = 22  # for arrival-time estimation


def _c_to_f(c):
    """Celsius to Fahrenheit."""
    return round(c * 9 / 5 + 32, 1)


def _kmh_to_mph(kmh):
    """km/h to mph."""
    return round(kmh * _KMH_TO_MPH, 1)


def _km_to_mi(km):
    """Kilometers to miles."""
    return round(km * 0.621371, 1)


def _build_weather_segments(sample_points, weather_data, bearings, start_dt, speed_mph=None):
    """Build weather segments with imperial units and lat/lng for map rendering.

    speed_mph: rider speed for arrival time estimation. Defaults to ~14 mph (22 km/h).
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

        dist_km = pt['distance_m'] / 1000
        hours_to_arrive = dist_km / speed_kmh if speed_kmh > 0 else 0
        arrival = start_dt + timedelta(hours=hours_to_arrive)
        idx = get_hour_index(times, arrival)

        temp_c = _safe_get(hourly, 'temperature_2m', idx, 0.0)
        wind_speed_kmh = _safe_get(hourly, 'wind_speed_10m', idx, 0.0)
        wind_dir = _safe_get(hourly, 'wind_direction_10m', idx, 0)
        precip = _safe_get(hourly, 'precipitation_probability', idx, 0)
        wmo_code = _safe_get(hourly, 'weather_code', idx, 0)

        bearing = bearings[i] if i < len(bearings) else (bearings[-1] if bearings else 0)
        hw_kmh = headwind_component(wind_speed_kmh, wind_dir, bearing)

        segments.append({
            'distance_mi': _km_to_mi(dist_km),
            'temperature_f': _c_to_f(temp_c),
            'wind_speed_mph': _kmh_to_mph(wind_speed_kmh),
            'wind_direction_deg': wind_dir,
            'headwind_mph': _kmh_to_mph(hw_kmh),
            'wind_label': wind_label(hw_kmh),
            'precip_percent': precip,
            'conditions': wmo_to_text(wmo_code),
            'lat': pt['lat'],
            'lng': pt['lng'],
            'rider_bearing_deg': bearing,
        })

    return segments


@weather_bp.route('/weather')
def weather_page():
    """Render the weather + wind map page with input form.

    Supports query params for pre-filling from brevet/ride plan pages:
      ?rwgps_url=...&start_datetime=...&speed_mph=...&auto=1
    """
    mapbox_token = current_app.config.get('MAPBOX_ACCESS_TOKEN', '')
    return render_template('weather.html',
                           mapbox_token=mapbox_token,
                           prefill_url=request.args.get('rwgps_url', ''),
                           prefill_datetime=request.args.get('start_datetime', ''),
                           prefill_speed=request.args.get('speed_mph', ''),
                           auto_fetch=request.args.get('auto', ''))


@weather_bp.route('/api/weather-map', methods=['POST'])
def weather_map_api():
    """JSON API: fetch route weather and return data for table + map rendering."""
    data = request.get_json(silent=True) or {}
    rwgps_url = (data.get('rwgps_url') or '').strip()
    start_datetime_str = data.get('start_datetime')
    speed_mph = data.get('speed_mph')

    # Validate URL
    if not rwgps_url:
        return jsonify({'error': 'Please provide a RideWithGPS URL.'}), 400

    route_id = extract_rwgps_route_id(rwgps_url)
    if not route_id:
        return jsonify({'error': 'Could not extract a route ID from that URL. Use a URL like ridewithgps.com/routes/12345.'}), 400

    # Parse start datetime early (default: tomorrow 7:00 AM local)
    if start_datetime_str:
        try:
            start_dt = datetime.fromisoformat(start_datetime_str)
        except (ValueError, TypeError):
            start_dt = _default_start_time()
    else:
        start_dt = _default_start_time()

    # Validate within 16-day forecast window before making API calls
    max_forecast = datetime.now() + timedelta(days=16)
    if start_dt > max_forecast:
        return jsonify({'error': 'Weather forecasts are only available up to 16 days ahead.'}), 400

    try:
        # Fetch route from RWGPS
        route_data = fetch_route(route_id)
    except Exception:
        logger.exception("Failed to fetch RWGPS route %s", route_id)
        return jsonify({'error': 'Could not fetch route data from RideWithGPS. The route may not exist or the service may be temporarily unavailable.'}), 502

    track_points = route_data.get('track_points', [])
    if not track_points:
        return jsonify({'error': 'This route has no GPS track data.'}), 400

    # Sample at two intervals: sparse for table, dense for map
    table_sample = sample_track_points(track_points, interval_m=_TABLE_INTERVAL_M)
    map_sample = sample_track_points(track_points, interval_m=_MAP_INTERVAL_M)

    if not table_sample:
        return jsonify({'error': 'Could not sample points from this route.'}), 400

    # Fetch weather for both sample sets (map is superset, fetch once)
    start_hour_str = start_dt.strftime("%Y-%m-%dT%H:00")
    slug = f"route-{route_id}"
    try:
        map_weather = get_cached_route_weather(
            f"{slug}-map", start_hour_str, map_sample, cache=cache)
        table_weather = get_cached_route_weather(
            f"{slug}-table", start_hour_str, table_sample, cache=cache)
    except Exception:
        return jsonify({'error': 'Weather data is temporarily unavailable. Please try again.'}), 503

    # Compute bearings for both sample sets
    def compute_bearings(points):
        bearings = []
        for i in range(len(points) - 1):
            bearings.append(calculate_bearing(
                points[i]['lat'], points[i]['lng'],
                points[i + 1]['lat'], points[i + 1]['lng'],
            ))
        return bearings

    table_bearings = compute_bearings(table_sample)
    map_bearings = compute_bearings(map_sample)

    # Parse speed (mph) — default ~14 mph if not provided
    try:
        rider_speed = float(speed_mph) if speed_mph else None
    except (ValueError, TypeError):
        rider_speed = None

    # Build segments in imperial units
    table_segments = _build_weather_segments(table_sample, table_weather, table_bearings, start_dt, rider_speed)
    map_segments = _build_weather_segments(map_sample, map_weather, map_bearings, start_dt, rider_speed)

    # Overall assessment from table segments
    headwinds = [s['headwind_mph'] for s in table_segments]
    avg_hw_mph = sum(headwinds) / len(headwinds) if headwinds else 0
    temps_f = [s['temperature_f'] for s in table_segments]

    # Decimate track points for map polyline
    polyline = []
    for i, pt in enumerate(track_points):
        if i % _POLYLINE_DECIMATION == 0:
            lat = pt.get('y')
            lng = pt.get('x')
            if lat is not None and lng is not None:
                polyline.append([lat, lng])
    last = track_points[-1]
    if last.get('y') is not None and last.get('x') is not None:
        polyline.append([last['y'], last['x']])

    # Extract cue points for reference
    try:
        controls = extract_controls(route_data)
        cue_points = [
            {
                'name': c['name'],
                'distance_mi': _km_to_mi(c['distance_m'] / 1000),
                'stop_type': c['stop_type'],
            }
            for c in controls
        ]
    except Exception:
        logger.warning("Could not extract controls for route %s", route_id)
        cue_points = []

    route_name = route_data.get('name', 'Unknown Route')
    total_dist_m = route_data.get('distance', 0) or 0

    return jsonify({
        'route_name': route_name,
        'total_distance_mi': _km_to_mi(total_dist_m / 1000),
        'polyline': polyline,
        'table_segments': table_segments,
        'map_segments': map_segments,
        'cue_points': cue_points,
        'overall_assessment': wind_label(avg_hw_mph / _KMH_TO_MPH) if headwinds else '',
        'temp_range': {
            'min_f': min(temps_f) if temps_f else 0,
            'max_f': max(temps_f) if temps_f else 0,
        },
        'attribution': '*Weather data: Open-Meteo*',
    })


def _default_start_time():
    """Default start: tomorrow at 7:00 AM."""
    tomorrow = datetime.now() + timedelta(days=1)
    return tomorrow.replace(hour=7, minute=0, second=0, microsecond=0)
