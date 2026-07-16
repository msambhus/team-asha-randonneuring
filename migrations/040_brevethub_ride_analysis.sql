-- 040_brevethub_ride_analysis.sql
-- BrevetHub M9 (per-ride analysis): the cached, computed breakdown of one of a
-- rider's own Strava activities.
--
-- rp_ride_analysis stores ONE analysis per (rider, Strava activity): the computed
-- per-segment breakdown (rp_ride_analysis.analysis JSONB — summary metrics, the
-- detected stop list, and per-leg pace/HR/power/climb rows) plus the zlib-compressed
-- raw Strava streams (rp_ride_analysis.activity_streams BYTEA) so the view can
-- re-derive the map without another Strava call. The breakdown is computed by the
-- REUSED shared/strava_analysis.py engine on an explicit rider action (POST
-- /analysis/<id>/compute) and READ (never re-fetched/re-computed) on every GET —
-- keeping the heavy Strava fetch + analysis off the request path, exactly like the
-- rp_brevet_weather cron pattern.
--
-- strava_activity_id is a BIGINT: Strava activity ids already exceed 32 bits, so a
-- plain INTEGER would overflow. UNIQUE(rider_id, strava_activity_id) makes the
-- compute an idempotent upsert (one cached analysis per rider per activity) and is
-- the per-rider cache key the route scopes every read by — a rider only ever reads
-- their own analysis.
--
-- Strictly additive + idempotent + rp_*-only, exactly like migrations 033/035/036/
-- 037/038/039: one guarded table creation plus guarded indexes, referencing only
-- rp_* tables (every statement carries IF NOT EXISTS). Applying (or re-applying) it
-- cannot alter any Team Asha table; old code that predates the table simply ignores
-- it, so it is safe to apply ahead of the code deploy.

-- --------------------------------------------------------------------------- --
-- rp_ride_analysis — a rider's cached per-ride Strava analysis.
--
-- analysis holds the JSON-safe computed breakdown (summary + stops + per-leg rows,
-- already unit-converted at the view boundary). activity_streams holds the raw
-- zlib-compressed streams so the map/detail view re-renders without re-fetching.
-- The FK references an rp_* table only.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_ride_analysis (
    id                  SERIAL PRIMARY KEY,
    rider_id            INTEGER NOT NULL REFERENCES rp_rider(id),
    strava_activity_id  BIGINT  NOT NULL,                    -- Strava activity id (exceeds 32 bits)
    analysis            JSONB,                               -- computed per-segment breakdown
    activity_streams    BYTEA,                               -- zlib-compressed raw Strava streams
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (rider_id, strava_activity_id)
);

CREATE INDEX IF NOT EXISTS rp_ride_analysis_rider_idx ON rp_ride_analysis (rider_id);
