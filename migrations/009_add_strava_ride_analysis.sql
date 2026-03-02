-- Migration 009: Add Strava ride analysis tables
-- Links brevet rides to Strava activities and caches stream analysis

-- Match table: links a rider's brevet ride to a Strava activity
CREATE TABLE IF NOT EXISTS strava_ride_match (
    id SERIAL PRIMARY KEY,
    rider_id INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    ride_id INTEGER NOT NULL REFERENCES ride(id) ON DELETE CASCADE,
    strava_activity_id BIGINT NOT NULL,
    match_confidence TEXT DEFAULT 'auto',
    matched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (rider_id, ride_id)
);

CREATE INDEX IF NOT EXISTS idx_strava_ride_match_rider ON strava_ride_match(rider_id);
CREATE INDEX IF NOT EXISTS idx_strava_ride_match_ride ON strava_ride_match(ride_id);

-- Analysis cache: stores stream-derived analysis to avoid re-fetching
CREATE TABLE IF NOT EXISTS strava_ride_analysis (
    id SERIAL PRIMARY KEY,
    match_id INTEGER NOT NULL UNIQUE REFERENCES strava_ride_match(id) ON DELETE CASCADE,
    detected_stops JSONB,
    stream_summary JSONB,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    strava_api_error TEXT
);
