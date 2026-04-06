"""Backfill missing Strava fields for matched brevet activities.

Targets two windows:
  - Oct 2025 to present  (current season)
  - 2022 and 2023        (historical seasons)

Total scope: ~156 API calls — completes in ~90 seconds, no rate-limit concerns.

Each row in strava_activity that is linked to a brevet in these windows via
strava_ride_match is re-fetched from the Strava detail endpoint
(GET /activities/{id}) to populate the 14 new columns added in migration 017.

Usage:
    DATABASE_URL=<supabase_url> python scripts/backfill_strava_fields.py
    DATABASE_URL=<supabase_url> python scripts/backfill_strava_fields.py --dry-run
"""

import argparse
import json
import os
import sys
import time

import psycopg2
import psycopg2.extras
import requests


STRAVA_TOKEN_URL = 'https://www.strava.com/oauth/token'
STRAVA_ACTIVITY_URL = 'https://www.strava.com/api/v3/activities/{}'
CALL_DELAY = 0.6   # seconds between calls — ~100 calls/minute, under 100/15min limit


def _refresh_token(connection):
    """Refresh an expired Strava access token. Returns new access_token or None."""
    client_id     = os.environ.get('STRAVA_CLIENT_ID')
    client_secret = os.environ.get('STRAVA_CLIENT_SECRET')
    if not client_id or not client_secret:
        print('  WARN: STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET not set — cannot refresh token')
        return None

    resp = requests.post(STRAVA_TOKEN_URL, data={
        'client_id':     client_id,
        'client_secret': client_secret,
        'grant_type':    'refresh_token',
        'refresh_token': connection['refresh_token'],
    }, timeout=10)
    if not resp.ok:
        print(f'  WARN: token refresh failed: {resp.status_code}')
        return None
    return resp.json().get('access_token')


def _get_access_token(conn_row, db_conn):
    """Return a valid access token, refreshing if expired."""
    if conn_row['expires_at'] and conn_row['expires_at'] < time.time() + 60:
        print(f'  Token expired for rider {conn_row["rider_id"]}, refreshing...')
        new_token = _refresh_token(conn_row)
        if not new_token:
            return None
        cur = db_conn.cursor()
        cur.execute(
            'UPDATE strava_connection SET access_token = %s WHERE rider_id = %s',
            (new_token, conn_row['rider_id'])
        )
        db_conn.commit()
        return new_token
    return conn_row['access_token']


_TOKEN_EXPIRED = object()  # sentinel — distinct from None (not found) and dict (success)


def _fetch_activity_detail(strava_activity_id, access_token):
    """Fetch full activity detail from Strava API.

    Returns:
        dict on success
        None if activity not found (404)
        _TOKEN_EXPIRED sentinel if access token is invalid/expired (401)
    """
    url = STRAVA_ACTIVITY_URL.format(strava_activity_id)
    resp = requests.get(url, headers={'Authorization': f'Bearer {access_token}'}, timeout=15)
    if resp.status_code == 404:
        return None
    if resp.status_code == 401:
        return _TOKEN_EXPIRED
    if resp.status_code == 429:
        print('  WARN: Strava rate limit hit — pausing 15 minutes')
        time.sleep(900)
        resp = requests.get(url, headers={'Authorization': f'Bearer {access_token}'}, timeout=15)
        if resp.status_code == 401:
            return _TOKEN_EXPIRED
    if not resp.ok:
        print(f'  WARN: Strava API error {resp.status_code} for activity {strava_activity_id}')
        return None
    return resp.json()


def _extract_fields(activity):
    """Extract the 14 new fields from a Strava activity detail response."""
    return {
        'average_cadence':      activity.get('average_cadence'),
        'average_temp':         activity.get('average_temp'),
        'calories':             activity.get('calories'),
        'pr_count':             activity.get('pr_count'),
        'achievement_count':    activity.get('achievement_count'),
        'gear_id':              activity.get('gear_id'),
        'elev_high':            activity.get('elev_high'),
        'elev_low':             activity.get('elev_low'),
        'trainer':              activity.get('trainer', False),
        'commute':              activity.get('commute', False),
        'workout_type':         activity.get('workout_type'),
        'map_summary_polyline': (activity.get('map') or {}).get('summary_polyline'),
        'start_latlng':         json.dumps(activity['start_latlng']) if activity.get('start_latlng') else None,
        'end_latlng':           json.dumps(activity['end_latlng']) if activity.get('end_latlng') else None,
    }


def _update_activity(cur, strava_activity_id, fields):
    """Write backfilled fields to strava_activity row."""
    cur.execute("""
        UPDATE strava_activity SET
            average_cadence      = %(average_cadence)s,
            average_temp         = %(average_temp)s,
            calories             = %(calories)s,
            pr_count             = %(pr_count)s,
            achievement_count    = %(achievement_count)s,
            gear_id              = %(gear_id)s,
            elev_high            = %(elev_high)s,
            elev_low             = %(elev_low)s,
            trainer              = %(trainer)s,
            commute              = %(commute)s,
            workout_type         = %(workout_type)s,
            map_summary_polyline = %(map_summary_polyline)s,
            start_latlng         = %(start_latlng)s,
            end_latlng           = %(end_latlng)s
        WHERE strava_activity_id = %(strava_activity_id)s
    """, {**fields, 'strava_activity_id': strava_activity_id})


def main():
    parser = argparse.ArgumentParser(description='Backfill Strava fields for matched brevet activities.')
    parser.add_argument('--dry-run', action='store_true', help='Print what would be updated without writing')
    args = parser.parse_args()

    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print('ERROR: DATABASE_URL environment variable not set')
        sys.exit(1)

    db_conn = psycopg2.connect(database_url)
    cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Fetch all matched brevet activities in scope, grouped by rider
    cur.execute("""
        SELECT
            sa.strava_activity_id,
            sa.rider_id,
            sa.average_cadence,
            r.name AS ride_name,
            r.date AS ride_date,
            sc.access_token,
            sc.refresh_token,
            sc.expires_at
        FROM strava_activity sa
        JOIN strava_ride_match srm ON srm.strava_activity_id = sa.strava_activity_id
        JOIN ride r ON r.id = srm.ride_id
        JOIN strava_connection sc ON sc.rider_id = sa.rider_id
        WHERE (
            r.date >= '2025-10-01'
            OR DATE_PART('year', r.date) IN (2022, 2023)
        )
          AND sa.average_cadence IS NULL
        ORDER BY sa.rider_id, r.date
    """)
    rows = cur.fetchall()

    if not rows:
        print('Nothing to backfill — all in-scope activities already have cadence data.')
        db_conn.close()
        return

    print(f'{"DRY RUN — " if args.dry_run else ""}Backfilling {len(rows)} activities...\n')

    updated = 0
    skipped = 0
    no_cadence = 0

    # Cache tokens per rider to avoid redundant refreshes
    token_cache = {}

    for row in rows:
        rider_id = row['rider_id']
        strava_id = row['strava_activity_id']

        if rider_id not in token_cache:
            token = _get_access_token(row, db_conn)
            if not token:
                print(f'  SKIP rider {rider_id} — could not obtain access token')
                skipped += 1
                continue
            token_cache[rider_id] = token

        access_token = token_cache[rider_id]

        print(f'  Fetching {strava_id} ({row["ride_name"]} {row["ride_date"]}) rider={rider_id}...', end=' ')

        if args.dry_run:
            print('[dry-run]')
            updated += 1
            continue

        activity = _fetch_activity_detail(strava_id, access_token)

        if activity is _TOKEN_EXPIRED:
            # Token expired mid-run — attempt one refresh then retry
            print('token expired, refreshing...', end=' ')
            token_cache.pop(rider_id, None)
            new_token = _refresh_token(row)
            if not new_token:
                print('refresh failed — skipping rider')
                skipped += 1
                time.sleep(CALL_DELAY)
                continue
            token_cache[rider_id] = new_token
            access_token = new_token
            activity = _fetch_activity_detail(strava_id, access_token)
            if activity is _TOKEN_EXPIRED or activity is None:
                print('still unauthorized — skipping rider')
                skipped += 1
                time.sleep(CALL_DELAY)
                continue

        if activity is None:
            print('not found')
            skipped += 1
            time.sleep(CALL_DELAY)
            continue

        fields = _extract_fields(activity)
        cadence = fields.get('average_cadence')
        if cadence is None:
            no_cadence += 1

        write_cur = db_conn.cursor()
        _update_activity(write_cur, strava_id, fields)
        db_conn.commit()

        cadence_str = f'{cadence:.0f} rpm' if cadence is not None else 'no cadence sensor'
        print(f'OK ({cadence_str})')
        updated += 1
        time.sleep(CALL_DELAY)

    db_conn.close()

    print(f'\n{"DRY RUN " if args.dry_run else ""}Summary:')
    print(f'  Updated:          {updated}')
    print(f'  Skipped:          {skipped}')
    print(f'  No cadence data:  {no_cadence}')
    print(f'  (riders without a cadence sensor will show — in the cohort cadence card)')


if __name__ == '__main__':
    main()
