-- Migration 022: make live tracking per-ride
--
-- Problem: positions were stored per-rider only, and a rider's single Garmin
-- link was global — so a rider Going on rides A and B showed their live dot on
-- BOTH maps. Fix: tag every position with the ride it belongs to, and scope the
-- Garmin session to one active ride at a time.
--
--   rider_live_position.ride_id     — which ride this point belongs to (NULL = legacy)
--   rider_live_tracking.active_ride_id — the ride the rider's current Garmin link is for
--
-- The per-ride map query filters on rider_live_position.ride_id, so points no
-- longer leak across rides. Applied manually / via Supabase MCP after merge.

ALTER TABLE rider_live_position
    ADD COLUMN IF NOT EXISTS ride_id INTEGER REFERENCES ride(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_rider_live_position_ride_recorded
    ON rider_live_position (ride_id, recorded_at DESC);

ALTER TABLE rider_live_tracking
    ADD COLUMN IF NOT EXISTS active_ride_id INTEGER REFERENCES ride(id) ON DELETE SET NULL;
