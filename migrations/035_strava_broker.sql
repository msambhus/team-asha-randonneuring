-- 035_strava_broker.sql
-- Shared Strava OAuth broker: the two rp_* tables BrevetHub uses to serve a
-- Strava connect on behalf of Team Asha.
--
-- One Strava app (single Authorization Callback Domain = brevethub.vercel.app)
-- now serves both apps. BrevetHub hosts the callback; a Team Asha rider is bounced
-- to BrevetHub to authorize, and the resulting tokens are handed back to Team Asha
-- via a one-time server-side row here — NEVER in a URL query param.
--
-- Both tables are rp_-prefixed so BrevetHub's rp-only isolation invariant holds
-- (BrevetHub is the sole reader/writer of rp_strava_broker_state; it writes
-- rp_strava_broker_handoff and Team Asha reads+deletes it). Every statement is
-- additive and idempotent (IF NOT EXISTS), touches no existing Team Asha table,
-- and creates no non-rp_* object, so applying it cannot affect either app.

-- --------------------------------------------------------------------------- --
-- rp_strava_broker_handoff — a one-time delivery row for a Team Asha connect.
--
-- BrevetHub INSERTs a row after exchanging the Strava code; Team Asha consumes it
-- with a single atomic DELETE ... RETURNING (delete-on-read), so a surviving row
-- IS an unconsumed row — there is deliberately no consumed_at column.
--
-- TWO distinct, non-interchangeable expiry columns:
--   strava_token_expires_at  the Strava ACCESS-TOKEN lifetime (~6h). Used ONLY
--                            when Team Asha writes strava_connection. NEVER the
--                            TTL/replay gate.
--   handoff_expires_at       the short one-time-code TTL (~5m). The consume gate
--                            reads ONLY this column, so an expired one-time code
--                            can never be accepted within the token's ~6h life.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_strava_broker_handoff (
    id                       SERIAL PRIMARY KEY,
    code                     TEXT NOT NULL UNIQUE,        -- opaque one-time code
    ta_rider_id              INTEGER NOT NULL,            -- Team Asha rider id (no cross-tenant FK)
    strava_athlete_id        BIGINT,
    access_token             TEXT NOT NULL,
    refresh_token            TEXT NOT NULL,
    strava_token_expires_at  TIMESTAMPTZ NOT NULL,        -- Strava token lifetime (~6h)
    scope                    TEXT,
    handoff_expires_at       TIMESTAMPTZ NOT NULL,        -- one-time-code TTL (~5m) — the replay/TTL gate
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS rp_strava_broker_handoff_expires_idx
    ON rp_strava_broker_handoff (handoff_expires_at);

-- --------------------------------------------------------------------------- --
-- rp_strava_broker_state — the durable single-use claim store (replay guard).
--
-- On a Team Asha /connect, BrevetHub does INSERT ... ON CONFLICT (nonce) DO
-- NOTHING RETURNING nonce: a returned row is first-use (proceed); zero rows means
-- the nonce was already claimed → hard reject as a replay, BEFORE any Strava
-- redirect. This is what makes signed-state single-use enforceable across
-- stateless serverless invocations (an HMAC + TTL check alone cannot).
--
-- state_expires_at exists only for opportunistic cleanup of old claims; it is not
-- part of any security gate. BrevetHub is the sole reader/writer; Team Asha never
-- touches this table.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_strava_broker_state (
    nonce             TEXT PRIMARY KEY,
    state_expires_at  TIMESTAMPTZ NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS rp_strava_broker_state_expires_idx
    ON rp_strava_broker_state (state_expires_at);
