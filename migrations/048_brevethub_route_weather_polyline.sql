-- 048_brevethub_route_weather_polyline.sql
-- BrevetHub along-route weather cache gains a decimated route polyline for the
-- guest Mapbox weather tab.
--
-- The full Mapbox weather tab on the guest /plan page draws the route line + wind
-- arrows client-side, but the geometry must come from the cron-warmed cache (the
-- guest page NEVER calls RWGPS/Open-Meteo live — the TA-237 lesson). This adds a
-- nullable polyline column to rp_brevet_route_weather: the warm cron decimates the
-- RWGPS track points it already fetches (every 20th point) into a compact
-- [[lat, lng], ...] list and stores it here, so the read path serves the map line
-- straight from cache. Rows written before this migration read NULL, and the read
-- path falls back to the coarser sample_points as a rough polyline — no map break.
--
-- Strictly additive + idempotent + rp_*-only, like migrations 043/…/047: one
-- guarded ADD COLUMN on an rp_ table. Applying (or re-applying) it cannot alter any
-- Team Asha table; old code that predates the column simply ignores it, so it is
-- safe to apply ahead of the code deploy (the cron backfills polyline on its next
-- run; until then the read path draws the sample_points fallback line).

-- --------------------------------------------------------------------------- --
-- polyline — decimated route line for the Mapbox map, one [[lat, lng], ...] list.
-- Nullable: a row warmed before this column existed reads NULL and the read path
-- falls back to sample_points; the cron fills it in on the next warm.
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_brevet_route_weather
    ADD COLUMN IF NOT EXISTS polyline JSONB;
