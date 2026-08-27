-- 084_brevethub_worker_ride.sql
-- Worker ride: volunteers may ride the same route during the event week (Sun–Sat).

ALTER TABLE rp_brevet_event
    ADD COLUMN IF NOT EXISTS worker_ride_enabled BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE rp_volunteer_slot
    ADD COLUMN IF NOT EXISTS allows_ride_on_event_day BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE rp_event_signup
    ADD COLUMN IF NOT EXISTS ride_mode TEXT,
    ADD COLUMN IF NOT EXISTS ride_mode_ack_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'rp_event_signup_ride_mode_check'
    ) THEN
        ALTER TABLE rp_event_signup
            ADD CONSTRAINT rp_event_signup_ride_mode_check
            CHECK (ride_mode IS NULL OR ride_mode IN ('event_day', 'worker_ride'));
    END IF;
END $$;
