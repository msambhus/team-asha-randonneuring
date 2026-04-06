-- migrations/017_add_strava_activity_fields.sql
-- Adds missing Strava activity fields to strava_activity table.
-- All columns were available from the Strava API but not previously stored.
-- average_cadence is the only new field exposed on the cohort comparison page (TA-87).
-- All others are stored for future feature use.

ALTER TABLE strava_activity
  ADD COLUMN IF NOT EXISTS average_cadence       REAL,
  ADD COLUMN IF NOT EXISTS average_temp          REAL,
  ADD COLUMN IF NOT EXISTS calories              REAL,
  ADD COLUMN IF NOT EXISTS pr_count              INTEGER,
  ADD COLUMN IF NOT EXISTS achievement_count     INTEGER,
  ADD COLUMN IF NOT EXISTS gear_id               TEXT,
  ADD COLUMN IF NOT EXISTS elev_high             REAL,
  ADD COLUMN IF NOT EXISTS elev_low              REAL,
  ADD COLUMN IF NOT EXISTS trainer               BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS commute               BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS workout_type          INTEGER,
  ADD COLUMN IF NOT EXISTS map_summary_polyline  TEXT,
  ADD COLUMN IF NOT EXISTS start_latlng          TEXT,
  ADD COLUMN IF NOT EXISTS end_latlng            TEXT;

COMMENT ON COLUMN strava_activity.average_cadence      IS 'Avg pedaling cadence in RPM';
COMMENT ON COLUMN strava_activity.average_temp         IS 'Avg temperature in Celsius';
COMMENT ON COLUMN strava_activity.calories             IS 'Calories burned (from Strava)';
COMMENT ON COLUMN strava_activity.pr_count             IS 'Number of personal records set';
COMMENT ON COLUMN strava_activity.achievement_count    IS 'Number of achievements earned';
COMMENT ON COLUMN strava_activity.gear_id              IS 'Strava gear ID (e.g. b1234567)';
COMMENT ON COLUMN strava_activity.elev_high            IS 'Max elevation reached in meters';
COMMENT ON COLUMN strava_activity.elev_low             IS 'Min elevation reached in meters';
COMMENT ON COLUMN strava_activity.trainer              IS 'TRUE if recorded on a trainer (indoor)';
COMMENT ON COLUMN strava_activity.commute              IS 'TRUE if marked as a commute';
COMMENT ON COLUMN strava_activity.workout_type         IS 'Strava workout_type enum: 10=race, 12=long ride, etc.';
COMMENT ON COLUMN strava_activity.map_summary_polyline IS 'Encoded polyline from map.summary_polyline';
COMMENT ON COLUMN strava_activity.start_latlng         IS 'JSON-encoded [lat, lng] of activity start';
COMMENT ON COLUMN strava_activity.end_latlng           IS 'JSON-encoded [lat, lng] of activity end';
