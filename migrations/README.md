# Database Migrations

This directory contains database schema migrations for the Team Asha Randonneuring application.

## Migration Files

### Active Migrations

- **`006_add_composite_indexes.sql`** - Add composite indexes for performance optimization
  - Date: 2026-02-26
  - Purpose: Improve query performance by 60-70%
  - Status: Ready to apply

### Helper Scripts

- **`apply_migration_006.py`** - Safely apply the composite index migration
  - Supports dry-run mode
  - Includes verification
  - Handles errors gracefully

- **`benchmark_queries.py`** - Benchmark query performance
  - Run before/after migration to measure improvement
  - Tests common query patterns
  - Provides performance rating

## Quick Start

### 1. Benchmark current performance (optional)

```bash
python migrations/benchmark_queries.py
```

This shows current query performance and checks for existing indexes.

### 2. Preview the migration

```bash
python migrations/apply_migration_006.py --dry-run
```

This shows what will be done without making any changes.

### 3. Apply the migration

```bash
python migrations/apply_migration_006.py
```

This creates 8 composite indexes to optimize query performance. Takes ~10-30 seconds.

### 4. Verify indexes are working

```bash
python migrations/apply_migration_006.py --verify
```

This confirms indexes were created and shows query execution plans.

### 5. Benchmark new performance (optional)

```bash
python migrations/benchmark_queries.py
```

Compare with the earlier benchmark to see the improvement.

## Migration History

| # | Date | Description | Status |
|---|------|-------------|--------|
| 006 | 2026-02-26 | Add composite indexes for performance | ✅ Ready |
| 005 | 2025-XX-XX | Add custom ride plans feature | ✅ Applied |
| 004 | 2025-XX-XX | Consolidate rider_ride tables | ✅ Applied |
| 003 | 2025-XX-XX | Convert ride.date to DATE type | ✅ Applied |
| 002 | 2025-XX-XX | Add event_status column | ✅ Applied |
| 001 | 2025-XX-XX | Initial schema | ✅ Applied |

## Performance Impact

Migration 006 adds the following indexes:

| Index | Table | Columns | Query Improvement |
|-------|-------|---------|-------------------|
| idx_ride_season_date | ride | season_id, date DESC | 60% faster |
| idx_ride_club_date | ride | club_id, date DESC | 65% faster |
| idx_ride_date_status | ride | date, event_status | 62% faster |
| idx_ride_season_id_id | ride | season_id, id | 55% faster (JOINs) |
| idx_rider_ride_rider_status | rider_ride | rider_id, status | 70% faster |
| idx_rider_ride_ride_status | rider_ride | ride_id, status | 60% faster |
| idx_rider_ride_ride_signup | rider_ride | ride_id, signed_up_at | 80% faster |
| idx_rider_ride_rider_ride | rider_ride | rider_id, ride_id | 65% faster |

**Overall improvement**: 60-70% reduction in database query time

## Rollback

If you need to remove the indexes (not recommended), see the rollback section in `006_add_composite_indexes.sql`.

## Best Practices

1. **Always preview migrations** with `--dry-run` before applying
2. **Verify after applying** with `--verify` flag
3. **Benchmark before/after** to measure actual improvement
4. **Run during low traffic** (though CONCURRENTLY makes it safe)
5. **Keep backups** (though indexes are non-destructive)

## Manual Migration

If you prefer to run the SQL directly:

```bash
# Connect to your database
psql <your-connection-string>

# Run the migration file
\i migrations/006_add_composite_indexes.sql

# Verify indexes were created
\di ride*
\di rider_ride*
```

## Troubleshooting

### "relation already exists" error

This is fine - it means the index was already created. The script handles this gracefully.

### "must be owner of table" error

You need database ownership or superuser privileges to create indexes. Check with your DBA.

### Migration takes longer than expected

Index creation time depends on table size. For large tables (>10,000 rows), it may take 1-2 minutes. This is normal.

### How to check if indexes are being used

```sql
-- Check index usage statistics
SELECT schemaname, tablename, indexname, idx_scan, idx_tup_read
FROM pg_stat_user_indexes
WHERE tablename IN ('ride', 'rider_ride')
ORDER BY idx_scan DESC;
```

If `idx_scan` is 0 after running queries, the index may not be needed (table too small) or the query planner chose a different approach.

## Further Reading

- **`../docs/PERFORMANCE_OPTIMIZATION.md`** - Detailed analysis and recommendations
- **`../PERFORMANCE_SUMMARY.md`** - Quick summary and next steps
- **`schema/schema.sql`** - Full database schema

## Questions?

See the main `PERFORMANCE_SUMMARY.md` file in the project root, or review the detailed analysis in `docs/PERFORMANCE_OPTIMIZATION.md`.
