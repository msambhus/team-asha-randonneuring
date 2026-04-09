"""Weather routes: standalone weather + wind map page."""
import math
import time
import logging
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, jsonify, current_app, session

from services.rwgps import fetch_route, extract_controls, extract_rwgps_route_id
from services.weather import (
    sample_track_points, calculate_bearing, headwind_component,
    get_cached_route_weather, format_weather_response,
    wind_label, wmo_to_text, wmo_to_icon, get_hour_index, _safe_get,
)
from cache import cache

# Cache TTL for RWGPS route data (route geometry doesn't change often)
_ROUTE_CACHE_TTL = 3600  # 1 hour

logger = logging.getLogger(__name__)

weather_bp = Blueprint('weather', __name__)

# Polyline decimation: keep every Nth track point for map rendering
_POLYLINE_DECIMATION = 20

# Sampling intervals
_TABLE_INTERVAL_M = 50000   # 50km between table rows
_MAP_INTERVAL_M = 15000     # 15km between map arrows

# Unit conversion
_KMH_TO_MPH = 0.621371
_MI_TO_M = 1609.344
_M_TO_FT = 3.28084
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


def _build_arrival_interpolator(plan_stops, start_dt):
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


def _load_plan_stops(plan_slug, rider_id=None):
    """Load plan stops for arrival time interpolation.

    If rider_id is provided and they have a custom plan, use that instead.
    Returns (stops_list, plan_name) or (None, None) if not found.
    """
    from models import get_ride_plan_by_slug, get_ride_plan_stops, get_custom_plan

    plan = get_ride_plan_by_slug(plan_slug)
    if not plan:
        return None, None

    # Check for custom plan
    if rider_id:
        custom = get_custom_plan(rider_id, plan['id'])
        if custom:
            from services.custom_plan_service import get_merged_plan_stops, recalculate_cumulative_values
            custom_stops_raw, _ = get_merged_plan_stops(custom['id'])
            custom_stops = recalculate_cumulative_values(custom_stops_raw)
            return custom_stops, plan.get('name', '')

    # Use base plan stops
    raw_stops = get_ride_plan_stops(plan['id'])
    # Recalculate cumulative times (same as ride_plan_detail does)
    stops = []
    cum_time = 0
    for s in raw_stops:
        d = dict(s)
        seg_time = int(d.get('segment_time_min') or 0)
        cum_time += seg_time
        d['cum_time_min'] = cum_time
        stops.append(d)
    return stops, plan.get('name', '')


def _build_weather_segments(sample_points, weather_data, bearings, start_dt,
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
            'conditions': wmo_to_text(wmo_code),
            'conditions_icon': wmo_to_icon(wmo_code),
            'elevation_ft': elev_ft,
            'lat': pt['lat'],
            'lng': pt['lng'],
            'rider_bearing_deg': bearing,
        })

    return segments


def _build_chart_data(segments):
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
    }


@weather_bp.route('/weather')
def weather_page():
    """Render the weather + wind map page with input form.

    Supports query params for pre-filling from brevet/ride plan pages:
      ?rwgps_url=...&start_datetime=...&speed_mph=...&plan_slug=...&auto=1
    """
    mapbox_token = current_app.config.get('MAPBOX_ACCESS_TOKEN', '')
    return render_template('weather.html',
                           mapbox_token=mapbox_token,
                           prefill_url=request.args.get('rwgps_url', ''),
                           prefill_datetime=request.args.get('start_datetime', ''),
                           prefill_speed=request.args.get('speed_mph', ''),
                           prefill_plan_slug=request.args.get('plan_slug', ''),
                           auto_fetch=request.args.get('auto', ''))


@weather_bp.route('/api/weather-map', methods=['POST'])
def weather_map_api():
    """JSON API: fetch route weather and return data for table + map + charts."""
    data = request.get_json(silent=True) or {}
    rwgps_url = (data.get('rwgps_url') or '').strip()
    start_datetime_str = data.get('start_datetime')
    speed_mph = data.get('speed_mph')
    plan_slug = data.get('plan_slug')

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

    t0 = time.time()

    # Fetch route from RWGPS (cached 1 hour — route geometry rarely changes)
    route_cache_key = f"rwgps-route:{route_id}"
    route_data = cache.get(route_cache_key) if cache else None
    if route_data:
        logger.info("RWGPS cache hit for route %s", route_id)
    else:
        try:
            route_data = fetch_route(route_id)
            if cache:
                cache.set(route_cache_key, route_data, timeout=_ROUTE_CACHE_TTL)
            logger.info("RWGPS fetch for route %s took %.1fs", route_id, time.time() - t0)
        except Exception:
            logger.exception("Failed to fetch RWGPS route %s", route_id)
            return jsonify({'error': 'Could not fetch route data from RideWithGPS. The route may not exist or the service may be temporarily unavailable.'}), 502

    track_points = route_data.get('track_points', [])
    if not track_points:
        return jsonify({'error': 'This route has no GPS track data.'}), 400

    # Sample at dense interval for map; table picks every Nth from the same data
    map_sample = sample_track_points(track_points, interval_m=_MAP_INTERVAL_M)

    if not map_sample:
        return jsonify({'error': 'Could not sample points from this route.'}), 400

    # Single weather fetch for all map points (one API call, cached 1 hour)
    start_hour_str = start_dt.strftime("%Y-%m-%dT%H:00")
    slug = f"route-{route_id}"
    t1 = time.time()
    try:
        logger.info("Fetching weather for %d sample points, route %s", len(map_sample), route_id)
        weather_data = get_cached_route_weather(slug, start_hour_str, map_sample, cache=cache)
        logger.info("Weather fetch took %.1fs, %d forecasts", time.time() - t1, len(weather_data))
    except Exception:
        logger.exception("Weather fetch failed for route %s with %d points", route_id, len(map_sample))
        return jsonify({'error': 'Weather data is temporarily unavailable. Please try again.'}), 503

    # Compute bearings for map points
    map_bearings = []
    for i in range(len(map_sample) - 1):
        map_bearings.append(calculate_bearing(
            map_sample[i]['lat'], map_sample[i]['lng'],
            map_sample[i + 1]['lat'], map_sample[i + 1]['lng'],
        ))

    # Build arrival time interpolator from ride plan if available
    arrival_fn = None
    plan_source = None
    if plan_slug:
        rider_id = session.get('rider_id')
        plan_stops, plan_name = _load_plan_stops(plan_slug, rider_id)
        if plan_stops:
            arrival_fn = _build_arrival_interpolator(plan_stops, start_dt)
            plan_source = 'custom' if rider_id and plan_name else 'base'
            logger.info("Using %s plan '%s' timing (%d stops) for route %s",
                        plan_source, plan_name, len(plan_stops), route_id)

    # Parse speed (mph) — used as fallback if no plan timing
    try:
        rider_speed = float(speed_mph) if speed_mph else None
    except (ValueError, TypeError):
        rider_speed = None

    # Build all segments with elevation and expanded weather fields
    map_segments = _build_weather_segments(
        map_sample, weather_data, map_bearings, start_dt,
        rider_speed, track_points, arrival_fn)

    # Table: pick every Nth segment to approximate TABLE_INTERVAL spacing
    table_step = max(1, _TABLE_INTERVAL_M // _MAP_INTERVAL_M)
    table_segments = [map_segments[i] for i in range(0, len(map_segments), table_step)]
    if table_segments and map_segments and table_segments[-1] is not map_segments[-1]:
        table_segments.append(map_segments[-1])

    # Chart data from dense map segments
    chart_data = _build_chart_data(map_segments)

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
    total_elev_m = route_data.get('elevation_gain', 0) or 0

    logger.info("Weather map total time: %.1fs for route %s (%d map points, %d table points, plan=%s)",
                time.time() - t0, route_id, len(map_segments), len(table_segments), plan_source or 'none')

    return jsonify({
        'route_name': route_name,
        'total_distance_mi': _km_to_mi(total_dist_m / 1000),
        'total_elevation_ft': round(total_elev_m * _M_TO_FT),
        'plan_source': plan_source,
        'polyline': polyline,
        'table_segments': table_segments,
        'map_segments': map_segments,
        'chart_data': chart_data,
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
