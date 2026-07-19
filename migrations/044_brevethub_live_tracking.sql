-- 044_brevethub_live_tracking.sql
-- BrevetHub Live Tracking (Mission 1): real Garmin ingestion + per-stop telemetry
-- + a multi-rider member map. This adds the rp_-scoped schema the poll cron,
-- rp_ model layer, setup UI, and member map need — mirroring Team Asha's
-- rider_live_tracking / rider_live_position shapes WITHOUT touching any TA table.
--
-- Two changes, both strictly additive + idempotent + rp_*-only (exactly like
-- migrations 033/036): extend rp_live_position with telemetry columns, and create
-- the rp_live_tracking config table. Applying (or re-applying) it cannot alter any
-- Team Asha table; old code that predates these columns simply ignores them.
--
--   rp_live_position  +source/+accuracy/+speed/+heart_rate/+power/+cadence
--                     (+created_at for retention purge; ride_id already exists
--                      from migration 033 — re-added IF NOT EXISTS as a no-op).
--   rp_live_tracking  NEW — per-rider live-tracking prefs (enabled, Garmin session
--                     link/token, the ride tracking is pointed at). Keyed on
--                     rider_id so every write is self-scoped (one row per rider).
--   rp_ride           +rwgps_url so the member map can overlay the RWGPS route
--                     polyline for a live ride (fail-soft when absent).

-- --------------------------------------------------------------------------- --
-- rp_live_position — telemetry columns. Garmin (and, later, a phone beacon)
-- report speed/heart_rate/power/cadence when paired sensors provide them; source
-- records how the point arrived ('garmin'), accuracy the GPS error radius, and
-- created_at the ingest time the retention purge deletes by. All nullable so the
-- rows the rider-posts-own-position path already writes stay valid.
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_live_position ADD COLUMN IF NOT EXISTS ride_id     INTEGER REFERENCES rp_ride(id);
ALTER TABLE rp_live_position ADD COLUMN IF NOT EXISTS source      TEXT;
ALTER TABLE rp_live_position ADD COLUMN IF NOT EXISTS accuracy    DOUBLE PRECISION;
ALTER TABLE rp_live_position ADD COLUMN IF NOT EXISTS speed       DOUBLE PRECISION;
ALTER TABLE rp_live_position ADD COLUMN IF NOT EXISTS heart_rate  INTEGER;
ALTER TABLE rp_live_position ADD COLUMN IF NOT EXISTS power       INTEGER;
ALTER TABLE rp_live_position ADD COLUMN IF NOT EXISTS cadence     INTEGER;
ALTER TABLE rp_live_position ADD COLUMN IF NOT EXISTS created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- The retention purge deletes points older than N days by created_at; index it.
CREATE INDEX IF NOT EXISTS rp_live_position_created_idx ON rp_live_position (created_at);

-- --------------------------------------------------------------------------- --
-- rp_live_tracking — one row per rider: their master opt-in flag plus the Garmin
-- LiveTrack session (URL + token) pointed at a specific ride (active_ride_id).
-- Keyed on rider_id (PRIMARY KEY) so a write is inherently self-scoped: the poll
-- cron reads enabled rows, the setup UI upserts the session rider's own row, and
-- ON CONFLICT (rider_id) makes save/enable idempotent. Mirrors TA's
-- rider_live_tracking shape (rp_-prefixed, referencing rp_ tables only).
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_live_tracking (
    rider_id              INTEGER PRIMARY KEY REFERENCES rp_rider(id),
    enabled               BOOLEAN NOT NULL DEFAULT FALSE,
    garmin_session_url    TEXT,
    garmin_session_token  TEXT,
    active_ride_id        INTEGER REFERENCES rp_ride(id),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The poll cron scans enabled riders; index the flag it filters on.
CREATE INDEX IF NOT EXISTS rp_live_tracking_enabled_idx ON rp_live_tracking (enabled);

-- --------------------------------------------------------------------------- --
-- rp_ride.rwgps_url — the RideWithGPS route a live ride follows, so the member
-- map can overlay the course polyline (shared/rwgps.fetch_route). Nullable: rides
-- created before this column, or without a route, simply render dots only.
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_ride ADD COLUMN IF NOT EXISTS rwgps_url TEXT;
