"""Weather routes: standalone weather + wind map page."""
import math
import time
import logging
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, jsonify, current_app, session

from services.rwgps import fetch_route, extract_controls, extract_rwgps_route_id
from services.weather import (
    sample_track_points, calculate_bearing, headwind_component,
    load_stored_route_weather, format_weather_response,
    wind_label, wmo_to_text, wmo_to_icon, get_hour_index, _safe_get,
    # Segment/chart builders now live in services.weather so the live map can
    # reuse the identical time-aware pipeline (imported here so the weather page's
    # behavior and this module's public helpers are unchanged).
    build_arrival_interpolator, build_weather_segments, build_chart_data,
    _c_to_f, _kmh_to_mph, _km_to_mi, _interpolate_elevation,
    _KMH_TO_MPH, _M_TO_FT,
)
from cache import cache

# Cache TTL for RWGPS route data (route geometry doesn't change often)
_ROUTE_CACHE_TTL = 3600  # 1 hour

logger = logging.getLogger(__name__)

weather_bp = Blueprint('weather', __name__)

# Polyline decimation: keep every Nth track point for map rendering
_POLYLINE_DECIMATION = 20

# Sampling intervals
_TABLE_INTERVAL_M = 25000   # 25km (~16 mi) between table rows
_MAP_INTERVAL_M = 15000     # 15km between map arrows

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
            custom_stops_raw, custom_plan_data = get_merged_plan_stops(custom['id'])
            custom_stops = recalculate_cumulative_values(custom_stops_raw, custom_plan_data or custom)
            return custom_stops, plan.get('name', '')

    # Use base plan stops
    raw_stops = get_ride_plan_stops(plan['id'])
    # Recalculate cumulative times (same as ride_plan_detail does):
    # cum time includes BOTH the riding segment time AND the break duration
    # at each control. Without the break time, the last stop's cum_time
    # under-counts the actual finish by hours, which compresses the whole
    # weather schedule into too-early hours.
    stops = []
    cum_time = 0
    for s in raw_stops:
        d = dict(s)
        seg_time = int(d.get('segment_time_min') or 0)
        stop_duration = int(d.get('stop_duration_min') or 0)
        cum_time += seg_time + stop_duration
        d['cum_time_min'] = cum_time
        stops.append(d)
    return stops, plan.get('name', '')


@weather_bp.route('/weather')
def weather_page():
    """Render the weather + wind map page with input form.

    Supports query params for pre-filling from brevet/ride plan pages:
      ?rwgps_url=...&start_datetime=...&speed_mph=...&plan_slug=...&auto=1
    """
    mapbox_token = current_app.config.get('MAPBOX_ACCESS_TOKEN', '')
    plan_slug = request.args.get('plan_slug', '')
    plan_name = ''
    if plan_slug:
        from models import get_ride_plan_by_slug
        plan = get_ride_plan_by_slug(plan_slug)
        if plan:
            plan_name = plan.get('name', '')
    return render_template('weather.html',
                           mapbox_token=mapbox_token,
                           prefill_url=request.args.get('rwgps_url', ''),
                           prefill_datetime=request.args.get('start_datetime', ''),
                           prefill_speed=request.args.get('speed_mph', ''),
                           prefill_plan_slug=plan_slug,
                           prefill_plan_name=plan_name,
                           auto_fetch=request.args.get('auto', ''))


def build_weather_payload(route_id, start_dt, speed_mph=None, plan_slug=None,
                          rider_id=None, include_summary=True):
    """Shared core: fetch a route + its weather and build the table/map/chart payload.

    Used by both the web `/api/weather-map` POST and the mobile
    `GET /api/ride/<id>/weather`. Returns ``(payload_dict, None)`` on success or
    ``(None, (error_dict, status_code))`` on failure, so callers just jsonify.

    speed_mph: optional already-parsed float (flat-speed fallback when there's no
    plan timing). plan_slug + rider_id drive plan-aware arrival interpolation.
    """
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
            return None, ({'error': 'Could not fetch route data from RideWithGPS. The route may not exist or the service may be temporarily unavailable.'}, 502)

    track_points = route_data.get('track_points', [])
    if not track_points:
        return None, ({'error': 'This route has no GPS track data.'}, 400)

    # Weather is pre-fetched hourly by the fetch-route-weather cron and READ from storage
    # here — no live Open-Meteo call on this request path (TA-237). The stored sample
    # points ARE the dense (15 km) points the forecast was sampled at, so weather_data[i]
    # aligns with map_sample[i]. The route's own track_points (fetched above) still drive
    # the polyline / elevation / cue points.
    weather_data, map_sample = load_stored_route_weather(route_id, start_dt.date())
    if not weather_data or not map_sample:
        return None, ({'available': False, 'reason': 'not_cached',
                       'message': 'Weather forecast is not available yet — it is prepared '
                                  'hourly for upcoming rides. Check back closer to the ride.'},
                      200)

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
        try:
            plan_stops, plan_name = _load_plan_stops(plan_slug, rider_id)
            if plan_stops:
                arrival_fn = build_arrival_interpolator(plan_stops, start_dt)
                plan_source = 'custom' if rider_id and plan_name else 'base'
                logger.info("Using %s plan '%s' timing (%d stops) for route %s",
                            plan_source, plan_name, len(plan_stops), route_id)
        except Exception:
            logger.exception("Failed to load plan %s, falling back to speed-based timing", plan_slug)

    # Build all segments with elevation and expanded weather fields
    map_segments = build_weather_segments(
        map_sample, weather_data, map_bearings, start_dt,
        speed_mph, track_points, arrival_fn)

    # Table: pick every Nth segment to approximate TABLE_INTERVAL spacing
    table_step = max(1, _TABLE_INTERVAL_M // _MAP_INTERVAL_M)
    table_segments = [map_segments[i] for i in range(0, len(map_segments), table_step)]
    if table_segments and map_segments and table_segments[-1] is not map_segments[-1]:
        table_segments.append(map_segments[-1])

    # Chart data from dense map segments
    chart_data = build_chart_data(map_segments)

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
    total_dist_mi = _km_to_mi(total_dist_m / 1000)
    total_elev_ft = round(total_elev_m * _M_TO_FT)

    # Generate ride weather summary (LLM or rule-based fallback)
    ride_summary = (generate_ride_summary(route_name, total_dist_mi, total_elev_ft,
                                          map_segments)
                    if include_summary else None)

    logger.info("Weather map total time: %.1fs for route %s (%d map points, %d table points, plan=%s)",
                time.time() - t0, route_id, len(map_segments), len(table_segments), plan_source or 'none')

    return {
        'route_name': route_name,
        'total_distance_mi': total_dist_mi,
        'total_elevation_ft': total_elev_ft,
        'plan_source': plan_source,
        'polyline': polyline,
        'table_segments': table_segments,
        'map_segments': map_segments,
        'chart_data': chart_data,
        'cue_points': cue_points,
        'ride_summary': ride_summary,
        'temp_range': {
            'min_f': min(temps_f) if temps_f else 0,
            'max_f': max(temps_f) if temps_f else 0,
        },
        'attribution': '*Weather data: Open-Meteo*',
    }, None


def build_multiday_weather_payload(legs, start_dt, speed_mph=None,
                                   plan_name='Multi-day ride'):
    """Combine ordered RWGPS legs into one continuous weather/map payload.

    Each leg reads its own day-specific stored forecast. Distances and cue points
    are offset into the complete route so the existing map, charts, hover sync,
    table and sharing UI work without a separate multi-day frontend.
    """
    combined_segments = []
    combined_table = []
    combined_polyline = []
    combined_cues = []
    leg_meta = []
    distance_offset = 0.0
    total_elevation = 0

    for leg in legs:
        route_id = extract_rwgps_route_id(leg.get('rwgps_url'))
        if not route_id:
            continue
        day_number = max(1, int(leg.get('day_number') or 1))
        leg_start = start_dt + timedelta(days=day_number - 1)
        payload, err = build_weather_payload(
            route_id, leg_start, speed_mph=speed_mph,
            plan_slug=None, rider_id=None, include_summary=False)
        if err:
            body, status = err
            body = dict(body)
            body['leg'] = leg.get('label') or f'Day {day_number}'
            return None, (body, status)

        for segment in payload.get('map_segments') or []:
            row = dict(segment)
            row['distance_mi'] = round(distance_offset + float(row['distance_mi']), 1)
            row['day_number'] = day_number
            combined_segments.append(row)
        for segment in payload.get('table_segments') or []:
            row = dict(segment)
            row['distance_mi'] = round(distance_offset + float(row['distance_mi']), 1)
            row['day_number'] = day_number
            combined_table.append(row)
        for cue in payload.get('cue_points') or []:
            row = dict(cue)
            row['distance_mi'] = round(distance_offset + float(row['distance_mi']), 1)
            row['day_number'] = day_number
            combined_cues.append(row)
        combined_polyline.extend(payload.get('polyline') or [])

        leg_distance = float(payload.get('total_distance_mi') or 0)
        leg_meta.append({
            'day_number': day_number,
            'label': leg.get('label') or f'Day {day_number}',
            'distance_mi': round(leg_distance, 1),
            'route_name': payload.get('route_name'),
            # Keep each leg's full geometry so per-day image exports can fit a
            # real Mapbox map to that day instead of drawing a generic summary.
            'polyline': payload.get('polyline') or [],
        })
        distance_offset += leg_distance
        total_elevation += int(payload.get('total_elevation_ft') or 0)

    if not combined_segments:
        return None, ({'available': False, 'reason': 'no_legs',
                       'message': 'No route legs are available for this plan.'}, 200)

    temps = [s['temperature_f'] for s in combined_table
             if s.get('temperature_f') is not None]
    total_distance = round(distance_offset, 1)
    return {
        'route_name': plan_name,
        'total_distance_mi': total_distance,
        'total_elevation_ft': total_elevation,
        'plan_source': 'base-multiday',
        'polyline': combined_polyline,
        'table_segments': combined_table,
        'map_segments': combined_segments,
        'chart_data': build_chart_data(combined_segments),
        'cue_points': combined_cues,
        'ride_summary': generate_ride_summary(
            plan_name, total_distance, total_elevation, combined_segments),
        'temp_range': {
            'min_f': min(temps) if temps else 0,
            'max_f': max(temps) if temps else 0,
        },
        'legs': leg_meta,
        'attribution': '*Weather data: Open-Meteo*',
    }, None


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

    # Parse speed (mph) — used as fallback if no plan timing
    try:
        rider_speed = float(speed_mph) if speed_mph else None
    except (ValueError, TypeError):
        rider_speed = None

    legs = []
    plan_name = None
    if plan_slug:
        try:
            from models import get_ride_plan_by_slug, get_ride_plan_legs
            plan = get_ride_plan_by_slug(plan_slug)
            if plan:
                plan_name = plan.get('name')
                legs = [dict(row) for row in (get_ride_plan_legs(plan['id']) or [])]
        except Exception:
            logger.exception('Could not load route legs for plan %s', plan_slug)

    if len(legs) > 1:
        payload, err = build_multiday_weather_payload(
            legs, start_dt, speed_mph=rider_speed, plan_name=plan_name or plan_slug)
    else:
        payload, err = build_weather_payload(
            route_id, start_dt, speed_mph=rider_speed, plan_slug=plan_slug,
            rider_id=session.get('rider_id'))
    if err:
        body, status = err
        return jsonify(body), status
    return jsonify(payload)


def generate_ride_summary(route_name, total_distance_mi, total_elevation_ft, map_segments):
    """Generate a concise ride weather summary using OpenAI.

    Falls back to a rule-based summary if OpenAI is unavailable.
    """
    if not map_segments:
        return ''

    # Compute stats for the prompt
    winds = [s['wind_speed_mph'] for s in map_segments]
    gusts = [s['wind_gust_mph'] for s in map_segments]
    headwinds = [s['headwind_mph'] for s in map_segments]
    temps = [s['temperature_f'] for s in map_segments]
    precip = [s['precip_percent'] for s in map_segments]
    rain_mm = [s['precipitation_mm'] for s in map_segments]
    humidity = [s.get('humidity', 0) for s in map_segments]

    max_wind = max(winds) if winds else 0
    max_gust = max(gusts) if gusts else 0
    avg_wind = sum(winds) / len(winds) if winds else 0
    headwind_pct = sum(1 for h in headwinds if h > 3) / len(headwinds) * 100 if headwinds else 0
    tailwind_pct = sum(1 for h in headwinds if h < -3) / len(headwinds) * 100 if headwinds else 0
    rain_hours = sum(1 for r in rain_mm if r > 0)
    heavy_rain = sum(1 for r in rain_mm if r >= 1)
    max_rain = max(rain_mm) if rain_mm else 0
    high_precip_pct = sum(1 for p in precip if p > 50) / len(precip) * 100 if precip else 0

    ft_per_mi = round(total_elevation_ft / total_distance_mi) if total_distance_mi > 0 else 0

    stats = (
        f"Route: {route_name}, {total_distance_mi} mi, {total_elevation_ft:,} ft gain ({ft_per_mi} ft/mi). "
        f"Temp: {min(temps):.0f}-{max(temps):.0f}°F. "
        f"Wind: avg {avg_wind:.0f} mph, max {max_wind:.0f} mph, gusts to {max_gust:.0f} mph. "
        f"Headwind {headwind_pct:.0f}% of route, tailwind {tailwind_pct:.0f}%. "
        f"Rain: {rain_hours} segments with rain, {heavy_rain} with >1mm. Max {max_rain:.1f}mm. "
        f"High precip probability (>50%): {high_precip_pct:.0f}% of route. "
        f"Humidity: {min(humidity):.0f}-{max(humidity):.0f}%."
    )

    # Try OpenAI
    import os
    api_key = os.environ.get('OPENAI_API_KEY')
    if api_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content":
                     "You summarize cycling ride weather forecasts in 1-2 sentences. "
                     "Be direct and practical — mention the most important conditions "
                     "(rain, strong winds, temperature extremes, headwinds). "
                     "Use a tone like a weather briefing for experienced cyclists. "
                     "Include specific numbers. Don't sugarcoat bad conditions."},
                    {"role": "user", "content": stats},
                ],
                temperature=0.5,
                max_tokens=100,
                timeout=8,
            )
            summary = response.choices[0].message.content.strip()
            logger.info("LLM ride summary: %s", summary)
            return summary
        except Exception:
            logger.warning("LLM summary failed, using rule-based fallback")

    # Rule-based fallback
    parts = []
    if max_gust >= 25:
        parts.append(f"strong gusts to {max_gust:.0f} mph")
    elif max_wind >= 15:
        parts.append(f"windy ({max_wind:.0f} mph)")
    if headwind_pct > 40:
        parts.append(f"headwind {headwind_pct:.0f}% of route")
    if heavy_rain > 0:
        parts.append(f"rain expected ({heavy_rain} segments >1mm)")
    elif high_precip_pct > 30:
        parts.append(f"rain likely ({high_precip_pct:.0f}% chance)")
    if min(temps) < 40:
        parts.append(f"cold ({min(temps):.0f}°F low)")
    if max(temps) > 90:
        parts.append(f"hot ({max(temps):.0f}°F)")
    if ft_per_mi >= 60:
        parts.append("hilly")

    if not parts:
        parts.append("mild conditions")

    return '; '.join(parts).capitalize()


def generate_summary_from_stop_wind(route_name, total_distance_mi, total_elevation_ft, stop_wind):
    """Generate ride summary from stop_wind data (used by ride plan detail page).

    Converts stop_wind format to the segment format expected by generate_ride_summary.
    """
    if not stop_wind:
        return ''

    segments = []
    for w in stop_wind:
        if w is None:
            continue
        segments.append({
            'wind_speed_mph': w.get('wind_speed_mph', 0),
            'wind_gust_mph': 0,  # not available in stop_wind
            'headwind_mph': round(float(w.get('headwind_kmh', 0)) * _KMH_TO_MPH, 1),
            'temperature_f': w.get('temperature_f', 0),
            'precip_percent': 0,  # not available in stop_wind
            'precipitation_mm': 0,
            'humidity': 0,
        })

    if not segments:
        return ''

    return generate_ride_summary(route_name, total_distance_mi, total_elevation_ft, segments)


def _default_start_time():
    """Default start: tomorrow at 7:00 AM."""
    tomorrow = datetime.now() + timedelta(days=1)
    return tomorrow.replace(hour=7, minute=0, second=0, microsecond=0)
