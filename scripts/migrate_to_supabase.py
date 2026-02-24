#!/usr/bin/env python3
"""
Migrate data from local SQLite database to Supabase PostgreSQL.

Usage:
    export DATABASE_URL='postgresql://postgres.glmkavkypzalzqznuvdr:[PASSWORD]@aws-0-us-west-2.pooler.supabase.com:6543/postgres'
    python3 scripts/migrate_to_supabase.py
"""

import os
import sys
import sqlite3
import psycopg2
import psycopg2.extras
from pathlib import Path

SQLITE_PATH = Path(__file__).parent.parent / 'data' / 'team_asha.db'
DATABASE_URL = os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    print("❌ DATABASE_URL environment variable not set")
    print("   export DATABASE_URL='postgresql://...'")
    sys.exit(1)


def _empty_to_none(val):
    """Convert empty strings to None (NULL) for PostgreSQL."""
    if val == '':
        return None
    return val


def migrate():
    # Connect to SQLite
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    # Connect to PostgreSQL
    pg_conn = psycopg2.connect(DATABASE_URL)
    pg_cur = pg_conn.cursor()

    try:
        # ---- 1. CLUB ----
        rows = sqlite_conn.execute("SELECT * FROM club ORDER BY id").fetchall()
        for r in rows:
            pg_cur.execute(
                "INSERT INTO club (id, code, name, region) VALUES (%s, %s, %s, %s)",
                (r['id'], r['code'], r['name'], r['region'])
            )
        print(f"✅ club: {len(rows)} rows")

        # ---- 2. SEASON ----
        rows = sqlite_conn.execute("SELECT * FROM season ORDER BY id").fetchall()
        for r in rows:
            pg_cur.execute(
                "INSERT INTO season (id, name, start_date, end_date, is_current) VALUES (%s, %s, %s, %s, %s)",
                (r['id'], r['name'], r['start_date'], r['end_date'], bool(r['is_current']))
            )
        print(f"✅ season: {len(rows)} rows")

        # ---- 3. RIDER ----
        rows = sqlite_conn.execute("SELECT * FROM rider ORDER BY id").fetchall()
        for r in rows:
            pg_cur.execute(
                "INSERT INTO rider (id, rusa_id, first_name, last_name) VALUES (%s, %s, %s, %s)",
                (r['id'], r['rusa_id'], r['first_name'], r['last_name'])
            )
        print(f"✅ rider: {len(rows)} rows")

        # ---- 4. RIDER_PROFILE ----
        rows = sqlite_conn.execute("SELECT * FROM rider_profile ORDER BY rider_id").fetchall()
        for r in rows:
            pg_cur.execute(
                """INSERT INTO rider_profile (rider_id, photo_filename, bio, pbp_2023_registered, pbp_2023_status)
                   VALUES (%s, %s, %s, %s, %s)""",
                (r['rider_id'], r['photo_filename'], r['bio'],
                 bool(r['pbp_2023_registered']) if r['pbp_2023_registered'] is not None else False,
                 r['pbp_2023_status'])
            )
        print(f"✅ rider_profile: {len(rows)} rows")

        # ---- 5. RIDE ----
        rows = sqlite_conn.execute("SELECT * FROM ride ORDER BY id").fetchall()
        for r in rows:
            pg_cur.execute(
                """INSERT INTO ride (id, season_id, club_id, name, ride_type, date, distance_km,
                   elevation_ft, distance_miles, ft_per_mile, rwgps_url, rusa_event_id, is_team_ride)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (r['id'], r['season_id'], r['club_id'], r['name'], r['ride_type'],
                 _empty_to_none(r['date']), r['distance_km'], r['elevation_ft'], r['distance_miles'],
                 r['ft_per_mile'], _empty_to_none(r['rwgps_url']), _empty_to_none(r['rusa_event_id']),
                 bool(r['is_team_ride']) if r['is_team_ride'] is not None else True)
            )
        print(f"✅ ride: {len(rows)} rows")

        # ---- 6. RIDER_RIDE ----
        rows = sqlite_conn.execute("SELECT * FROM rider_ride ORDER BY id").fetchall()
        for r in rows:
            pg_cur.execute(
                """INSERT INTO rider_ride (id, rider_id, ride_id, status, finish_time)
                   VALUES (%s, %s, %s, %s, %s)""",
                (r['id'], r['rider_id'], r['ride_id'], r['status'], r['finish_time'])
            )
        print(f"✅ rider_ride: {len(rows)} rows")

        # ---- 7. RIDER_RIDE_SIGNUP ----
        rows = sqlite_conn.execute("SELECT * FROM rider_ride_signup ORDER BY id").fetchall()
        for r in rows:
            pg_cur.execute(
                """INSERT INTO rider_ride_signup (id, rider_id, ride_id, signed_up_at)
                   VALUES (%s, %s, %s, %s)""",
                (r['id'], r['rider_id'], r['ride_id'], r['signed_up_at'])
            )
        print(f"✅ rider_ride_signup: {len(rows)} rows")

        # ---- 8. UPCOMING_RUSA_EVENT ----
        rows = sqlite_conn.execute("SELECT * FROM upcoming_rusa_event ORDER BY id").fetchall()
        for r in rows:
            pg_cur.execute(
                """INSERT INTO upcoming_rusa_event (id, region, ride_type, date, distance_km,
                   climbing, route_name, rwgps_url, distance_miles, elevation_ft,
                   start_location, start_time, time_limit_hours, event_status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (r['id'], r['region'], r['ride_type'], r['date'], r['distance_km'],
                 r['climbing'], r['route_name'], r['rwgps_url'], r['distance_miles'],
                 r['elevation_ft'], r['start_location'], r['start_time'],
                 r['time_limit_hours'], r['event_status'] or 'ACTIVE')
            )
        print(f"✅ upcoming_rusa_event: {len(rows)} rows")

        # ---- Reset sequences to max id + 1 ----
        sequences = [
            ('club', 'club_id_seq'),
            ('season', 'season_id_seq'),
            ('rider', 'rider_id_seq'),
            ('ride', 'ride_id_seq'),
            ('rider_ride', 'rider_ride_id_seq'),
            ('rider_ride_signup', 'rider_ride_signup_id_seq'),
            ('upcoming_rusa_event', 'upcoming_rusa_event_id_seq'),
        ]
        for table, seq in sequences:
            pg_cur.execute(f"SELECT setval('{seq}', COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")
        print("✅ Sequences reset")

        pg_conn.commit()
        print("\n🎉 Migration complete!")

    except Exception as e:
        pg_conn.rollback()
        print(f"\n❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        sqlite_conn.close()
        pg_cur.close()
        pg_conn.close()


if __name__ == '__main__':
    migrate()
