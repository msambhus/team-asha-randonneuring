-- Migration 021: richer per-point fields for live telemetry.
-- All nullable — populated only when the source (Garmin fitness data / browser
-- geolocation) provides them. Applied manually / via Supabase MCP after merge.

ALTER TABLE rider_live_position ADD COLUMN IF NOT EXISTS speed      NUMERIC;  -- m/s
ALTER TABLE rider_live_position ADD COLUMN IF NOT EXISTS heart_rate INTEGER;  -- bpm
ALTER TABLE rider_live_position ADD COLUMN IF NOT EXISTS power      INTEGER;  -- watts
ALTER TABLE rider_live_position ADD COLUMN IF NOT EXISTS cadence    INTEGER;  -- rpm
