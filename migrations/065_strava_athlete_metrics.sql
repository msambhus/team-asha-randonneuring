-- Private athlete-level metrics supplied by Strava.
-- This is deliberately kept on the rider-owned server-side connection row.
ALTER TABLE strava_connection
    ADD COLUMN IF NOT EXISTS ftp NUMERIC;

COMMENT ON COLUMN strava_connection.ftp IS
    'Functional threshold power supplied by the authenticated Strava athlete profile';
