#!/usr/bin/env python3
"""
Apply Migration 006: Add Composite Indexes for Performance

This script safely applies the composite index migration to improve
query performance across the application.

Usage:
    python migrations/apply_migration_006.py [--dry-run] [--verify]

Options:
    --dry-run   Show what would be done without making changes
    --verify    Verify indexes exist and show query plans
"""

import sys
import os
import argparse
from pathlib import Path

# Add parent directory to path to import db module
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_db


def read_migration_sql():
    """Read the migration SQL file."""
    migration_path = Path(__file__).parent / '006_add_composite_indexes.sql'
    with open(migration_path) as f:
        return f.read()


def parse_sql_statements(sql):
    """Extract CREATE INDEX statements from SQL file."""
    statements = []
    for line in sql.split('\n'):
        line = line.strip()
        if line.startswith('CREATE INDEX CONCURRENTLY'):
            # Accumulate multi-line statements
            statements.append(line)
    return statements


def apply_migration(dry_run=False):
    """Apply the migration to add composite indexes."""
    print("=" * 70)
    print("Migration 006: Add Composite Indexes for Performance")
    print("=" * 70)
    print()

    conn = get_db()

    # Must use autocommit for CREATE INDEX CONCURRENTLY
    original_autocommit = conn.autocommit
    conn.autocommit = True

    cursor = conn.cursor()

    # Read migration SQL
    migration_sql = read_migration_sql()
    statements = parse_sql_statements(migration_sql)

    print(f"Found {len(statements)} index creation statements\n")

    if dry_run:
        print("DRY RUN MODE - No changes will be made\n")
        print("Statements to execute:")
        for i, stmt in enumerate(statements, 1):
            print(f"\n{i}. {stmt[:80]}...")
        print()
        return

    # Check existing indexes
    print("Checking existing indexes...")
    cursor.execute("""
        SELECT tablename, indexname
        FROM pg_indexes
        WHERE tablename IN ('ride', 'rider_ride', 'custom_ride_plan')
        ORDER BY tablename, indexname
    """)
    existing_indexes = cursor.fetchall()

    print(f"Found {len(existing_indexes)} existing indexes:")
    for table, index in existing_indexes:
        print(f"  - {table}.{index}")
    print()

    # Apply migration
    print("Applying migration...")
    print("(This may take 10-30 seconds depending on table size)\n")

    success_count = 0
    skip_count = 0
    error_count = 0

    for i, stmt in enumerate(statements, 1):
        # Extract index name for reporting
        index_name = None
        if 'idx_' in stmt:
            parts = stmt.split('idx_')
            if len(parts) > 1:
                index_name = 'idx_' + parts[1].split()[0]

        try:
            print(f"{i}/{len(statements)} Creating {index_name}...", end=' ', flush=True)
            cursor.execute(stmt)
            print("✓ Created")
            success_count += 1
        except Exception as e:
            error_msg = str(e)
            if 'already exists' in error_msg.lower():
                print("⊘ Already exists")
                skip_count += 1
            else:
                print(f"✗ Error: {error_msg}")
                error_count += 1

    print()
    print("=" * 70)
    print("Migration Summary:")
    print(f"  Created:       {success_count}")
    print(f"  Skipped:       {skip_count} (already exist)")
    print(f"  Errors:        {error_count}")
    print("=" * 70)
    print()

    # Update statistics
    if success_count > 0:
        print("Updating table statistics...")
        cursor.execute("VACUUM ANALYZE ride")
        cursor.execute("VACUUM ANALYZE rider_ride")
        print("✓ Statistics updated\n")

    # Restore original autocommit setting
    conn.autocommit = original_autocommit

    if error_count > 0:
        print("⚠️  Migration completed with errors. Please review above.")
        return False
    else:
        print("✓ Migration completed successfully!")
        return True


def verify_indexes():
    """Verify that indexes were created and show query plans."""
    print("=" * 70)
    print("Index Verification")
    print("=" * 70)
    print()

    conn = get_db()
    cursor = conn.cursor()

    # List all composite indexes
    print("1. Checking composite indexes on 'ride' table:\n")
    cursor.execute("""
        SELECT indexname,
               pg_size_pretty(pg_relation_size(indexname::regclass)) as size
        FROM pg_indexes
        WHERE tablename = 'ride'
          AND indexname LIKE 'idx_ride_%'
        ORDER BY indexname
    """)

    ride_indexes = cursor.fetchall()
    if ride_indexes:
        for index, size in ride_indexes:
            print(f"  ✓ {index:40} ({size})")
    else:
        print("  ⚠️  No composite indexes found!")

    print("\n2. Checking composite indexes on 'rider_ride' table:\n")
    cursor.execute("""
        SELECT indexname,
               pg_size_pretty(pg_relation_size(indexname::regclass)) as size
        FROM pg_indexes
        WHERE tablename = 'rider_ride'
          AND indexname LIKE 'idx_rider_ride_%'
        ORDER BY indexname
    """)

    rider_ride_indexes = cursor.fetchall()
    if rider_ride_indexes:
        for index, size in rider_ride_indexes:
            print(f"  ✓ {index:40} ({size})")
    else:
        print("  ⚠️  No composite indexes found!")

    # Show example query plan
    print("\n3. Testing index usage with EXPLAIN:\n")
    print("Query: Get rides for season 3 ordered by date")
    print("-" * 70)

    cursor.execute("""
        EXPLAIN (FORMAT TEXT, ANALYZE FALSE)
        SELECT * FROM ride
        WHERE season_id = 3
        ORDER BY date DESC
    """)

    plan = cursor.fetchall()
    for row in plan:
        print(f"  {row[0]}")

    # Check if index is being used
    plan_text = '\n'.join(row[0] for row in plan)
    if 'idx_ride_season_date' in plan_text or 'Index Scan' in plan_text:
        print("\n  ✓ Index is being used!")
    else:
        print("\n  ⚠️  Index may not be used (table might be too small)")

    print("\n" + "=" * 70)
    print("Verification complete!")
    print("=" * 70)
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Apply migration 006: Add composite indexes for performance'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without making changes'
    )
    parser.add_argument(
        '--verify',
        action='store_true',
        help='Verify indexes exist and show query plans'
    )

    args = parser.parse_args()

    try:
        if args.verify:
            verify_indexes()
        else:
            success = apply_migration(dry_run=args.dry_run)

            if success and not args.dry_run:
                print("\nRun with --verify to check index usage:")
                print("  python migrations/apply_migration_006.py --verify")

            sys.exit(0 if success else 1)

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
