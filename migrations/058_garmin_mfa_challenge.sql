-- Short-lived, encrypted Garmin MFA login context.
-- Passwords are never included; the ciphertext contains only Garmin challenge
-- cookies and routing metadata required to submit the one-time code.
CREATE TABLE IF NOT EXISTS garmin_mfa_challenge (
    rider_id          INTEGER PRIMARY KEY REFERENCES rider(id) ON DELETE CASCADE,
    state_ciphertext  TEXT NOT NULL,
    expires_at        TIMESTAMPTZ NOT NULL,
    attempts          INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_garmin_mfa_challenge_expires
    ON garmin_mfa_challenge (expires_at);

COMMENT ON COLUMN garmin_mfa_challenge.state_ciphertext IS
    'Fernet-encrypted JSON challenge state; never Garmin credentials';

-- Both Garmin tables are server-only. The app uses its direct PostgreSQL
-- connection; browser-facing Supabase Data API roles must never see them.
REVOKE ALL ON TABLE garmin_connection FROM anon, authenticated;
REVOKE ALL ON TABLE garmin_mfa_challenge FROM anon, authenticated;
