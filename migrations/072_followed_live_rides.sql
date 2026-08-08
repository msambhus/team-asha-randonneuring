-- Cross-device live ride follows for signed-in riders.
CREATE TABLE IF NOT EXISTS rider_followed_live_ride (
    rider_id INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    ride_id INTEGER NOT NULL REFERENCES ride(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (rider_id, ride_id)
);

CREATE INDEX IF NOT EXISTS idx_followed_live_ride_ride
    ON rider_followed_live_ride (ride_id);

-- This table is private application data. The Flask backend uses its direct
-- database connection; the public PostgREST surface must expose no rows.
ALTER TABLE rider_followed_live_ride ENABLE ROW LEVEL SECURITY;
