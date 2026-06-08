#!/usr/bin/env python3
"""
Idempotent seed script: add the SFR brevet "Two Rock-Valley Ford 200km"
(2026-06-06) to the ride table as an upcoming current-season event.

Reuses the import-safe scraper helpers from update_rusa_events.py:
  - download_sfr_events()  fetches the live SFR spreadsheet
  - upsert_event()         resolves the SFR club + current season and writes
                           idempotently on (date, name)

If the live sheet is unreachable or the event is not yet listed, falls back to
a hardcoded event dict sourced from https://www.sfrandonneurs.org/ (date
06/06/2026, 200km ACP brevet, start 7:00, time limit 13.5 hrs).

Usage:
    python scripts/add_sfr_two_rock_valley_ford.py
"""

import os
import sys
from pathlib import Path

import psycopg2

# Add parent directory to path to import from project
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.update_rusa_events import download_sfr_events, upsert_event

# Target event identity (canonical name from sfrandonneurs.org)
TARGET_DATE = '2026-06-06'
TARGET_NAME = 'Two Rock-Valley Ford 200km'
SFR_REGION = 'San Francisco'

# Fallback event dict — includes every key upsert_event() reads, None where
# unknown. Used only if the live sheet is unreachable or the event is absent.
FALLBACK_EVENT = {
    'name': TARGET_NAME,
    'ride_type': 'ACP brevet',
    'date': TARGET_DATE,
    'distance_km': 200,
    'distance_miles': None,
    'elevation_ft': None,
    'rwgps_url': None,
    'start_time': '7:00',
    'time_limit_hours': 13.5,
    'start_location': None,
}


def _matches_target(event):
    """
    True if a scraped event is the Two Rock-Valley Ford 200km.

    Matched on the name markers only, not on an exact date string:
    ``download_sfr_events()`` stores the sheet's *raw* (non-ISO) "Event date"
    value, so an exact ISO comparison would never hit a real row. The sheet
    holds one Two Rock 200km per (current) season, so the name is a unique key
    — and adopting the sheet's exact name is precisely what keeps the seed and
    the scraper from writing two differently-named rows on the same date.
    """
    name = (event.get('name') or '').lower()
    return 'two rock' in name and '200k' in name


def find_target_event():
    """
    Return the target event from the live SFR sheet, or the hardcoded fallback.

    Sourcing the event from the sheet first keeps its name identical to what the
    scraper would write, so the (date, name) upsert stays idempotent across both
    tools. Only when the sheet is unreachable or the event is absent do we use
    the canonical hardcoded dict.
    """
    try:
        events = download_sfr_events()
    except Exception as e:  # network/parse failure — fall back
        print(f"⚠️  Could not fetch SFR sheet ({e}); using fallback event.")
        events = []

    for event in events:
        if _matches_target(event):
            print(f"✅ Found target event on SFR sheet: {event['name']} ({event['date']})")
            return event

    print("⚠️  Target event not found on SFR sheet; using fallback event.")
    return dict(FALLBACK_EVENT)


def main():
    print("=" * 60)
    print(f"Seeding SFR event: {TARGET_NAME} ({TARGET_DATE})")
    print("=" * 60)

    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("❌ DATABASE_URL environment variable not set")
        sys.exit(1)

    event = find_target_event()

    conn = psycopg2.connect(database_url)
    cursor = conn.cursor()
    try:
        action = upsert_event(cursor, SFR_REGION, event)
        # Only persist a clean write. 'error' (no club/season resolved) and
        # 'filtered' (no standard ACP time limit) mean nothing was written —
        # don't commit a partial transaction or report success.
        if action in ('inserted', 'updated', 'skipped'):
            conn.commit()
    finally:
        conn.close()

    symbol = {'inserted': '+', 'updated': '✓', 'skipped': '⊘', 'filtered': '⊗'}.get(action, '✗')
    print(f"  {symbol} {event['name']} ({event['date']}) [{action}]")
    print("=" * 60)
    if action in ('inserted', 'updated', 'skipped'):
        print(f"✅ Done! Action: {action}")
        print("=" * 60)
    else:
        print(f"❌ Failed! Upsert returned: {action}")
        print("=" * 60)
        sys.exit(1)


if __name__ == '__main__':
    main()
