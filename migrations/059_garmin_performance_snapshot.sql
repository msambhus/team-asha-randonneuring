-- Private daily Garmin recovery/training headlines.
CREATE TABLE IF NOT EXISTS garmin_performance_snapshot (
    rider_id              INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    snapshot_date         DATE NOT NULL,
    resting_heart_rate    NUMERIC,
    hrv_status            TEXT,
    sleep_score           NUMERIC,
    body_battery          NUMERIC,
    training_readiness    NUMERIC,
    vo2_max_cycling       NUMERIC,
    training_status       TEXT,
    raw_ciphertext        TEXT NOT NULL,
    synced_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (rider_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_garmin_snapshot_rider_date
    ON garmin_performance_snapshot (rider_id, snapshot_date DESC);

COMMENT ON COLUMN garmin_performance_snapshot.raw_ciphertext IS
    'Fernet-encrypted raw Garmin daily endpoint payloads';

REVOKE ALL ON TABLE garmin_performance_snapshot FROM anon, authenticated;
