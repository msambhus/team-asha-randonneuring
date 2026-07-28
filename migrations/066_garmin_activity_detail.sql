-- Private, bounded Garmin lap details for matched brevet Stats.
CREATE TABLE IF NOT EXISTS garmin_activity_detail (
    rider_id              INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    garmin_activity_id     BIGINT NOT NULL,
    laps                   JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_ciphertext         TEXT NOT NULL,
    synced_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (rider_id, garmin_activity_id),
    FOREIGN KEY (rider_id, garmin_activity_id)
        REFERENCES garmin_activity(rider_id, garmin_activity_id)
        ON DELETE CASCADE
);

COMMENT ON COLUMN garmin_activity_detail.raw_ciphertext IS
    'Fernet-encrypted complete Garmin activity summary and split payload';

ALTER TABLE garmin_activity_detail ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE garmin_activity_detail FROM anon, authenticated;
