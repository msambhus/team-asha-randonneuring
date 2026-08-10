#!/usr/bin/env python3
"""Apply migration 083: RUSA membership cache columns on rp_rider.

Run: python3 migrations/apply_migration_083.py
Idempotent: uses ADD COLUMN IF NOT EXISTS.
"""

import os
import sys
from pathlib import Path

import psycopg2


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
    print('Migration 083: BrevetHub RUSA membership cache (rp_rider)')
    print('=' * 70)
    print()

    db_url = get_database_url()
    if not db_url:
        print('✗ Error: DATABASE_URL not found')
        print('  Set DATABASE_URL or add it to .env / .env.local.brevet')
        return False

    sql_file = Path(__file__).parent / '083_brevethub_rusa_membership_cache.sql'
    sql_content = sql_file.read_text()
    statements = [s.strip() for s in sql_content.split(';') if s.strip()]

    try:
        print('Connecting to database...', end=' ', flush=True)
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cursor = conn.cursor()
        print('✓ Connected')
        print()

        for stmt in statements:
            lines = [
                line for line in stmt.split('\n')
                if line.strip() and not line.strip().startswith('--')
            ]
            if not lines:
                continue
            label = lines[0].strip()[:60]
            print(f'  {label}...', end=' ', flush=True)
            cursor.execute(stmt)
            print('✓')

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'rp_rider'
              AND column_name IN (
                  'rusa_membership_expires', 'rusa_membership_checked_at'
              )
            ORDER BY column_name
            """
        )
        cols = [row[0] for row in cursor.fetchall()]
        print()
        print('Columns on rp_rider:', ', '.join(cols) if cols else '(none)')
        cursor.close()
        conn.close()
        print()
        print('✓ Migration 083 completed successfully!')
        return len(cols) == 2

    except psycopg2.Error as e:
        print(f'✗ Error: {e}')
        return False


if __name__ == '__main__':
    sys.exit(0 if apply_migration() else 1)
