#!/usr/bin/env python3
"""
Query Performance Benchmark

Run this script before and after applying the migration to see the
actual performance improvement.

Usage:
    # Before migration
    python migrations/benchmark_queries.py > results_before.txt

    # Apply migration
    python migrations/apply_migration_006.py

    # After migration
    python migrations/benchmark_queries.py > results_after.txt

    # Compare results
    diff results_before.txt results_after.txt
"""

import sys
import time
from pathlib import Path
from datetime import date

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_db
from models import (
    get_current_season,
    get_rides_for_season,
    get_riders_for_season,
    get_participation_matrix,
    get_all_rider_season_stats,
    get_upcoming_rides,
    get_all_upcoming_events,
    get_rider_by_rusa,
    get_rider_participation,
    get_rider_season_stats,
    detect_sr_for_rider_season,
)


def time_query(description, func, *args, **kwargs):
    """Time a query and return the result and elapsed time."""
    start = time.time()
    try:
        result = func(*args, **kwargs)
        elapsed = (time.time() - start) * 1000  # Convert to milliseconds
        return result, elapsed, None
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        return None, elapsed, str(e)


def run_benchmark():
    """Run a series of benchmark queries."""
    print("=" * 70)
    print("Query Performance Benchmark")
    print("=" * 70)
    print(f"Date: {date.today()}")
    print()

    # Get current season for benchmarks
    current_season = get_current_season()
    if not current_season:
        print("Error: No current season found")
        return

    season_id = current_season['id']
    print(f"Using season: {current_season['name']} (ID: {season_id})")
    print()

    results = []

    # Benchmark 1: Season rides query
    print("1. Testing get_rides_for_season()...", end=' ', flush=True)
    rides, elapsed, error = time_query(
        "Season rides",
        get_rides_for_season,
        season_id
    )
    results.append(("get_rides_for_season", elapsed, error, len(rides) if rides else 0))
    print(f"{elapsed:.1f}ms ({len(rides) if rides else 0} rides)")

    # Benchmark 2: Season riders query
    print("2. Testing get_riders_for_season()...", end=' ', flush=True)
    riders, elapsed, error = time_query(
        "Season riders",
        get_riders_for_season,
        season_id
    )
    results.append(("get_riders_for_season", elapsed, error, len(riders) if riders else 0))
    print(f"{elapsed:.1f}ms ({len(riders) if riders else 0} riders)")

    # Benchmark 3: Participation matrix
    print("3. Testing get_participation_matrix()...", end=' ', flush=True)
    matrix, elapsed, error = time_query(
        "Participation matrix",
        get_participation_matrix,
        season_id
    )
    results.append(("get_participation_matrix", elapsed, error, len(matrix) if matrix else 0))
    print(f"{elapsed:.1f}ms ({len(matrix) if matrix else 0} riders)")

    # Benchmark 4: All rider season stats (batch query)
    print("4. Testing get_all_rider_season_stats()...", end=' ', flush=True)
    stats, elapsed, error = time_query(
        "All rider season stats",
        get_all_rider_season_stats,
        season_id
    )
    results.append(("get_all_rider_season_stats", elapsed, error, len(stats) if stats else 0))
    print(f"{elapsed:.1f}ms ({len(stats) if stats else 0} riders)")

    # Benchmark 5: Upcoming rides
    print("5. Testing get_upcoming_rides()...", end=' ', flush=True)
    upcoming, elapsed, error = time_query(
        "Upcoming rides",
        get_upcoming_rides
    )
    results.append(("get_upcoming_rides", elapsed, error, len(upcoming) if upcoming else 0))
    print(f"{elapsed:.1f}ms ({len(upcoming) if upcoming else 0} rides)")

    # Benchmark 6: All upcoming events
    print("6. Testing get_all_upcoming_events()...", end=' ', flush=True)
    events, elapsed, error = time_query(
        "All upcoming events",
        get_all_upcoming_events
    )
    results.append(("get_all_upcoming_events", elapsed, error, len(events) if events else 0))
    print(f"{elapsed:.1f}ms ({len(events) if events else 0} events)")

    # Benchmark 7: Single rider profile (simulate N+1 problem)
    if riders and len(riders) > 0:
        # Get first rider's RUSA ID
        test_rider = riders[0]
        rider_rusa_id = test_rider.get('rusa_id')

        if rider_rusa_id:
            print(f"7. Testing rider profile queries (RUSA {rider_rusa_id})...", end=' ', flush=True)

            # Simulate what happens on rider profile page
            start = time.time()

            rider, _, _ = time_query("Get rider", get_rider_by_rusa, rider_rusa_id)

            if rider:
                rider_id = rider['id']

                # These queries run in a loop for each season (N+1 problem)
                participation, _, _ = time_query("Participation", get_rider_participation, rider_id, season_id)
                stats, _, _ = time_query("Stats", get_rider_season_stats, rider_id, season_id)
                sr, _, _ = time_query("SR", detect_sr_for_rider_season, rider_id, season_id, True)

            total_elapsed = (time.time() - start) * 1000
            results.append(("rider_profile_simulation", total_elapsed, None, 1))
            print(f"{total_elapsed:.1f}ms")

    print()
    print("=" * 70)
    print("Benchmark Summary")
    print("=" * 70)
    print()

    # Calculate statistics
    total_time = sum(r[1] for r in results if r[2] is None)
    avg_time = total_time / len([r for r in results if r[2] is None])

    print(f"{'Query':<35} {'Time (ms)':<12} {'Rows':<8} {'Status'}")
    print("-" * 70)

    for name, elapsed, error, rows in results:
        status = "✓" if error is None else "✗"
        error_msg = f" ({error[:30]}...)" if error else ""
        print(f"{name:<35} {elapsed:>10.1f}ms {rows:>6} {status}{error_msg}")

    print("-" * 70)
    print(f"{'Total':<35} {total_time:>10.1f}ms")
    print(f"{'Average':<35} {avg_time:>10.1f}ms")
    print()

    # Performance rating
    if avg_time < 50:
        rating = "Excellent ⭐⭐⭐⭐⭐"
    elif avg_time < 100:
        rating = "Very Good ⭐⭐⭐⭐"
    elif avg_time < 200:
        rating = "Good ⭐⭐⭐"
    elif avg_time < 400:
        rating = "Fair ⭐⭐"
    else:
        rating = "Needs Optimization ⭐"

    print(f"Performance Rating: {rating}")
    print()

    # Check index usage
    print("=" * 70)
    print("Index Usage Check")
    print("=" * 70)
    print()

    conn = get_db()
    cursor = conn.cursor()

    # Check if our composite indexes exist
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM pg_indexes
        WHERE tablename IN ('ride', 'rider_ride')
          AND indexname LIKE '%season%'
           OR indexname LIKE '%rider_status%'
    """)

    index_count = cursor.fetchone()[0]

    if index_count >= 4:
        print("✓ Composite indexes detected!")
        print(f"  Found {index_count} optimized indexes")
    else:
        print("⚠️  Composite indexes NOT detected")
        print("  Run: python migrations/apply_migration_006.py")

    print()
    print("=" * 70)

    return results


def compare_benchmarks(before_file, after_file):
    """Compare two benchmark result files."""
    # This is a placeholder for future enhancement
    print("Comparison feature coming soon!")
    print(f"Before: {before_file}")
    print(f"After: {after_file}")


if __name__ == '__main__':
    try:
        if len(sys.argv) > 2 and sys.argv[1] == 'compare':
            compare_benchmarks(sys.argv[2], sys.argv[3])
        else:
            run_benchmark()
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
