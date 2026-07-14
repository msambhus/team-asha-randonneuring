-- 034_brevethub_rusa_strava_cache.sql
-- BrevetHub Mission 2: caching columns for per-rider RUSA stats + Strava stats.
--
-- Additive and idempotent (ADD COLUMN IF NOT EXISTS). It touches ONLY the two
-- rp_* tenant tables from migration 033 and creates no non-rp_* object, so
-- applying it cannot affect the Team Asha app. Re-applying it is a no-op.
--
--   rp_rider              gains a cached RUSA scrape (JSONB) + fetch timestamp
--   rp_strava_connection  gains the granted OAuth scope + a cached activity
--                         summary (JSONB) + its fetch timestamp
--
-- rp_strava_connection.expires_at stays TIMESTAMPTZ (from migration 033):
-- BrevetHub's model layer converts epoch<->TIMESTAMPTZ at the DB boundary
-- (to_timestamp on write, .timestamp() on read), so no column-type change.

-- --------------------------------------------------------------------------- --
-- rp_rider — cache the rider's scraped RUSA brevet history so every dashboard
-- load does not re-scrape rusa.org (7-day TTL enforced in app code; a manual
-- refresh overrides it).
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_rider
    ADD COLUMN IF NOT EXISTS rusa_cache      JSONB,
    ADD COLUMN IF NOT EXISTS rusa_fetched_at TIMESTAMPTZ;

-- --------------------------------------------------------------------------- --
-- rp_strava_connection — record the granted OAuth scope and cache a computed
-- 28-day activity summary (6-hour TTL in app code). Individual activities are
-- fetched, summarized, and discarded; there is no rp_strava_activity table.
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_strava_connection
    ADD COLUMN IF NOT EXISTS scope             TEXT,
    ADD COLUMN IF NOT EXISTS stats_cache       JSONB,
    ADD COLUMN IF NOT EXISTS stats_fetched_at  TIMESTAMPTZ;
