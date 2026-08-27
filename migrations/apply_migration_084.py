#!/usr/bin/env python3
"""Apply migration 084: BrevetHub worker ride columns and ride_mode on signups.

Run: python3 migrations/apply_migration_084.py
Idempotent: uses ADD COLUMN IF NOT EXISTS and guarded constraint add.
"""

import os
import sys
from pathlib import Path

import psycopg2

STATEMENTS = (
    """
    ALTER TABLE rp_brevet_event
        ADD COLUMN IF NOT EXISTS worker_ride_enabled BOOLEAN NOT NULL DEFAULT FALSE
    """,
    """
    ALTER TABLE rp_volunteer_slot
        ADD COLUMN IF NOT EXISTS allows_ride_on_event_day BOOLEAN NOT NULL DEFAULT FALSE
    """,
    """
    ALTER TABLE rp_event_signup
        ADD COLUMN IF NOT EXISTS ride_mode TEXT,
        ADD COLUMN IF NOT EXISTS ride_mode_ack_at TIMESTAMPTZ
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'rp_event_signup_ride_mode_check'
        ) THEN
            ALTER TABLE rp_event_signup
                ADD CONSTRAINT rp_event_signup_ride_mode_check
                CHECK (ride_mode IS NULL OR ride_mode IN ('event_day', 'worker_ride'));
        END IF;
    END $$
    """,
)


def get_database_url():
    root = Path(__file__).parent.parent
    for name in ('.env', '.env.local.brevet'):
        env_file = root / name
        if not env_file.exists():
            continue
        with open(env_file) as f:
            for line in f:
                if line.startswith('DATABASE_URL='):
                    return line.strip().split('=', 1)[1]
    return os.getenv('DATABASE_URL')


def apply_migration():
    print('=' * 70)
    print('Migration 084: BrevetHub worker ride')
    print('=' * 70)
    print()

    db_url = get_database_url()
    if not db_url:
        print('✗ Error: DATABASE_URL not found')
        print('  Set DATABASE_URL or add it to .env / .env.local.brevet')
        return False

    try:
        print('Connecting to database...', end=' ', flush=True)
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cursor = conn.cursor()
        print('✓ Connected')
        print()

        labels = (
            'rp_brevet_event.worker_ride_enabled',
            'rp_volunteer_slot.allows_ride_on_event_day',
            'rp_event_signup ride_mode columns',
            'rp_event_signup_ride_mode_check constraint',
        )
        for label, stmt in zip(labels, STATEMENTS):
            print(f'  {label}...', end=' ', flush=True)
            cursor.execute(stmt)
            print('✓')

        cursor.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE (table_name = 'rp_brevet_event' AND column_name = 'worker_ride_enabled')
               OR (table_name = 'rp_volunteer_slot' AND column_name = 'allows_ride_on_event_day')
               OR (table_name = 'rp_event_signup' AND column_name IN ('ride_mode', 'ride_mode_ack_at'))
            ORDER BY table_name, column_name
            """
        )
        cols = cursor.fetchall()
        cursor.execute(
            """
            SELECT 1 FROM pg_constraint
            WHERE conname = 'rp_event_signup_ride_mode_check'
            """
        )
        has_check = cursor.fetchone() is not None
        cursor.close()
        conn.close()

        print()
        for table_name, column_name in cols:
            print(f'  ✓ {table_name}.{column_name}')
        print(f'  ✓ constraint rp_event_signup_ride_mode_check: {"yes" if has_check else "no"}')
        print()

        ok = len(cols) == 4 and has_check
        if ok:
            print('✓ Migration 084 completed successfully!')
        else:
            print('✗ Migration 084 verification failed')
        return ok

    except psycopg2.Error as e:
        print(f'✗ Error: {e}')
        return False


if __name__ == '__main__':
    sys.exit(0 if apply_migration() else 1)
