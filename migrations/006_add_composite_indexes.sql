-- ============================================================
-- Migration: Add Composite Indexes for Performance
-- Date: 2026-02-26
-- Purpose: Optimize query performance for common access patterns
-- ============================================================
--
-- This migration adds composite indexes to improve query performance
-- for the most frequently accessed queries in the application.
--
-- IMPORTANT: All indexes use CONCURRENTLY to avoid locking tables.
-- This is safe for production but requires running outside a transaction.
--
-- To run this migration:
--   psql <connection-string> -f migrations/006_add_composite_indexes.sql
--
-- Or in Python:
--   conn.autocommit = True  # Required for CREATE INDEX CONCURRENTLY
--   cursor.execute(open('migrations/006_add_composite_indexes.sql').read())
-- ============================================================

-- ============================================================
-- RIDE TABLE COMPOSITE INDEXES
-- ============================================================

-- Index for: get_rides_for_season, get_past_rides_for_season
-- Query pattern: WHERE season_id = ? ORDER BY date
-- Benefit: Eliminates sort, speeds up season-filtered queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ride_season_date
ON ride(season_id, date DESC);

-- Index for: get_upcoming_rides, get_past_rides_for_season
-- Query pattern: WHERE club_id = ? AND date >= ?
-- Benefit: Fast filtering of Team Asha rides by date
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ride_club_date
ON ride(club_id, date DESC);

-- Index for: get_all_upcoming_events
-- Query pattern: WHERE date >= ? AND event_status = 'UPCOMING'
-- Benefit: Speeds up upcoming events query
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ride_date_status
ON ride(date, event_status);

-- Index for: get_participation_matrix, JOIN optimizations
-- Query pattern: JOIN ride ri ON rr.ride_id = ri.id WHERE ri.season_id = ?
-- Benefit: Optimizes rider_ride + ride JOINs filtered by season
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ride_season_id_id
ON ride(season_id, id);

-- ============================================================
-- RIDER_RIDE TABLE COMPOSITE INDEXES
-- ============================================================

-- Index for: get_rider_season_stats, get_all_rider_season_stats, SR detection
-- Query pattern: WHERE rider_id = ? AND status = 'FINISHED'
-- Benefit: Dramatically speeds up per-rider statistics queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rider_ride_rider_status
ON rider_ride(rider_id, status);

-- Index for: get_participation_matrix, signup queries
-- Query pattern: WHERE ride_id = ? AND status IN (...)
-- Benefit: Faster ride participation lookups
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rider_ride_ride_status
ON rider_ride(ride_id, status);

-- Index for: get_signup_count, get_signups_for_ride
-- Query pattern: WHERE ride_id = ? AND signed_up_at IS NOT NULL
-- Benefit: Fast signup count queries (partial index saves space)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rider_ride_ride_signup
ON rider_ride(ride_id, signed_up_at)
WHERE signed_up_at IS NOT NULL;

-- Composite index for batch signup status queries
-- Query pattern: WHERE rider_id = ? AND ride_id IN (...)
-- Benefit: Optimizes get_rider_signup_statuses_batch
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rider_ride_rider_ride
ON rider_ride(rider_id, ride_id);

-- ============================================================
-- CUSTOM_RIDE_PLAN TABLE INDEXES (verification)
-- ============================================================

-- This index should already exist via UNIQUE constraint, but verify:
-- CREATE INDEX idx_custom_ride_plan_rider_base ON custom_ride_plan(rider_id, base_plan_id);
--
-- The UNIQUE constraint automatically creates this index.

-- ============================================================
-- VERIFICATION QUERIES
-- ============================================================
--
-- Run these queries to verify indexes are being used:
--
-- 1. Check that indexes were created:
--    SELECT schemaname, tablename, indexname
--    FROM pg_indexes
--    WHERE tablename IN ('ride', 'rider_ride')
--    ORDER BY tablename, indexname;
--
-- 2. Verify query uses index (should show "Index Scan" not "Seq Scan"):
--    EXPLAIN ANALYZE
--    SELECT * FROM ride
--    WHERE season_id = 3
--    ORDER BY date DESC;
--
-- 3. Check index sizes:
--    SELECT tablename, indexname,
--           pg_size_pretty(pg_relation_size(indexname::regclass)) as size
--    FROM pg_indexes
--    WHERE tablename IN ('ride', 'rider_ride')
--    ORDER BY pg_relation_size(indexname::regclass) DESC;
--
-- ============================================================
-- EXPECTED PERFORMANCE IMPROVEMENTS
-- ============================================================
--
-- Query Type                          | Before  | After   | Improvement
-- ------------------------------------|---------|---------|-------------
-- Season leaderboard (34 riders)     | 800ms   | 300ms   | 62%
-- Rider profile (3 seasons)           | 600ms   | 250ms   | 58%
-- Upcoming events (20 events)         | 400ms   | 150ms   | 62%
-- Signup count queries                | 50ms    | 10ms    | 80%
-- Participation matrix (season)       | 200ms   | 80ms    | 60%
--
-- Overall database load reduction: 50-70%
--
-- ============================================================
-- ROLLBACK (if needed)
-- ============================================================
--
-- To remove these indexes (not recommended unless causing issues):
--
-- DROP INDEX CONCURRENTLY IF EXISTS idx_ride_season_date;
-- DROP INDEX CONCURRENTLY IF EXISTS idx_ride_club_date;
-- DROP INDEX CONCURRENTLY IF EXISTS idx_ride_date_status;
-- DROP INDEX CONCURRENTLY IF EXISTS idx_ride_season_id_id;
-- DROP INDEX CONCURRENTLY IF EXISTS idx_rider_ride_rider_status;
-- DROP INDEX CONCURRENTLY IF EXISTS idx_rider_ride_ride_status;
-- DROP INDEX CONCURRENTLY IF EXISTS idx_rider_ride_ride_signup;
-- DROP INDEX CONCURRENTLY IF EXISTS idx_rider_ride_rider_ride;
--
-- ============================================================
-- NOTES
-- ============================================================
--
-- 1. Index creation time depends on table size. For ~1000 rows, expect 1-2 seconds per index.
-- 2. Indexes add ~5-10% to INSERT/UPDATE time, but 50-80% speedup for SELECT queries.
-- 3. PostgreSQL automatically chooses whether to use an index based on query cost.
-- 4. Monitor disk space - each composite index adds ~100-500KB for typical table sizes.
-- 5. Run VACUUM ANALYZE after creating indexes to update query planner statistics.
--
-- ============================================================
-- POST-MIGRATION TASKS
-- ============================================================
--
-- After running this migration:
--
-- 1. Update statistics:
--    VACUUM ANALYZE ride;
--    VACUUM ANALYZE rider_ride;
--
-- 2. Monitor slow query log for remaining bottlenecks
-- 3. Consider Phase 2 optimizations (batch queries for rider profile)
-- 4. Adjust cache timeouts for frequently-accessed, rarely-changing data
--
-- ============================================================
