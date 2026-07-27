-- Rider-owned Garmin Connect account linkage.
--
-- token_ciphertext contains Fernet-encrypted DI OAuth token JSON. Garmin
-- email/password and MFA codes are never persisted. One row per rider keeps every
-- read/write naturally self-scoped, and deleting the row disconnects the account.
CREATE TABLE IF NOT EXISTS garmin_connection (
    rider_id          INTEGER PRIMARY KEY REFERENCES rider(id) ON DELETE CASCADE,
    token_ciphertext  TEXT NOT NULL,
    display_name      TEXT,
    status            TEXT NOT NULL DEFAULT 'connected'
                      CHECK (status IN ('connected', 'reauth_required', 'error')),
    connected_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_sync_at      TIMESTAMPTZ,
    last_error        TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON COLUMN garmin_connection.token_ciphertext IS
    'Fernet-encrypted Garmin DI OAuth token JSON; never plaintext credentials';
