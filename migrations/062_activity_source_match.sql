-- Rider-owned provenance links between Garmin and Strava recordings.
CREATE TABLE IF NOT EXISTS activity_source_match (
    id                    BIGSERIAL PRIMARY KEY,
    rider_id              INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    garmin_activity_id    BIGINT NOT NULL,
    strava_activity_id    BIGINT NOT NULL REFERENCES strava_activity(strava_activity_id)
                              ON DELETE CASCADE,
    confidence            NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    reasons               JSONB NOT NULL DEFAULT '{}'::jsonb,
    match_status          TEXT NOT NULL DEFAULT 'auto'
                              CHECK (match_status IN ('auto', 'confirmed', 'manual', 'rejected')),
    matched_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (rider_id, garmin_activity_id)
        REFERENCES garmin_activity(rider_id, garmin_activity_id) ON DELETE CASCADE,
    UNIQUE (rider_id, garmin_activity_id),
    UNIQUE (rider_id, strava_activity_id)
);

CREATE INDEX IF NOT EXISTS idx_activity_source_match_rider
    ON activity_source_match (rider_id, matched_at DESC);

COMMENT ON TABLE activity_source_match IS
    'Private rider-owned provenance links between Garmin and Strava recordings';
COMMENT ON COLUMN activity_source_match.reasons IS
    'Human-readable matching signals; never contains raw provider payloads';

ALTER TABLE activity_source_match ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE activity_source_match FROM anon, authenticated;
