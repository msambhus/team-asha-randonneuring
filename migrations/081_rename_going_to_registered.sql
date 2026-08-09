-- 081_rename_going_to_registered.sql
-- Rename ride sign-up status going → registered (BrevetHub lowercase, Team Asha uppercase).
-- Also expands rp_event_signup_status_check to the full current lifecycle set.
--
-- Apply out-of-band before or with the code deploy that uses RideStatus.REGISTERED.
-- Order matters: drop the old CHECK before UPDATE, then re-add the expanded CHECK.

-- Drop legacy status CHECK so new values can be written
ALTER TABLE rp_event_signup DROP CONSTRAINT IF EXISTS rp_event_signup_status_check;

-- BrevetHub participation rows
UPDATE rp_event_signup SET status = 'registered' WHERE status = 'going';

-- Team Asha participation rows
UPDATE rider_ride SET status = 'REGISTERED' WHERE status = 'GOING';
UPDATE rider_ride SET status = 'REGISTERED' WHERE status = 'SIGNED_UP';

-- Re-add status CHECK with registered + withdrawal lifecycle values
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'rp_event_signup_status_check'
    ) THEN
        ALTER TABLE rp_event_signup
            ADD CONSTRAINT rp_event_signup_status_check
            CHECK (status IN (
                'interested', 'maybe', 'registered', 'withdraw',
                'withdrawal_requested', 'rejected',
                'finished', 'dnf', 'dns', 'otl'
            ));
    END IF;
END $$;
