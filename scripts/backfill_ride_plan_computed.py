#!/usr/bin/env python3
"""
Backfill computed columns for ride_plan and ride_plan_stop tables.

Computes: seg_dist, ft_per_mi, avg_speed, cum_time_min, bookend_time_min,
          time_bank_min, difficulty_score per stop.
Plan-level: distance_km, cutoff_hours, avg_moving_speed, avg_elapsed_speed,
            total_moving_time_min, total_elapsed_time_min, total_break_time_min,
            overall_ft_per_mile, rwgps_route_id.

Usage:
    DATABASE_URL='postgresql://...' python scripts/backfill_ride_plan_computed.py
"""
import os
import re
import sys
import psycopg2
import psycopg2.extras
from pathlib import Path
from decimal import Decimal

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith('DATABASE_URL='):
                DATABASE_URL = line.split('=', 1)[1].strip()
                break

if not DATABASE_URL:
    print("DATABASE_URL not set")
    sys.exit(1)

# -- Helpers (same logic as routes/riders.py) --

_CUTOFF_HOURS = {200: 13.5, 300: 20, 400: 27, 600: 40, 1000: 75, 1200: 90}


def _extract_distance_km(name):
    match = re.search(r'(\d{3,4})\s*[kK]', name)
    return int(match.group(1)) if match else None


def _get_cutoff_hours(km):
    if not km:
        return None
    for limit in sorted(_CUTOFF_HOURS):
        if km <= limit:
            return _CUTOFF_HOURS[limit]
    return None


def _extract_rwgps_route_id(url):
    if not url:
        return None
    m = re.search(r'/routes/(\d+)', url)
    return m.group(1) if m else None


def _compute_difficulty_score(ft_per_mi, notes):
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


def _to_float(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _to_int(v):
    if v is None:
        return None
    return int(v)


def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Fetch all plans
    cur.execute("SELECT * FROM ride_plan ORDER BY name")
    plans = cur.fetchall()
    print(f"Processing {len(plans)} ride plans...\n")

    for plan in plans:
        plan_id = plan['id']
        name = plan['name']
        total_miles = _to_float(plan['total_distance_miles']) or 0
        total_elev = _to_int(plan['total_elevation_ft']) or 0

        # Fetch stops
        cur.execute("""SELECT * FROM ride_plan_stop
                       WHERE ride_plan_id = %s ORDER BY stop_order""", (plan_id,))
        stops = cur.fetchall()

        if not stops:
            print(f"  SKIP {name} (no stops)")
            continue

        # Plan-level computed fields
        distance_km = _extract_distance_km(name)
        cutoff_hours = _get_cutoff_hours(distance_km)
        rwgps_url = plan.get('rwgps_url_team') or plan.get('rwgps_url')
        rwgps_route_id = _extract_rwgps_route_id(rwgps_url)
        overall_ft_per_mile = round(total_elev / total_miles, 1) if total_miles > 0 else 0

        # Per-stop computations
        cum_time_min = 0
        prev_dist = 0.0
        total_moving_time = 0
        total_break_time = 0

        for s in stops:
            sid = s['id']
            dist = _to_float(s['distance_miles']) or 0.0
            elev = _to_int(s['elevation_gain'])
            seg_time = _to_int(s['segment_time_min'])
            notes = s.get('notes') or ''

            seg_dist = round(dist - prev_dist, 1)

            ft_per_mi = int(round(elev / seg_dist)) if elev and seg_dist > 0 else None
            avg_speed = round(seg_dist / (seg_time / 60.0), 1) if seg_time and seg_time > 0 and seg_dist > 0 else None

            if seg_time:
                cum_time_min += seg_time
                if seg_dist > 0:
                    total_moving_time += seg_time
                else:
                    total_break_time += seg_time

            # Bookend time
            bookend_time_min = None
            time_bank_min = None
            if cutoff_hours and total_miles > 0 and dist > 0:
                fraction = dist / total_miles
                bookend_time_min = round(fraction * cutoff_hours * 60)
                time_bank_min = bookend_time_min - cum_time_min

            difficulty_score = _compute_difficulty_score(ft_per_mi, notes)

            # Update stop
            cur.execute("""UPDATE ride_plan_stop SET
                seg_dist = %s, ft_per_mi = %s, avg_speed = %s,
                cum_time_min = %s, bookend_time_min = %s, time_bank_min = %s,
                difficulty_score = %s
                WHERE id = %s""",
                (seg_dist, ft_per_mi, avg_speed,
                 cum_time_min, bookend_time_min, time_bank_min,
                 difficulty_score, sid))

            prev_dist = dist

        total_elapsed = cum_time_min
        avg_moving_speed = round(total_miles / (total_moving_time / 60.0), 1) if total_moving_time > 0 else None
        avg_elapsed_speed = round(total_miles / (total_elapsed / 60.0), 1) if total_elapsed > 0 else None

        # Update plan
        cur.execute("""UPDATE ride_plan SET
            distance_km = %s, cutoff_hours = %s,
            avg_moving_speed = %s, avg_elapsed_speed = %s,
            total_moving_time_min = %s, total_elapsed_time_min = %s,
            total_break_time_min = %s, overall_ft_per_mile = %s,
            rwgps_route_id = %s
            WHERE id = %s""",
            (distance_km, cutoff_hours,
             avg_moving_speed, avg_elapsed_speed,
             total_moving_time, total_elapsed,
             total_break_time, overall_ft_per_mile,
             rwgps_route_id, plan_id))

        print(f"  OK   {name} ({len(stops)} stops, {distance_km or '?'}K, "
              f"moving={total_moving_time}m, break={total_break_time}m, "
              f"route_id={rwgps_route_id or 'none'})")

    conn.commit()
    conn.close()
    print(f"\nDone: {len(plans)} plans backfilled.")


if __name__ == '__main__':
    main()
