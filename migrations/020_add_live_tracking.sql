-- Migration 020: Live rider location tracking (PR 1 — Garmin LiveTrack + shared foundation)
--
-- Two tables:
--   rider_live_tracking  — per-rider opt-in prefs + registered Garmin LiveTrack session
--   rider_live_position  — append-only position points (source: 'garmin' now, 'beacon' later)
--
-- Privacy: opt-in only (enabled defaults false); reads are club-login-only at the app layer;
-- a 7-day TTL purge runs in the poll cron. Applied manually / via Supabase MCP after merge.

CREATE TABLE IF NOT EXISTS rider_live_tracking (
    rider_id             INTEGER PRIMARY KEY REFERENCES rider(id) ON DELETE CASCADE,
    enabled              BOOLEAN NOT NULL DEFAULT FALSE,
    garmin_session_url   TEXT,
    garmin_session_token TEXT,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rider_live_position (
    id          SERIAL PRIMARY KEY,
    rider_id    INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    lat         NUMERIC NOT NULL,
    lng         NUMERIC NOT NULL,
    accuracy    NUMERIC,
    recorded_at TIMESTAMPTZ NOT NULL,          -- device-reported timestamp
    source      TEXT NOT NULL CHECK (source IN ('garmin', 'beacon')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rider_live_position_rider_recorded
    ON rider_live_position (rider_id, recorded_at DESC);
