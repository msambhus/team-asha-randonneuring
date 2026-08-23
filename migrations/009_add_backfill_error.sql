-- ============================================================
-- Migration: Track Strava backfill failures per rider
-- Purpose: Prevent one expired/invalid token from blocking the queue.
-- ============================================================

ALTER TABLE strava_connection
ADD COLUMN IF NOT EXISTS backfill_error TEXT;

COMMENT ON COLUMN strava_connection.backfill_error IS
  'Most recent automatic historical-backfill error; cleared after a successful sync';
