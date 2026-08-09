-- 075_brevethub_registration.sql
-- BrevetHub brevet registration (profile, waivers, event metadata) — no payments.
--
-- Extends rp_rider with contact/emergency fields, rp_brevet_event with registration
-- metadata, rp_event_signup with registration_status, and adds waiver versioning +
-- per-event acceptance audit rows. Strictly additive + idempotent + rp_*-only.

-- --------------------------------------------------------------------------- --
-- rp_rider — registration profile fields
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_rider ADD COLUMN IF NOT EXISTS first_name TEXT;
ALTER TABLE rp_rider ADD COLUMN IF NOT EXISTS last_name TEXT;
ALTER TABLE rp_rider ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE rp_rider ADD COLUMN IF NOT EXISTS city TEXT;
ALTER TABLE rp_rider ADD COLUMN IF NOT EXISTS emergency_name TEXT;
ALTER TABLE rp_rider ADD COLUMN IF NOT EXISTS emergency_phone TEXT;
ALTER TABLE rp_rider ADD COLUMN IF NOT EXISTS sfr_member_year INTEGER;

-- --------------------------------------------------------------------------- --
-- rp_brevet_event — registration metadata (populated from club sheets / admin)
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_brevet_event ADD COLUMN IF NOT EXISTS fee_cents INTEGER;
ALTER TABLE rp_brevet_event ADD COLUMN IF NOT EXISTS registration_deadline DATE;
ALTER TABLE rp_brevet_event ADD COLUMN IF NOT EXISTS capacity INTEGER;
ALTER TABLE rp_brevet_event ADD COLUMN IF NOT EXISTS event_summary TEXT;
ALTER TABLE rp_brevet_event ADD COLUMN IF NOT EXISTS registration_enabled BOOLEAN NOT NULL DEFAULT FALSE;

-- --------------------------------------------------------------------------- --
-- rp_event_signup — registration outcome (distinct from ride status)
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_event_signup ADD COLUMN IF NOT EXISTS registration_status TEXT;
ALTER TABLE rp_event_signup ADD COLUMN IF NOT EXISTS registration_confirmed_at TIMESTAMPTZ;
ALTER TABLE rp_event_signup ADD COLUMN IF NOT EXISTS exception_reason TEXT;
ALTER TABLE rp_event_signup ADD COLUMN IF NOT EXISTS confirmation_code TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'rp_event_signup_registration_status_check'
    ) THEN
        ALTER TABLE rp_event_signup
            ADD CONSTRAINT rp_event_signup_registration_status_check
            CHECK (registration_status IS NULL OR registration_status IN (
                'confirmed', 'waitlist', 'exception', 'withdrawn'
            ));
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS rp_event_signup_confirmation_code_idx
    ON rp_event_signup (confirmation_code)
    WHERE confirmation_code IS NOT NULL;

-- --------------------------------------------------------------------------- --
-- rp_waiver_version — versioned waiver text per club (or global default)
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_waiver_version (
    id            SERIAL PRIMARY KEY,
    club_id       INTEGER REFERENCES rp_club(id),
    version_label TEXT NOT NULL,
    waiver_text   TEXT NOT NULL,
    effective_at  DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (club_id, version_label)
);

-- --------------------------------------------------------------------------- --
-- rp_waiver_acceptance — auditable per-event acceptance
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_waiver_acceptance (
    id                 SERIAL PRIMARY KEY,
    event_id           INTEGER NOT NULL REFERENCES rp_brevet_event(id),
    rider_id           INTEGER NOT NULL REFERENCES rp_rider(id),
    waiver_version_id  INTEGER NOT NULL REFERENCES rp_waiver_version(id),
    accepted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    profile_snapshot   JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (event_id, rider_id, waiver_version_id)
);

CREATE INDEX IF NOT EXISTS rp_waiver_acceptance_event_rider_idx
    ON rp_waiver_acceptance (event_id, rider_id);

-- Seed a default SFR waiver (club_id NULL = global fallback).
INSERT INTO rp_waiver_version (club_id, version_label, waiver_text)
SELECT NULL, '2026-default',
       'I voluntarily participate in this San Francisco Randonneurs brevet and acknowledge its inherent risks, including road hazards, traffic, fatigue, weather, and physical strain.' || E'\n\n' ||
       'I release SFR, ACP, RUSA, and their officers and volunteers from all liability arising from my participation.' || E'\n\n' ||
       'I confirm I am medically fit to participate, will follow all applicable traffic laws, abide by SFR and RUSA event rules, and follow volunteer instructions. I consent to emergency medical care if I am unable to make decisions myself.' || E'\n\n' ||
       'I understand that SFR organizers may withdraw any participant for safety reasons at their discretion.'
WHERE NOT EXISTS (
    SELECT 1 FROM rp_waiver_version WHERE club_id IS NULL AND version_label = '2026-default'
);
