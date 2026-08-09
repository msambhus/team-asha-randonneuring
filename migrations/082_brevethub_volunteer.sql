-- 082_brevethub_volunteer.sql
-- Per-event volunteer role slots and rider signups (rp_* only).

ALTER TABLE rp_brevet_event
    ADD COLUMN IF NOT EXISTS volunteer_enabled BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS rp_volunteer_slot (
    id          SERIAL PRIMARY KEY,
    event_id    INTEGER NOT NULL REFERENCES rp_brevet_event(id) ON DELETE CASCADE,
    role_name   TEXT NOT NULL,
    description TEXT,
    capacity    INTEGER NOT NULL DEFAULT 1 CHECK (capacity >= 1),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS rp_volunteer_slot_event_idx
    ON rp_volunteer_slot (event_id, sort_order);

CREATE TABLE IF NOT EXISTS rp_volunteer_signup (
    id           SERIAL PRIMARY KEY,
    slot_id      INTEGER NOT NULL REFERENCES rp_volunteer_slot(id) ON DELETE CASCADE,
    rider_id     INTEGER NOT NULL REFERENCES rp_rider(id),
    status       TEXT NOT NULL DEFAULT 'confirmed',
    signed_up_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at  TIMESTAMPTZ,
    approved_by  TEXT,
    notes        TEXT,
    UNIQUE (slot_id, rider_id)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'rp_volunteer_signup_status_check'
    ) THEN
        ALTER TABLE rp_volunteer_signup
            ADD CONSTRAINT rp_volunteer_signup_status_check
            CHECK (status IN ('confirmed', 'exception', 'withdrawn'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS rp_volunteer_signup_rider_idx
    ON rp_volunteer_signup (rider_id);

CREATE INDEX IF NOT EXISTS rp_volunteer_signup_slot_idx
    ON rp_volunteer_signup (slot_id);
