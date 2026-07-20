-- 045_brevethub_signup_lifecycle.sql
-- BrevetHub ride sign-up lifecycle + result reconciliation: the post-ride result
-- columns and the status domain constraint on the participation table.
--
-- rp_event_signup gains a finish_time column (the official RUSA finish, filled
-- only by the daily RUSA-sync cron — never rider-supplied), plus a CHECK
-- constraint pinning status to BrevetHub's eight lowercase lifecycle values
-- (interested / maybe / going / withdraw / finished / dnf / dns / otl).
--
-- Strictly additive + idempotent + rp_*-only, exactly like migrations 037/042:
-- an ADD COLUMN IF NOT EXISTS plus a guarded ADD CONSTRAINT (Postgres has no
-- ADD CONSTRAINT IF NOT EXISTS, so it is wrapped in a re-runnable DO block that
-- checks pg_constraint first), all referencing only rp_* tables. Applying (or
-- re-applying) it cannot alter any parent-app table; old code that predates the
-- column simply ignores it, so it is safe to apply before or after the code
-- deploy. There is no migration runner here — apply it out-of-band (the PR body
-- carries the exact SQL).

-- --------------------------------------------------------------------------- --
-- finish_time — the official RUSA finish time for a completed brevet, as the raw
-- "HH:MM" / "DDdHH:MM" RUSA string (TEXT, never parsed into an interval so the
-- display is byte-faithful to RUSA). NULL until the RUSA-sync cron backfills it;
-- the rider self-service result endpoint never writes a real value here.
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_event_signup ADD COLUMN IF NOT EXISTS finish_time TEXT;

-- --------------------------------------------------------------------------- --
-- status domain — pin the participation status to the eight lowercase lifecycle
-- values. Idempotent: the DO block adds the constraint only when it is absent, so
-- re-applying the migration is a no-op. Named explicitly so the guard can find it.
-- --------------------------------------------------------------------------- --
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'rp_event_signup_status_check'
    ) THEN
        ALTER TABLE rp_event_signup
            ADD CONSTRAINT rp_event_signup_status_check
            CHECK (status IN (
                'interested', 'maybe', 'going', 'withdraw',
                'finished', 'dnf', 'dns', 'otl'
            ));
    END IF;
END $$;
