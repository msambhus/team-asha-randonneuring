"""Compatibility shim + Team Asha-only weather functions.

The club-agnostic weather engine (bearing/headwind/crosswind math, wind-arrow and
WMO helpers, track sampling, Open-Meteo fetch/parse, segment/chart builders, and
the new point-forecast helpers) now lives in ``shared/weather.py`` so BrevetHub can
reuse the identical code. This module re-exports every one of those names — public
AND private (routes/weather.py imports ``_safe_get``/``_c_to_f``/... directly) — so
every existing ``from services.weather import X`` keeps working unchanged, and so
existing test patches like ``services.weather.requests.get`` and
``services.weather.fetch_route_weather`` still resolve here.

What stays here (NOT in shared/) are the three Team Asha-specific functions that
depend on Team Asha ``models`` — ``load_stored_route_weather`` (reads
``route_weather_cache``), ``get_historical_stop_wind`` (reads/writes
``ride_wind_data``), and ``fetch_stop_wind`` (reads through
``load_stored_route_weather``). ``shared/weather.py`` must import nothing from the
Team Asha app (isolation guard), so these model-coupled functions live in this shim.
They call the shared helpers as bare module globals, and the model dependencies
(``get_ride_wind_data``/``save_ride_wind_data``/``fetch_historical_wind``/
``load_stored_route_weather``) resolve to this module's namespace — so the existing
``patch('services.weather.<name>')`` tests keep patching the exact binding these
functions call.
"""
import logging
from datetime import datetime, timedelta, date  # noqa: F401  (used by retained fns)

from shared import weather as _shared_weather

# Re-export EVERY name from the shared engine (public + private helpers + the
# ``requests`` module object, so ``services.weather.requests.get`` patches still
# resolve) into this module's namespace. This makes ``from services.weather import
# X`` work for any X the shared engine defines, and lets the Team Asha-only
# functions below call shared helpers as bare globals.
globals().update({_k: _v for _k, _v in vars(_shared_weather).items()
                  if not _k.startswith('__')})

from models import get_ride_wind_data, save_ride_wind_data  # noqa: E402

# This module's own logger (the shared copy would log under ``shared.weather``).
logger = logging.getLogger(__name__)


# ── Stored weather (read path — populated by the fetch-route-weather cron) ──

def load_stored_route_weather(route_id, forecast_date):
    """Load the pre-fetched Open-Meteo forecast for a route on a date (TA-237).

    The hourly fetch-route-weather cron pre-computes and stores a dense (15 km) sampled
    forecast per (route_id, forecast_date); every request path READS it here instead of
    calling Open-Meteo live. Returns (weather_data, sample_points) — a list of per-sample
    forecast dicts and the aligned [{lat,lng,distance_m}] points it was sampled at — or
    (None, None) when nothing is stored (new route, beyond-horizon ride, or the cron has
    not run yet), so callers degrade gracefully with no live fallback.
    """
    if route_id is None or forecast_date is None:
        return None, None
    from models import get_route_weather_cache
    row = get_route_weather_cache(route_id, forecast_date)
    if not row:
        return None, None
    return row.get('weather_data'), row.get('sample_points')


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

    # STOR-02: DB check before API call. Rows saved before migration 027 lack
    # gust / temp-range columns; treat those as stale and fall through to a
    # (deterministic) re-fetch so the new columns get backfilled once. Freshness
    # is keyed on temp_min_c (temperature_2m is always present in an Open-Meteo
    # response) rather than wind_gust_kmh (gusts can legitimately be absent),
    # so a gust-less ride heals once instead of re-fetching on every view.
    if ride_id is not None:
        stored = get_ride_wind_data(ride_id)
        if stored and stored[0].get('temp_min_c') is not None:
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

    # Track the previous stop's arrival so each segment's weather is sampled over
    # the leg's time window (prev arrival -> this arrival), not just a point.
    prev_arrival_dt = start_dt

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
            arrival_dt = start_dt + timedelta(minutes=float(arrival_time_min))
        else:
            dist_km = float(stops[i].get('distance_miles') or 0) * 1.60934
            hours_to_arrive = dist_km / _AVG_SPEED_KMH if _AVG_SPEED_KMH > 0 else 0
            arrival_dt = start_dt + timedelta(hours=hours_to_arrive)

        times = hourly.get('time', []) or []
        hour_index = get_hour_index(times, arrival_dt)

        # Segment window: hours ridden from the previous stop to this one, in THIS
        # stop's forecast. this-stop index is the UPPER bound; lower bound is the
        # previous stop's arrival hour (start-of-ride for the first segment).
        prev_index = get_hour_index(times, prev_arrival_dt)
        lo, hi = min(prev_index, hour_index), max(prev_index, hour_index)
        gust_kmh = _window_max(hourly, 'wind_gusts_10m', lo, hi)
        temp_min_c, temp_max_c = _window_min_max(hourly, 'temperature_2m', lo, hi)

        wind_speed = _safe_get(hourly, 'wind_speed_10m', hour_index, 0.0)
        wind_dir = _safe_get(hourly, 'wind_direction_10m', hour_index, 0)
        temperature = _safe_get(hourly, 'temperature_2m', hour_index, 0.0)

        # If the window yielded no temperature samples, fall back to the arrival
        # point temp so temp_min_c is never None on a saved row — otherwise the
        # temp_min_c freshness check would re-fetch this ride on every view.
        if temp_min_c is None:
            temp_min_c = temp_max_c = round(float(temperature), 1)

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
            'wind_arrow_deg': wind_arrow_rotation(hw, cw),
            'temperature_c': round(float(temperature), 1),
            'wind_gust_kmh': gust_kmh,
            'temp_min_c': temp_min_c,
            'temp_max_c': temp_max_c,
            'conditions': '',
            'data_source': data_source,
        })

        prev_arrival_dt = arrival_dt

    if not wind_rows:
        return None, None

    # STOR-02: persist after successful fetch
    if ride_id is not None:
        save_ride_wind_data(ride_id, wind_rows)

    return wind_rows, data_source


def fetch_stop_wind(stops, route_id, forecast_date, start_time_str):
    """Return per-stop wind data for the ride-plan table, READ from stored weather (TA-237).

    stops: list of stop dicts with 'distance_miles' (and optionally 'arrival_time_min')
    route_id: RWGPS route id — keys the stored forecast
    forecast_date: datetime.date of the ride — keys the stored forecast AND dates arrivals
    start_time_str: "HH:MM" ride start clock (a time object stringifies fine too)

    Loads the forecast the hourly fetch-route-weather cron stored for
    (route_id, forecast_date), maps each stop to the nearest stored sample point by route
    distance, derives bearing from adjacent samples, and picks the arrival-hour forecast —
    with ZERO live Open-Meteo calls. Returns a list the same length as `stops` (None for
    stops that can't be resolved), or None when no forecast is stored for this route+date
    (graceful miss: the plan/calendar simply shows no wind, exactly like the old
    API-error path). Same per-stop dict shape as before.
    """
    if route_id is None or forecast_date is None:
        return None

    weather_data, sample_points = load_stored_route_weather(route_id, forecast_date)
    if not weather_data or not sample_points:
        return None

    # Ride start on the REAL ride date — the stored hourly arrays span that day, so
    # arrival-hour selection is correct even for a ride weeks out (the old code timed
    # arrivals from datetime.now(), i.e. as if the ride were today).
    s = str(start_time_str or '')
    try:
        start_hour = int(s[:2])
        start_minute = int(s[3:5]) if len(s) >= 5 else 0
    except (ValueError, TypeError):
        start_hour, start_minute = 7, 0
    start_dt = datetime(forecast_date.year, forecast_date.month, forecast_date.day,
                        start_hour, start_minute)

    result = []
    for stop in stops:
        target_m = float(stop.get('distance_miles') or 0) * MILES_TO_METERS
        idx = _nearest_sample_index(sample_points, target_m)
        if idx is None or idx >= len(weather_data):
            result.append(None)
            continue

        forecast = weather_data[idx]
        hourly = forecast.get('hourly', {})

        # Use arrival_time_min if present; otherwise estimate from distance
        arrival_time_min = stop.get('arrival_time_min')
        if arrival_time_min is not None:
            arrival_dt = start_dt + timedelta(minutes=float(arrival_time_min))
        else:
            dist_km = float(stop.get('distance_miles') or 0) * 1.60934
            hours_to_arrive = dist_km / _AVG_SPEED_KMH if _AVG_SPEED_KMH > 0 else 0
            arrival_dt = start_dt + timedelta(hours=hours_to_arrive)

        hour_index = get_hour_index(hourly.get('time', []), arrival_dt)

        wind_speed = _safe_get(hourly, 'wind_speed_10m', hour_index, 0.0)
        wind_dir = _safe_get(hourly, 'wind_direction_10m', hour_index, 0)
        temperature = _safe_get(hourly, 'temperature_2m', hour_index, 0.0)

        # Bearing from the stored sample geometry (nearest sample -> its neighbour).
        bearing = _sample_bearing(sample_points, idx)

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
            'crosswind_kmh': round(float(cw), 1),
            'wind_type': wind_type,
            'wind_arrow_deg': wind_arrow_rotation(hw, cw),
            'wind_direction_deg': int(wind_dir),
            'rider_bearing_deg': int(bearing),
            'style': style,
            'label': wind_label(hw),
            'temperature_c': round(float(temperature), 1),
            'temperature_f': int(temp_f),
        })

    return result
