-- 055_brevethub_route_geometry_cache.sql
-- BrevetHub route-keyed elevation-track cache for the rpv2 plan-page profile.
--
-- Same fix as the Team Asha 054 migration, for BrevetHub. The rpv2 /plan page draws a
-- gradient altitude profile from the RWGPS route track, read from cache only (the guest
-- page NEVER fetches RWGPS live). #534 stored that track on rp_brevet_route_weather,
-- which is keyed by EVENT and only warmed for upcoming events, so a plan for a past /
-- non-warmed event showed no profile even though its RWGPS route exists.
--
-- Route geometry is DATE-INVARIANT and route-scoped, so it gets its own route-keyed
-- rp_ table. The /cron/warm-plan-elevation cron populates it for every route referenced
-- by an rp_brevet_route_plan; the render reads it by the plan rwgps_route_id. Strictly
-- additive + idempotent + rp_*-only: one guarded CREATE on an rp_ table, no change to
-- any Team Asha table.

CREATE TABLE IF NOT EXISTS rp_route_geometry_cache (
    route_id        BIGINT PRIMARY KEY,
    elevation_track JSONB,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
