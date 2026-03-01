-- ============================================================
-- Migration: Add backfill cursor to strava_connection
-- Date: 2026-03-01
-- Purpose: Track how far back Strava backfill has searched,
--          independent of whether activities were found.
--          Fixes backfill getting stuck on gaps in riding history.
-- ============================================================

ALTER TABLE strava_connection
ADD COLUMN IF NOT EXISTS backfill_cursor DATE;

COMMENT ON COLUMN strava_connection.backfill_cursor IS 'How far back the gradual backfill has searched (moves back 90 days per run)';
