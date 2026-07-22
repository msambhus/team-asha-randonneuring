-- 052_route_weather_elevation_track.sql
-- Team Asha along-route weather cache gains a cached elevation track for the
-- rpv2 plan-page gradient elevation profile.
--
-- The rpv2 plan page (ride_plan_detail_v2.html) draws a live gradient ALTITUDE
-- profile with control/break overlays. The altitude points come from the RWGPS
-- route track (dist_m + elevation), which must NOT be fetched live on the plan
-- render — the plan page reads only cron-warmed caches (the TA-237 lesson: no
-- live RWGPS/Open-Meteo on the request path). The fetch-route-weather cron already
-- fetches the full route (with per-point elevation) to sample the forecast, so it
-- now also persists a downsampled elevation track here; the plan render reads it
-- straight from cache and builds the profile in-process (no round-trip).
--
-- Strictly additive + idempotent: one guarded ADD COLUMN. Rows warmed before this
-- column existed read NULL and the plan render degrades to an empty profile until
-- the cron backfills the track on its next run — safe to apply ahead of the code
-- deploy.

-- --------------------------------------------------------------------------- --
-- elevation_track — downsampled [{lat, lng, dist_m, e_m}, ...] route track (the
-- shared.live_radial.track_from_route output). build_elevation_profile consumes
-- dist_m + e_m. Nullable: a pre-column row reads NULL → empty profile; the cron
-- fills it in on the next warm.
-- --------------------------------------------------------------------------- --
ALTER TABLE route_weather_cache
    ADD COLUMN IF NOT EXISTS elevation_track JSONB;
