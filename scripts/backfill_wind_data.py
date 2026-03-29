#!/usr/bin/env python3
"""
Backfill historical wind data for past rides that have linked ride plans.

Fetches wind data from Open-Meteo Archive API for each ride in the 2024-2025
and 2025-2026 seasons that has a ride plan with an RWGPS route. Stores results
in ride_wind_data table (idempotent — skips rides that already have data).

Usage:
    DATABASE_URL='postgresql://...' python scripts/backfill_wind_data.py
    DATABASE_URL='postgresql://...' python scripts/backfill_wind_data.py --dry-run
    DATABASE_URL='postgresql://...' python scripts/backfill_wind_data.py --ride-id 128
"""
import os
import re
import sys
import time
import argparse
from pathlib import Path
from datetime import date, datetime, timedelta

import psycopg2
import psycopg2.extras
import requests

# ── Database URL ─────────────────────────────────────────────────────

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith('DATABASE_URL='):
                DATABASE_URL = line.split('=', 1)[1].strip()
                break

RWGPS_API_KEY = os.environ.get('RWGPS_API_KEY')
RWGPS_AUTH_TOKEN = os.environ.get('RWGPS_AUTH_TOKEN')
if not RWGPS_API_KEY or not RWGPS_AUTH_TOKEN:
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith('RWGPS_API_KEY='):
                RWGPS_API_KEY = line.split('=', 1)[1].strip()
            elif line.startswith('RWGPS_AUTH_TOKEN='):
                RWGPS_AUTH_TOKEN = line.split('=', 1)[1].strip()

# ── Constants ────────────────────────────────────────────────────────

MILES_TO_METERS = 1609.344
AVG_SPEED_KMH = 22
ARCHIVE_LAG_DAYS = 5
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
RATE_LIMIT_DELAY = 1.0  # seconds between API calls to avoid throttling


# ── RWGPS fetch (standalone, no Flask context) ───────────────────────

def fetch_rwgps_route(route_id):
    """Fetch route from RWGPS API. Returns dict with track_points or None."""
    if not RWGPS_API_KEY or not RWGPS_AUTH_TOKEN:
        print("  SKIP: RWGPS_API_KEY or RWGPS_AUTH_TOKEN not set")
        return None

    url = f'https://ridewithgps.com/api/v1/routes/{route_id}.json'
    headers = {
        'x-rwgps-api-key': RWGPS_API_KEY,
        'x-rwgps-auth-token': RWGPS_AUTH_TOKEN,
    }
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        print(f"  SKIP: RWGPS returned {resp.status_code} for route {route_id}")
        return None
    data = resp.json()
    return data.get('route', data) if isinstance(data, dict) else None


def extract_rwgps_route_id(url):
    """Extract numeric route ID from RWGPS URL."""
    if not url:
        return None
    match = re.search(r'/routes/(\d+)', url)
    return int(match.group(1)) if match else None


# ── Coordinate interpolation (same logic as services/weather.py) ─────

def get_stop_coordinates(stops, track_points):
    """Interpolate lat/lng for each stop from RWGPS track points."""
    valid = [tp for tp in track_points
             if tp.get('y') is not None and tp.get('x') is not None]
    if not valid:
        return [None] * len(stops)

    result = []
    for stop in stops:
        target_m = stop['distance_miles'] * MILES_TO_METERS

        if target_m <= valid[0]['d']:
            result.append({'lat': valid[0]['y'], 'lng': valid[0]['x']})
            continue
        if target_m >= valid[-1]['d']:
            result.append({'lat': valid[-1]['y'], 'lng': valid[-1]['x']})
            continue

        for i in range(1, len(valid)):
            if valid[i]['d'] >= target_m:
                prev, curr = valid[i - 1], valid[i]
                seg_len = curr['d'] - prev['d']
                if seg_len == 0:
                    result.append({'lat': curr['y'], 'lng': curr['x']})
                else:
                    t = (target_m - prev['d']) / seg_len
                    result.append({
                        'lat': prev['y'] + t * (curr['y'] - prev['y']),
                        'lng': prev['x'] + t * (curr['x'] - prev['x']),
                    })
                break
    return result


# ── Wind math (same as services/weather.py) ──────────────────────────

import math

def calculate_bearing(lat1, lon1, lat2, lon2):
    lat1, lon1 = math.radians(lat1), math.radians(lon1)
    lat2, lon2 = math.radians(lat2), math.radians(lon2)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def headwind_component(wind_speed, wind_dir, bearing):
    angle = math.radians(wind_dir - bearing)
    return round(wind_speed * math.cos(angle), 1)


def crosswind_component(wind_speed, wind_dir, bearing):
    angle = math.radians(wind_dir - bearing)
    return round(abs(wind_speed * math.sin(angle)), 1)


def classify_wind(hw, cw):
    if abs(hw) >= abs(cw):
        return 'headwind' if hw > 0 else 'tailwind'
    return 'crosswind'


# ── Open-Meteo archive fetch ────────────────────────────────────────

def fetch_archive_wind(stop_coords, ride_date):
    """Fetch historical wind from Open-Meteo archive API."""
    lats = ",".join(str(round(c['lat'], 4)) for c in stop_coords)
    lngs = ",".join(str(round(c['lng'], 4)) for c in stop_coords)
    date_str = ride_date.strftime('%Y-%m-%d')

    days_ago = (date.today() - ride_date).days

    if days_ago < ARCHIVE_LAG_DAYS:
        # Use forecast past_days for very recent rides
        params = {
            'latitude': lats, 'longitude': lngs,
            'past_days': max(days_ago + 1, 1),
            'hourly': 'wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m',
            'timezone': 'auto',
        }
        url = OPEN_METEO_FORECAST_URL
        data_source = 'forecast_past_days'
    else:
        params = {
            'latitude': lats, 'longitude': lngs,
            'start_date': date_str, 'end_date': date_str,
            'hourly': 'wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m',
            'timezone': 'auto',
        }
        url = OPEN_METEO_ARCHIVE_URL
        data_source = 'archive'

    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    weather_data = [data] if isinstance(data, dict) else data
    return weather_data, data_source


def get_hour_index(times, target_dt):
    """Find the hourly index closest to target_dt."""
    if not times:
        return 0
    target_str = target_dt.strftime('%Y-%m-%dT%H:00')
    for i, t in enumerate(times):
        if t >= target_str:
            return i
    return len(times) - 1


def safe_get(hourly, key, index, default=0.0):
    vals = hourly.get(key, [])
    if index < len(vals) and vals[index] is not None:
        return vals[index]
    return default


# ── Main backfill logic ─────────────────────────────────────────────

def backfill_ride(cur, conn, ride, plan_stops, track_points, dry_run=False):
    """Compute and store wind data for a single ride."""
    ride_id = ride['id']
    ride_date = ride['date']
    if isinstance(ride_date, str):
        ride_date = date.fromisoformat(ride_date)

    # Interpolate stop coordinates
    coords = get_stop_coordinates(plan_stops, track_points)
    valid_coords = [c for c in coords if c is not None]
    if not valid_coords:
        print(f"  SKIP: No valid coordinates for ride {ride_id}")
        return False

    # Fetch historical wind
    try:
        weather_data, data_source = fetch_archive_wind(valid_coords, ride_date)
    except Exception as e:
        print(f"  ERROR: API failed for ride {ride_id}: {e}")
        return False

    if not weather_data:
        print(f"  SKIP: No weather data returned for ride {ride_id}")
        return False

    # Build valid_map: original stop index -> valid_coords index
    valid_map = {}
    valid_idx = 0
    for orig_idx, c in enumerate(coords):
        if c is not None:
            valid_map[orig_idx] = valid_idx
            valid_idx += 1

    # Estimate ride start at 07:00
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

        # Arrival time
        arrival_time_min = plan_stops[i].get('arrival_time_min')
        if arrival_time_min is not None:
            arrival_dt = start_dt + timedelta(minutes=float(arrival_time_min))
        else:
            dist_km = plan_stops[i].get('distance_miles', 0) * 1.60934
            hours_to_arrive = dist_km / AVG_SPEED_KMH if AVG_SPEED_KMH > 0 else 0
            arrival_dt = start_dt + timedelta(hours=hours_to_arrive)

        hour_index = get_hour_index(hourly.get('time', []), arrival_dt)

        wind_speed = safe_get(hourly, 'wind_speed_10m', hour_index, 0.0)
        wind_dir = safe_get(hourly, 'wind_direction_10m', hour_index, 0)
        temperature = safe_get(hourly, 'temperature_2m', hour_index, 0.0)

        # Bearing
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
            'stop_name': plan_stops[i].get('stop_name', f'Stop {i}'),
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
        print(f"  SKIP: No wind rows computed for ride {ride_id}")
        return False

    if dry_run:
        print(f"  DRY-RUN: Would insert {len(wind_rows)} wind rows for ride {ride_id}")
        for row in wind_rows[:3]:
            print(f"    Stop {row['stop_order']}: {row['stop_name']} — "
                  f"{row['wind_speed_kmh']}km/h {row['wind_type']}")
        if len(wind_rows) > 3:
            print(f"    ... and {len(wind_rows) - 3} more stops")
        return True

    # Insert rows
    sql = """
        INSERT INTO ride_wind_data (
            ride_id, stop_order, stop_name,
            wind_speed_kmh, wind_direction_deg,
            headwind_kmh, crosswind_kmh,
            wind_type, temperature_c, conditions, data_source
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (ride_id, stop_order) DO NOTHING
    """
    for row in wind_rows:
        cur.execute(sql, (
            ride_id, row['stop_order'], row['stop_name'],
            row['wind_speed_kmh'], row['wind_direction_deg'],
            row['headwind_kmh'], row['crosswind_kmh'],
            row['wind_type'], row['temperature_c'],
            row['conditions'], row['data_source'],
        ))
    conn.commit()
    print(f"  OK: Inserted {len(wind_rows)} wind rows for ride {ride_id} ({data_source})")
    return True


def main():
    parser = argparse.ArgumentParser(description='Backfill wind data for past rides')
    parser.add_argument('--dry-run', action='store_true', help='Print what would be done without writing')
    parser.add_argument('--ride-id', type=int, help='Backfill a single ride by ID')
    parser.add_argument('--seasons', default='2024-2025,2025-2026', help='Comma-separated season names')
    args = parser.parse_args()

    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Find rides with linked ride plans
    if args.ride_id:
        cur.execute("""
            SELECT r.id, r.name, r.date, r.distance_km,
                   rp.id as plan_id, rp.slug as plan_slug,
                   rp.rwgps_url, rp.rwgps_url_team
            FROM ride r
            JOIN ride_plan rp ON r.ride_plan_id = rp.id
            WHERE r.id = %s AND r.date < CURRENT_DATE
        """, (args.ride_id,))
    else:
        season_list = [s.strip() for s in args.seasons.split(',')]
        cur.execute("""
            SELECT r.id, r.name, r.date, r.distance_km,
                   rp.id as plan_id, rp.slug as plan_slug,
                   rp.rwgps_url, rp.rwgps_url_team
            FROM ride r
            JOIN ride_plan rp ON r.ride_plan_id = rp.id
            JOIN season s ON r.season_id = s.id
            WHERE s.name = ANY(%s)
              AND r.date < CURRENT_DATE
            ORDER BY r.date DESC
        """, (season_list,))

    rides = cur.fetchall()
    print(f"Found {len(rides)} past rides with linked ride plans")

    # Filter out rides that already have wind data
    ride_ids = [r['id'] for r in rides]
    if ride_ids:
        cur.execute(
            "SELECT DISTINCT ride_id FROM ride_wind_data WHERE ride_id = ANY(%s)",
            (ride_ids,)
        )
        existing = {row['ride_id'] for row in cur.fetchall()}
    else:
        existing = set()

    rides_to_process = [r for r in rides if r['id'] not in existing]
    print(f"  {len(existing)} already have wind data, {len(rides_to_process)} to process")

    # Cache RWGPS route data by route ID to avoid refetching
    route_cache = {}
    success = 0
    fail = 0

    for ride in rides_to_process:
        rwgps_url = ride['rwgps_url_team'] or ride['rwgps_url']
        route_id = extract_rwgps_route_id(rwgps_url)

        if not route_id:
            print(f"\nRide {ride['id']} ({ride['name']}): SKIP — no RWGPS URL")
            fail += 1
            continue

        print(f"\nRide {ride['id']} ({ride['name']}) — {ride['date']} — route {route_id}")

        # Get track points (cached)
        if route_id not in route_cache:
            route_data = fetch_rwgps_route(route_id)
            route_cache[route_id] = route_data
            time.sleep(RATE_LIMIT_DELAY)  # Rate limit RWGPS calls
        else:
            route_data = route_cache[route_id]

        if not route_data:
            fail += 1
            continue

        track_points = route_data.get('track_points', [])
        if not track_points:
            print(f"  SKIP: No track points for route {route_id}")
            fail += 1
            continue

        # Get plan stops
        cur.execute("""
            SELECT stop_name, distance_miles, arrival_time_min
            FROM ride_plan_stop
            WHERE ride_plan_id = %s
            ORDER BY stop_order
        """, (ride['plan_id'],))
        plan_stops = [dict(row) for row in cur.fetchall()]

        if not plan_stops:
            print(f"  SKIP: No plan stops for plan {ride['plan_slug']}")
            fail += 1
            continue

        if backfill_ride(cur, conn, ride, plan_stops, track_points, dry_run=args.dry_run):
            success += 1
        else:
            fail += 1

        # Rate limit Open-Meteo calls
        time.sleep(RATE_LIMIT_DELAY)

    print(f"\n{'=' * 50}")
    print(f"Done: {success} succeeded, {fail} skipped/failed out of {len(rides_to_process)}")
    conn.close()


if __name__ == '__main__':
    main()
