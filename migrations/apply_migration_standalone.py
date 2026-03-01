#!/usr/bin/env python3
"""
Standalone migration script that doesn't require Flask app context.
"""

import os
import sys
import psycopg2
from pathlib import Path


def get_database_url():
    """Get database URL from environment."""
    # Try to load from .env file
    env_file = Path(__file__).parent.parent / '.env'
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                if line.startswith('DATABASE_URL='):
                    return line.strip().split('=', 1)[1]

    # Try environment variable
    return os.getenv('DATABASE_URL')


def apply_migration():
    """Apply the migration using direct psycopg2 connection."""
    print("=" * 70)
    print("Migration 006: Add Composite Indexes for Performance")
    print("=" * 70)
    print()

    # Get database URL
    db_url = get_database_url()
    if not db_url:
        print("✗ Error: DATABASE_URL not found")
        print("  Set DATABASE_URL environment variable or create .env file")
        return False

    # Check if using Supabase (might not be accessible locally)
    if 'supabase' in db_url.lower():
        print("⚠️  Detected Supabase database")
        print("   This requires network access to Supabase")
        print()

    try:
        print("Connecting to database...", end=' ', flush=True)
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cursor = conn.cursor()
        print("✓ Connected")
        print()

        # List of indexes to create
        indexes = [
            ("idx_ride_season_date", "ride", "season_id, date DESC"),
            ("idx_ride_club_date", "ride", "club_id, date DESC"),
            ("idx_ride_date_status", "ride", "date, event_status"),
            ("idx_ride_season_id_id", "ride", "season_id, id"),
            ("idx_rider_ride_rider_status", "rider_ride", "rider_id, status"),
            ("idx_rider_ride_ride_status", "rider_ride", "ride_id, status"),
            ("idx_rider_ride_rider_ride", "rider_ride", "rider_id, ride_id"),
        ]

        # Special case: partial index
        indexes_special = [
            ("idx_rider_ride_ride_signup", "rider_ride",
             "ride_id, signed_up_at", "WHERE signed_up_at IS NOT NULL"),
        ]

        success_count = 0
        skip_count = 0
        error_count = 0

        print("Creating indexes...")
        print()

        # Create regular indexes
        for index_name, table_name, columns in indexes:
            try:
                sql = f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} ON {table_name}({columns})"
                print(f"  {index_name}...", end=' ', flush=True)
                cursor.execute(sql)
                print("✓ Created")
                success_count += 1
            except psycopg2.Error as e:
                if 'already exists' in str(e).lower():
                    print("⊘ Already exists")
                    skip_count += 1
                else:
                    print(f"✗ Error: {e}")
                    error_count += 1

        # Create special partial index
        for index_name, table_name, columns, condition in indexes_special:
            try:
                sql = f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} ON {table_name}({columns}) {condition}"
                print(f"  {index_name}...", end=' ', flush=True)
                cursor.execute(sql)
                print("✓ Created")
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
        print(f"  Created:       {success_count}")
        print(f"  Skipped:       {skip_count} (already exist)")
        print(f"  Errors:        {error_count}")
        print("=" * 70)
        print()

        # Update statistics if we created any indexes
        if success_count > 0:
            print("Updating table statistics...")
            cursor.execute("VACUUM ANALYZE ride")
            cursor.execute("VACUUM ANALYZE rider_ride")
            print("✓ Statistics updated")
            print()

        # Show index sizes
        print("Index Sizes:")
        cursor.execute("""
            SELECT tablename, indexname,
                   pg_size_pretty(pg_relation_size(indexname::regclass)) as size
            FROM pg_indexes
            WHERE tablename IN ('ride', 'rider_ride')
              AND indexname LIKE 'idx_%'
            ORDER BY pg_relation_size(indexname::regclass) DESC
            LIMIT 10
        """)
        for table, index, size in cursor.fetchall():
            print(f"  {index:<40} {size:>10}")

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
        print()
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
