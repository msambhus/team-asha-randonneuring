-- Store large evidence images in private Supabase Storage instead of BYTEA.
ALTER TABLE rp_validation_evidence
    ADD COLUMN IF NOT EXISTS storage_path TEXT;

CREATE TABLE IF NOT EXISTS rp_validation_upload (
    id                 BIGSERIAL PRIMARY KEY,
    rider_id           INTEGER NOT NULL REFERENCES rp_rider(id) ON DELETE CASCADE,
    event_id           INTEGER NOT NULL REFERENCES rp_brevet_event(id) ON DELETE CASCADE,
    storage_path       TEXT NOT NULL UNIQUE,
    original_filename  TEXT NOT NULL,
    content_type       TEXT NOT NULL,
    byte_size          INTEGER NOT NULL CHECK (byte_size > 0),
    sha256             TEXT NOT NULL,
    control_order      INTEGER,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    attached_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS rp_validation_upload_owner_idx
    ON rp_validation_upload (rider_id, event_id, attached_at, created_at);
ALTER TABLE rp_validation_upload ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON rp_validation_upload FROM anon, authenticated;
