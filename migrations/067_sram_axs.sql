-- Private SRAM AXS connection, gearing telemetry, and provider matches.
CREATE TABLE IF NOT EXISTS sram_axs_connection (
    rider_id          INTEGER PRIMARY KEY REFERENCES rider(id) ON DELETE CASCADE,
    token_ciphertext  TEXT NOT NULL,
    display_name      TEXT,
    status            TEXT NOT NULL DEFAULT 'connected',
    connected_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_sync_at      TIMESTAMPTZ,
    last_error        TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sram_axs_activity (
    rider_id            INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    sram_activity_id    TEXT NOT NULL,
    activity_name       TEXT,
    activity_type       TEXT,
    started_at          TIMESTAMPTZ,
    ended_at            TIMESTAMPTZ,
    distance_m          NUMERIC,
    duration_s          NUMERIC,
    elevation_gain_m    NUMERIC,
    average_power       NUMERIC,
    max_power           NUMERIC,
    normalized_power    NUMERIC,
    average_hr          NUMERIC,
    max_hr              NUMERIC,
    average_cadence     NUMERIC,
    max_cadence         NUMERIC,
    rear_shift_count    INTEGER,
    front_shift_count   INTEGER,
    component_ids       JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_ciphertext      TEXT NOT NULL,
    synced_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (rider_id, sram_activity_id)
);

CREATE INDEX IF NOT EXISTS idx_sram_axs_activity_rider_started
    ON sram_axs_activity (rider_id, started_at DESC);

CREATE TABLE IF NOT EXISTS sram_axs_activity_detail (
    rider_id            INTEGER NOT NULL,
    sram_activity_id    TEXT NOT NULL,
    gear_summary        JSONB NOT NULL DEFAULT '{}'::jsonb,
    components          JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_ciphertext      TEXT NOT NULL,
    synced_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (rider_id, sram_activity_id),
    FOREIGN KEY (rider_id, sram_activity_id)
        REFERENCES sram_axs_activity(rider_id, sram_activity_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sram_axs_activity_match (
    rider_id            INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    sram_activity_id    TEXT NOT NULL,
    strava_activity_id  BIGINT REFERENCES strava_activity(strava_activity_id)
                            ON DELETE SET NULL,
    garmin_activity_id  BIGINT,
    ride_id             INTEGER REFERENCES ride(id) ON DELETE SET NULL,
    confidence          NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    reasons             JSONB NOT NULL DEFAULT '{}'::jsonb,
    match_status        TEXT NOT NULL DEFAULT 'auto'
                            CHECK (match_status IN ('auto','confirmed','manual','rejected')),
    matched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (rider_id, sram_activity_id),
    FOREIGN KEY (rider_id, sram_activity_id)
        REFERENCES sram_axs_activity(rider_id, sram_activity_id)
        ON DELETE CASCADE,
    FOREIGN KEY (rider_id, garmin_activity_id)
        REFERENCES garmin_activity(rider_id, garmin_activity_id)
        ON DELETE CASCADE
);

COMMENT ON COLUMN sram_axs_connection.token_ciphertext IS
    'Fernet-encrypted SRAM Auth0 session tokens; passwords are never stored';
COMMENT ON COLUMN sram_axs_activity.raw_ciphertext IS
    'Fernet-encrypted complete SRAM AXS activity envelope';
COMMENT ON COLUMN sram_axs_activity_detail.raw_ciphertext IS
    'Fernet-encrypted complete SRAM AXS component telemetry';

ALTER TABLE sram_axs_connection ENABLE ROW LEVEL SECURITY;
ALTER TABLE sram_axs_activity ENABLE ROW LEVEL SECURITY;
ALTER TABLE sram_axs_activity_detail ENABLE ROW LEVEL SECURITY;
ALTER TABLE sram_axs_activity_match ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE sram_axs_connection FROM anon, authenticated;
REVOKE ALL ON TABLE sram_axs_activity FROM anon, authenticated;
REVOKE ALL ON TABLE sram_axs_activity_detail FROM anon, authenticated;
REVOKE ALL ON TABLE sram_axs_activity_match FROM anon, authenticated;

-- Garmin Edge activity summaries expose the device-recorded temperature.
ALTER TABLE garmin_activity
    ADD COLUMN IF NOT EXISTS average_temperature_c NUMERIC,
    ADD COLUMN IF NOT EXISTS min_temperature_c NUMERIC,
    ADD COLUMN IF NOT EXISTS max_temperature_c NUMERIC;
