-- ============================================================
-- Team Asha Randonneuring - Database Schema
-- ============================================================
-- PostgreSQL Database Schema
-- Last updated: 2026-02-24
-- Tables: 10 (after consolidation and date type migration)
-- ============================================================

-- Enable extensions if needed
-- CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- AUTHENTICATION & USER MANAGEMENT
-- ============================================================

CREATE TABLE app_user (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    google_id VARCHAR(255) NOT NULL UNIQUE,
    profile_completed BOOLEAN DEFAULT FALSE,
    rider_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_app_user_email ON app_user(email);
CREATE INDEX idx_app_user_google_id ON app_user(google_id);
CREATE INDEX idx_app_user_rider_id ON app_user(rider_id);

COMMENT ON TABLE app_user IS 'User authentication via Google OAuth';
COMMENT ON COLUMN app_user.profile_completed IS 'Whether user has completed profile setup';

-- ============================================================
-- RIDER INFORMATION
-- ============================================================

CREATE TABLE rider (
    id SERIAL PRIMARY KEY,
    rusa_id INTEGER NOT NULL UNIQUE,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL
);

CREATE INDEX idx_rider_rusa_id ON rider(rusa_id);
CREATE INDEX idx_rider_name ON rider(first_name, last_name);

COMMENT ON TABLE rider IS 'Core rider information tied to RUSA membership';
COMMENT ON COLUMN rider.rusa_id IS 'RUSA membership number (unique identifier)';

CREATE TABLE rider_profile (
    rider_id INTEGER PRIMARY KEY,
    photo_filename TEXT,
    bio TEXT,
    pbp_2023_registered BOOLEAN DEFAULT FALSE,
    pbp_2023_status TEXT,
    FOREIGN KEY (rider_id) REFERENCES rider(id) ON DELETE CASCADE
);

COMMENT ON TABLE rider_profile IS 'Extended rider profile information (photos, bio, PBP status)';

-- ============================================================
-- RIDE PLANNING & MANAGEMENT
-- ============================================================

CREATE TABLE season (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    start_date DATE,
    end_date DATE,
    is_current BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_season_current ON season(is_current);

COMMENT ON TABLE season IS 'Cycling seasons for organizing rides (e.g., 2025-2026)';
COMMENT ON COLUMN season.is_current IS 'Only one season should be marked as current';

CREATE TABLE club (
    id SERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    region TEXT NOT NULL
);

CREATE INDEX idx_club_code ON club(code);
CREATE INDEX idx_club_region ON club(region);

COMMENT ON TABLE club IS 'Cycling clubs/organizations (Team Asha + external clubs)';
COMMENT ON COLUMN club.code IS 'Short club code (TA, SFR, DBC, SCR, SRR, etc.)';

-- Insert default clubs
INSERT INTO club (code, name, region) VALUES 
    ('TA', 'Team Asha', 'Bay Area'),
    ('SFR', 'San Francisco Randonneurs', 'San Francisco'),
    ('DBC', 'Davis Bike Club', 'Davis'),
    ('SCR', 'Santa Cruz Randonneurs', 'Santa Cruz'),
    ('SRR', 'Santa Rosa Randonneurs', 'Santa Rosa'),
    ('AIR', 'Audax India Randonneurs', 'India'),
    ('PR', 'Pune Randonneurs', 'India')
ON CONFLICT (code) DO NOTHING;

CREATE TABLE ride_plan (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    total_distance_miles NUMERIC,
    total_elevation_ft INTEGER,
    rwgps_url TEXT,
    rwgps_url_team TEXT,
    distance_km INTEGER,
    cutoff_hours NUMERIC,
    start_time TEXT DEFAULT '07:00',
    avg_moving_speed NUMERIC,
    avg_elapsed_speed NUMERIC,
    total_moving_time_min INTEGER,
    total_elapsed_time_min INTEGER,
    total_break_time_min INTEGER,
    overall_ft_per_mile NUMERIC,
    rwgps_route_id TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_ride_plan_slug ON ride_plan(slug);
CREATE INDEX idx_ride_plan_distance ON ride_plan(distance_km);

COMMENT ON TABLE ride_plan IS 'Detailed ride route plans with timing and elevation data';
COMMENT ON COLUMN ride_plan.rwgps_url_team IS 'Team Asha customized route URL';

CREATE TABLE ride_plan_stop (
    id SERIAL PRIMARY KEY,
    ride_plan_id INTEGER NOT NULL,
    stop_order INTEGER NOT NULL,
    location TEXT NOT NULL,
    stop_type TEXT DEFAULT 'waypoint',
    distance_miles NUMERIC,
    elevation_gain INTEGER,
    segment_time_min INTEGER,
    notes TEXT,
    seg_dist NUMERIC,
    ft_per_mi INTEGER,
    avg_speed NUMERIC,
    cum_time_min INTEGER,
    bookend_time_min INTEGER,
    time_bank_min INTEGER,
    difficulty_score NUMERIC,
    FOREIGN KEY (ride_plan_id) REFERENCES ride_plan(id) ON DELETE CASCADE
);

CREATE INDEX idx_ride_plan_stop_plan ON ride_plan_stop(ride_plan_id);
CREATE INDEX idx_ride_plan_stop_order ON ride_plan_stop(ride_plan_id, stop_order);

COMMENT ON TABLE ride_plan_stop IS 'Individual stops/waypoints along a ride plan';
COMMENT ON COLUMN ride_plan_stop.stop_type IS 'Type: start, finish, control, rest, waypoint';

CREATE TABLE ride (
    id SERIAL PRIMARY KEY,
    season_id INTEGER NOT NULL,
    club_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    ride_type TEXT,
    date DATE NOT NULL,
    distance_km INTEGER NOT NULL,
    elevation_ft INTEGER,
    distance_miles REAL,
    ft_per_mile REAL,
    rwgps_url TEXT,
    rusa_event_id TEXT,
    ride_plan_id INTEGER,
    event_status TEXT DEFAULT 'UPCOMING',
    start_location TEXT,
    start_time TEXT,
    time_limit_hours REAL,
    FOREIGN KEY (season_id) REFERENCES season(id),
    FOREIGN KEY (club_id) REFERENCES club(id),
    FOREIGN KEY (ride_plan_id) REFERENCES ride_plan(id)
);

CREATE INDEX idx_ride_season_id ON ride(season_id);
CREATE INDEX idx_ride_club_id ON ride(club_id);
CREATE INDEX idx_ride_date ON ride(date);
CREATE INDEX idx_ride_status ON ride(event_status);
CREATE INDEX idx_ride_plan_id ON ride(ride_plan_id);

COMMENT ON TABLE ride IS 'All ride events (Team Asha organized + external RUSA events)';
COMMENT ON COLUMN ride.club_id IS 'TA=Team Asha, DBC/SFR/SCR/SRR=external clubs';
COMMENT ON COLUMN ride.event_status IS 'Status: UPCOMING or COMPLETED';
COMMENT ON COLUMN ride.date IS 'Ride date (DATE type for proper date handling)';

-- ============================================================
-- RIDE PARTICIPATION
-- ============================================================

CREATE TABLE rider_ride (
    id SERIAL PRIMARY KEY,
    rider_id INTEGER NOT NULL,
    ride_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    finish_time TEXT,
    signed_up_at TIMESTAMP,
    FOREIGN KEY (rider_id) REFERENCES rider(id) ON DELETE CASCADE,
    FOREIGN KEY (ride_id) REFERENCES ride(id) ON DELETE CASCADE,
    UNIQUE(rider_id, ride_id)
);

CREATE INDEX idx_rider_ride_rider ON rider_ride(rider_id);
CREATE INDEX idx_rider_ride_ride ON rider_ride(ride_id);
CREATE INDEX idx_rider_ride_status ON rider_ride(status);
CREATE INDEX idx_rider_ride_signed_up_at ON rider_ride(signed_up_at);

COMMENT ON TABLE rider_ride IS 'Tracks rider signups, participation and completion status (consolidated)';
COMMENT ON COLUMN rider_ride.status IS 'Status: SIGNED_UP, FINISHED, DNF, DNS';
COMMENT ON COLUMN rider_ride.signed_up_at IS 'Timestamp when rider signed up (NULL for historical rides)';

-- ============================================================
-- FOREIGN KEY CONSTRAINTS
-- ============================================================

-- Add foreign key from app_user to rider
ALTER TABLE app_user 
ADD CONSTRAINT fk_app_user_rider 
FOREIGN KEY (rider_id) REFERENCES rider(id);

-- ============================================================
-- HELPER VIEWS (Optional but useful)
-- ============================================================

-- View for upcoming events with club info
CREATE OR REPLACE VIEW v_upcoming_events AS
SELECT 
    ri.*,
    c.code as club_code,
    c.name as club_name,
    c.region as region,
    (c.code = 'TA') as is_team_ride,
    rp.slug as plan_slug,
    rp.rwgps_url_team as plan_rwgps_url_team
FROM ride ri
INNER JOIN club c ON ri.club_id = c.id
LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
WHERE ri.date >= CURRENT_DATE 
  AND ri.event_status = 'UPCOMING'
ORDER BY ri.date;

COMMENT ON VIEW v_upcoming_events IS 'All upcoming events (Team Asha + external) with club details';

-- View for Team Asha rides only
CREATE OR REPLACE VIEW v_team_asha_rides AS
SELECT ri.*, rp.slug as plan_slug
FROM ride ri
LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
WHERE ri.club_id = (SELECT id FROM club WHERE code = 'TA')
ORDER BY ri.date DESC;

COMMENT ON VIEW v_team_asha_rides IS 'Team Asha organized rides only';

-- ============================================================
-- USEFUL QUERIES FOR REFERENCE
-- ============================================================

-- Get all upcoming Team Asha rides:
-- SELECT * FROM v_team_asha_rides WHERE date >= CURRENT_DATE;

-- Get all upcoming external events:
-- SELECT * FROM v_upcoming_events WHERE is_team_ride = FALSE;

-- Get rider statistics for a season:
-- SELECT 
--   r.id, r.first_name, r.last_name,
--   COUNT(*) as rides_completed,
--   SUM(ri.distance_km) as total_km
-- FROM rider r
-- JOIN rider_ride rr ON r.id = rr.rider_id
-- JOIN ride ri ON rr.ride_id = ri.id
-- WHERE ri.season_id = 3 AND LOWER(rr.status) = 'yes'
-- GROUP BY r.id, r.first_name, r.last_name
-- ORDER BY total_km DESC;

-- ============================================================
-- SCHEMA VERSION HISTORY
-- ============================================================

-- v1.0: Initial schema
-- v2.0: Added event_status column to ride table
-- v3.0: Consolidated upcoming_rusa_event into ride table
--       - Removed is_team_ride column (use club_id instead)
--       - Added Team Asha as club (code='TA')
--       - Added start_location, start_time, time_limit_hours columns
-- v3.1: Converted ride.date from TEXT to DATE type
--       - Better data integrity and simpler queries
--       - Added NOT NULL constraint to date column
-- v4.0: Consolidated rider_ride_signup into rider_ride table
--       - Added signed_up_at column to rider_ride
--       - Updated status values: 'yes' → 'FINISHED', added 'SIGNED_UP'
--       - Dropped rider_ride_signup table
--       - Single table for complete signup → participation lifecycle

-- ============================================================
-- END OF SCHEMA
-- ============================================================
