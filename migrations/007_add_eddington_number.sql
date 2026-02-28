-- ============================================================
-- Migration: Add Eddington Number Feature
-- Date: 2026-02-28
-- Purpose: Add Eddington Number tracking for Strava-connected riders
-- ============================================================
--
-- Eddington Number (E): The largest number E such that you have ridden
-- at least E miles on at least E different days.
--
-- Example: Eddington number of 50 means you've ridden 50+ miles on 50+ days
--
-- ============================================================

-- Add eddington_number columns to strava_connection table
ALTER TABLE strava_connection
ADD COLUMN IF NOT EXISTS eddington_number_miles INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS eddington_number_km INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS eddington_calculated_at TIMESTAMP;

-- Add helpful comments
COMMENT ON COLUMN strava_connection.eddington_number_miles IS 'Largest E where rider has ridden ≥E miles on ≥E days';
COMMENT ON COLUMN strava_connection.eddington_number_km IS 'Largest E where rider has ridden ≥E km on ≥E days';
COMMENT ON COLUMN strava_connection.eddington_calculated_at IS 'Last time Eddington number was calculated';

-- Create index for faster queries
CREATE INDEX IF NOT EXISTS idx_strava_connection_eddington
ON strava_connection(eddington_number_miles DESC);

-- ============================================================
-- VERIFICATION QUERIES
-- ============================================================
--
-- Check columns were added:
-- SELECT column_name, data_type, column_default
-- FROM information_schema.columns
-- WHERE table_name = 'strava_connection'
--   AND column_name LIKE 'eddington%';
--
-- ============================================================
-- ROLLBACK (if needed)
-- ============================================================
--
-- ALTER TABLE strava_connection
-- DROP COLUMN IF EXISTS eddington_number_miles,
-- DROP COLUMN IF EXISTS eddington_number_km,
-- DROP COLUMN IF EXISTS eddington_calculated_at;
--
-- DROP INDEX IF EXISTS idx_strava_connection_eddington;
--
-- ============================================================
