"""Weather routes: standalone weather + wind map page."""
import logging
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, jsonify, current_app

from services.rwgps import fetch_route, extract_controls, extract_rwgps_route_id
from services.weather import (
    sample_track_points, calculate_bearing, headwind_component,
    get_cached_route_weather, format_weather_response,
)
from cache import cache

logger = logging.getLogger(__name__)

weather_bp = Blueprint('weather', __name__)

# Polyline decimation: keep every Nth track point for map rendering
_POLYLINE_DECIMATION = 20


@weather_bp.route('/weather')
def weather_page():
    """Render the weather + wind map page with input form."""
    mapbox_token = current_app.config.get('MAPBOX_ACCESS_TOKEN', '')
    return render_template('weather.html', mapbox_token=mapbox_token)


@weather_bp.route('/api/weather-map', methods=['POST'])
def weather_map_api():
    """JSON API: fetch route weather and return data for table + map rendering."""
    data = request.get_json(silent=True) or {}
    rwgps_url = (data.get('rwgps_url') or '').strip()
    start_datetime_str = data.get('start_datetime')

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

    # Sample track points at 50km intervals for weather queries
    sample_points = sample_track_points(track_points, interval_m=50000)
    if not sample_points:
        return jsonify({'error': 'Could not sample points from this route.'}), 400

    # Fetch weather (cached)
    start_hour_str = start_dt.strftime("%Y-%m-%dT%H:00")
    slug = f"route-{route_id}"
    try:
        weather_data = get_cached_route_weather(slug, start_hour_str, sample_points, cache=cache)
    except Exception:
        return jsonify({'error': 'Weather data is temporarily unavailable. Please try again.'}), 503

    # Compute bearings between consecutive sample points
    bearings = []
    for i in range(len(sample_points) - 1):
        b = calculate_bearing(
            sample_points[i]['lat'], sample_points[i]['lng'],
            sample_points[i + 1]['lat'], sample_points[i + 1]['lng'],
        )
        bearings.append(b)

    # Format weather response (reuse existing logic)
    formatted = format_weather_response(sample_points, weather_data, bearings, start_dt)

    # Augment segments with lat/lng/bearing for map rendering
    segments = formatted.get('segments', [])
    for i, seg in enumerate(segments):
        if i < len(sample_points):
            seg['lat'] = sample_points[i]['lat']
            seg['lng'] = sample_points[i]['lng']
        seg['rider_bearing_deg'] = bearings[i] if i < len(bearings) else (bearings[-1] if bearings else 0)

    # Decimate track points for map polyline
    polyline = []
    for i, pt in enumerate(track_points):
        if i % _POLYLINE_DECIMATION == 0:
            lat = pt.get('y')
            lng = pt.get('x')
            if lat is not None and lng is not None:
                polyline.append([lat, lng])
    # Always include last point
    last = track_points[-1]
    if last.get('y') is not None and last.get('x') is not None:
        polyline.append([last['y'], last['x']])

    # Extract cue points for reference
    try:
        controls = extract_controls(route_data)
        cue_points = [
            {
                'name': c['name'],
                'distance_km': round(c['distance_m'] / 1000, 1),
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
        'total_distance_km': round(total_dist_m / 1000, 1),
        'polyline': polyline,
        'segments': segments,
        'cue_points': cue_points,
        'overall_assessment': formatted.get('overall_assessment', ''),
        'temp_range': formatted.get('temp_range', {}),
        'attribution': formatted.get('attribution', ''),
    })


def _default_start_time():
    """Default start: tomorrow at 7:00 AM."""
    tomorrow = datetime.now() + timedelta(days=1)
    return tomorrow.replace(hour=7, minute=0, second=0, microsecond=0)
