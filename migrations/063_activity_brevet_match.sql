-- Private provenance links from provider recordings to finished brevets.
CREATE TABLE IF NOT EXISTS activity_brevet_match (
    id                    BIGSERIAL PRIMARY KEY,
    rider_id              INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    ride_id               INTEGER NOT NULL REFERENCES ride(id) ON DELETE CASCADE,
    source_match_id       BIGINT REFERENCES activity_source_match(id) ON DELETE CASCADE,
    garmin_activity_id    BIGINT,
    strava_activity_id    BIGINT REFERENCES strava_activity(strava_activity_id)
                              ON DELETE CASCADE,
    confidence            NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    reasons               JSONB NOT NULL DEFAULT '{}'::jsonb,
    match_status          TEXT NOT NULL
                              CHECK (match_status IN ('authoritative', 'auto', 'confirmed', 'manual', 'rejected')),
    matched_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (rider_id, garmin_activity_id)
        REFERENCES garmin_activity(rider_id, garmin_activity_id) ON DELETE CASCADE,
    CHECK (source_match_id IS NOT NULL OR garmin_activity_id IS NOT NULL
           OR strava_activity_id IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_activity_brevet_source_match
    ON activity_brevet_match (source_match_id)
    WHERE source_match_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_activity_brevet_garmin
    ON activity_brevet_match (rider_id, garmin_activity_id)
    WHERE garmin_activity_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_activity_brevet_strava
    ON activity_brevet_match (rider_id, strava_activity_id)
    WHERE strava_activity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_activity_brevet_rider_ride
    ON activity_brevet_match (rider_id, ride_id);

COMMENT ON TABLE activity_brevet_match IS
    'Private rider-owned links from provider recordings to finished brevets';
COMMENT ON COLUMN activity_brevet_match.reasons IS
    'Human-readable match provenance; never contains raw provider payloads';

ALTER TABLE activity_brevet_match ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE activity_brevet_match FROM anon, authenticated;
