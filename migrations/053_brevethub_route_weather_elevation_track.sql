-- 053_brevethub_route_weather_elevation_track.sql
-- BrevetHub along-route weather cache gains a cached elevation track for the
-- rpv2 plan-page gradient elevation profile.
--
-- The rpv2 plan page (brevethub/templates/plan.html) draws a live gradient
-- ALTITUDE profile with control/break overlays. The altitude points come from the
-- RWGPS route track (dist_m + elevation), which must NOT be fetched live on the
-- guest /plan render — the guest page reads only the cron-warmed cache (the TA-237
-- lesson: the guest page NEVER calls RWGPS/Open-Meteo live). The
-- warm-brevet-route-weather cron already fetches the full route (with per-point
-- elevation) to sample the forecast, so it now also persists a downsampled
-- elevation track here; the /plan render reads it straight from cache and builds
-- the profile in-process (no round-trip).
--
-- Strictly additive + idempotent + rp_*-only, like migrations 043/…/049: one
-- guarded ADD COLUMN on an rp_ table. Applying (or re-applying) it cannot alter any
-- Team Asha table; old code that predates the column simply ignores it, so it is
-- safe to apply ahead of the code deploy (the cron backfills the track on its next
-- run; until then the render degrades to an empty profile).

-- --------------------------------------------------------------------------- --
-- elevation_track — downsampled [{lat, lng, dist_m, e_m}, ...] route track (the
-- shared.live_radial.track_from_route output). build_elevation_profile consumes
-- dist_m + e_m. Nullable: a row warmed before this column existed reads NULL → the
-- render draws an empty profile; the cron fills it in on the next warm.
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_brevet_route_weather
    ADD COLUMN IF NOT EXISTS elevation_track JSONB;
