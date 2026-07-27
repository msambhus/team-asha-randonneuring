-- Private Garmin cycling summaries used for rider-owned performance analysis.
CREATE TABLE IF NOT EXISTS garmin_activity (
    rider_id               INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    garmin_activity_id     BIGINT NOT NULL,
    activity_name          TEXT,
    activity_type          TEXT,
    started_at             TIMESTAMPTZ,
    distance_m             NUMERIC,
    duration_s             NUMERIC,
    moving_duration_s      NUMERIC,
    elevation_gain_m       NUMERIC,
    average_hr             NUMERIC,
    max_hr                 NUMERIC,
    average_power          NUMERIC,
    max_power              NUMERIC,
    normalized_power       NUMERIC,
    aerobic_training_effect NUMERIC,
    anaerobic_training_effect NUMERIC,
    calories               NUMERIC,
    average_cadence        NUMERIC,
    device_name            TEXT,
    raw_ciphertext         TEXT NOT NULL,
    synced_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (rider_id, garmin_activity_id)
);

CREATE INDEX IF NOT EXISTS idx_garmin_activity_rider_started
    ON garmin_activity (rider_id, started_at DESC);

COMMENT ON COLUMN garmin_activity.raw_ciphertext IS
    'Fernet-encrypted complete Garmin activity summary';

-- The Flask server uses its direct PostgreSQL connection. Browser-facing
-- Supabase roles must never be able to query Garmin tokens or health data.
ALTER TABLE garmin_connection ENABLE ROW LEVEL SECURITY;
ALTER TABLE garmin_mfa_challenge ENABLE ROW LEVEL SECURITY;
ALTER TABLE garmin_performance_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE garmin_activity ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE garmin_connection FROM anon, authenticated;
REVOKE ALL ON TABLE garmin_mfa_challenge FROM anon, authenticated;
REVOKE ALL ON TABLE garmin_performance_snapshot FROM anon, authenticated;
REVOKE ALL ON TABLE garmin_activity FROM anon, authenticated;
