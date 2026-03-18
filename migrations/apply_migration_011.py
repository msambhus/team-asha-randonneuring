#!/usr/bin/env python3
"""
Standalone migration script for personality coaching tables.
Creates personality_profile, gear_preference, coach_assignment, coaching_guardrail tables.

Run: python3 migrations/apply_migration_011.py
Idempotent: safe to run multiple times (uses IF NOT EXISTS).
"""

import os
import sys
import psycopg2
from pathlib import Path


def get_database_url():
    """Get database URL from environment."""
    env_file = Path(__file__).parent.parent / '.env'
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                if line.startswith('DATABASE_URL='):
                    return line.strip().split('=', 1)[1]
    return os.getenv('DATABASE_URL')


def apply_migration():
    """Apply migration 011: personality coaching tables."""
    print("=" * 70)
    print("Migration 011: Personality Coaching Tables")
    print("=" * 70)
    print()

    db_url = get_database_url()
    if not db_url:
        print("✗ Error: DATABASE_URL not found")
        print("  Set DATABASE_URL environment variable or create .env file")
        return False

    if 'supabase' in db_url.lower():
        print("⚠️  Detected Supabase database")
        print()

    try:
        print("Connecting to database...", end=' ', flush=True)
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cursor = conn.cursor()
        print("✓ Connected")
        print()

        # Read the SQL file
        sql_file = Path(__file__).parent / '011_personality_coaching_tables.sql'
        sql_content = sql_file.read_text()

        # Split on semicolons and execute each statement
        statements = [s.strip() for s in sql_content.split(';') if s.strip()]

        success_count = 0
        skip_count = 0
        error_count = 0

        print("Executing statements...")
        print()

        for stmt in statements:
            # Skip pure comments
            lines = [l for l in stmt.split('\n') if l.strip() and not l.strip().startswith('--')]
            if not lines:
                continue

            # Extract a short description from the statement
            first_line = lines[0].strip()[:60]
            print(f"  {first_line}...", end=' ', flush=True)

            try:
                cursor.execute(stmt)
                print("✓")
                success_count += 1
            except psycopg2.Error as e:
                if 'already exists' in str(e).lower():
                    print("⊘ Already exists")
                    skip_count += 1
                else:
                    print(f"✗ Error: {e}")
                    error_count += 1

        print()
        print("=" * 70)
        print("Migration Summary:")
        print(f"  Executed:      {success_count}")
        print(f"  Skipped:       {skip_count} (already exist)")
        print(f"  Errors:        {error_count}")
        print("=" * 70)
        print()

        cursor.close()
        conn.close()

        if error_count > 0:
            print("⚠️  Migration completed with errors")
            return False
        else:
            print("✓ Migration completed successfully!")
            return True

    except psycopg2.OperationalError as e:
        print(f"✗ Connection Error: {e}")
        print()
        print("This usually means:")
        print("  1. Database is not accessible (network issue)")
        print("  2. Wrong credentials in DATABASE_URL")
        print("  3. Supabase requires password in connection string")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    try:
        success = apply_migration()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nMigration cancelled by user")
        sys.exit(1)
