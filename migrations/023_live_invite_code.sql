-- Migration 023: Public per-ride live-map invite codes.
--
-- A logged-in club member generates a short, typeable code for a ride; an
-- unauthenticated guest enters it on /live/join to view THAT ride's live map
-- (read-only — positions only, no member controls). Codes auto-expire ~24h
-- after the ride day so live locations aren't viewable indefinitely.
--
-- Privacy: read-only, scoped to one ride; the join grant lives in the guest's
-- session and is re-validated (expiry) on every request. Applied via Supabase
-- MCP after merge.

CREATE TABLE IF NOT EXISTS live_invite_code (
    code        TEXT PRIMARY KEY,
    ride_id     INTEGER NOT NULL REFERENCES ride(id) ON DELETE CASCADE,
    created_by  INTEGER REFERENCES rider(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_live_invite_ride ON live_invite_code(ride_id);
