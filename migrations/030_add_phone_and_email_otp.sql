-- 030_add_phone_and_email_otp.sql
-- Phase 1 of the mobile auth overhaul (App Store Guideline 4.8): the iOS app
-- drops Google + Sign in with Apple and offers email + password PLUS passwordless
-- email OTP (a 6-digit code AND a magic link). An existing Google/Apple member
-- signs in by requesting an email OTP — the code goes to their verified email and
-- resolves to their SAME app_user row (models.get_user_by_email), so no account
-- is orphaned by removing the buttons.
--
-- `phone` is collected now (unverified) so a future phase 2 can add SMS OTP
-- without another migration. See routes/api_auth.py otp_* endpoints and
-- services/otp_service.py.

-- Phone number for future SMS OTP. NULL for everyone until they provide it;
-- phone_verified stays FALSE until an SMS OTP confirms ownership (phase 2).
ALTER TABLE app_user
    ADD COLUMN IF NOT EXISTS phone          VARCHAR(32),
    ADD COLUMN IF NOT EXISTS phone_verified BOOLEAN NOT NULL DEFAULT FALSE;

-- One row per issued OTP. We store only HASHES, never the plaintext:
--   * code_hash — a salted werkzeug hash of the 6-digit code (low entropy, so it
--     needs a slow salted hash + an attempts cap + short expiry). Looked up by
--     identifier, verified with check_password_hash.
--   * link_hash — sha256 hex of the high-entropy magic-link token. Deterministic
--     (unlike a salted hash) so the magic link can be looked up directly by hash;
--     safe because the token is 256 bits of randomness.
-- A DB leak therefore can't be replayed to log in.
CREATE TABLE IF NOT EXISTS auth_otp (
    id          BIGSERIAL    PRIMARY KEY,
    identifier  VARCHAR(255) NOT NULL,               -- lower(email); phase 2 reuses this for phones
    channel     VARCHAR(16)  NOT NULL DEFAULT 'email',
    code_hash   VARCHAR(255) NOT NULL,               -- salted werkzeug hash of the 6-digit code
    link_hash   VARCHAR(64),                         -- sha256 hex of the magic-link token
    request_ip  VARCHAR(64),                         -- client IP at request time, for per-IP rate limiting
    attempts    SMALLINT     NOT NULL DEFAULT 0,     -- wrong-code tries, capped in the route
    consumed_at TIMESTAMPTZ,                         -- set on redemption / when a newer code supersedes it
    expires_at  TIMESTAMPTZ  NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Newest live code per identifier + rate-limit counts by (identifier, created_at).
CREATE INDEX IF NOT EXISTS auth_otp_identifier_created_idx
    ON auth_otp (identifier, created_at DESC);

-- Direct magic-link lookup by hash.
CREATE INDEX IF NOT EXISTS auth_otp_link_hash_idx
    ON auth_otp (link_hash);

-- Per-IP rate-limit counts (email-bomb / cross-identifier brute-force defense).
CREATE INDEX IF NOT EXISTS auth_otp_ip_created_idx
    ON auth_otp (request_ip, created_at DESC);
