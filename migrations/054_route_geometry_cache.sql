-- 054_route_geometry_cache.sql
-- Team Asha route-keyed elevation-track cache for the rpv2 plan-page profile.
--
-- The rpv2 plan page draws a gradient altitude profile from the RWGPS route track.
-- That track must NOT be fetched live on the plan render (TA-237: no live RWGPS on
-- the request path), so #534 read it from route_weather_cache.elevation_track. But
-- that cache is only warmed for UPCOMING events, so a plan for a past / reference
-- route showed "no route geometry for this plan" even though its RWGPS route exists.
--
-- Route geometry is DATE-INVARIANT, so it belongs in its own route-keyed cache rather
-- than piggybacked on the date-keyed weather cache. This table holds one downsampled
-- elevation track per RWGPS route id; the /api/cron/warm-plan-elevation cron populates
-- it for EVERY route referenced by a ride_plan (past and upcoming), and the plan render
-- reads it by route id. Strictly additive: a new table, no change to existing ones.

CREATE TABLE IF NOT EXISTS route_geometry_cache (
    route_id        BIGINT PRIMARY KEY,
    elevation_track JSONB,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
