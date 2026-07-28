-- Resume a rider-owned Garmin activity backfill across bounded serverless
-- requests. The cursor is an offset into Garmin's newest-first cycling feed.
ALTER TABLE garmin_connection
    ADD COLUMN IF NOT EXISTS activity_sync_cursor INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS activity_sync_since DATE,
    ADD COLUMN IF NOT EXISTS activity_history_complete BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN garmin_connection.activity_sync_cursor IS
    'Next newest-first Garmin cycling activity offset for the active backfill';
COMMENT ON COLUMN garmin_connection.activity_sync_since IS
    'Inclusive history boundary fixed when the current Garmin backfill started';
COMMENT ON COLUMN garmin_connection.activity_history_complete IS
    'True after the current Garmin activity history boundary has been reached';

