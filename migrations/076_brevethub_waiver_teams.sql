-- 076_brevethub_waiver_teams.sql
-- Adds waiver signing enhancements (adult/minor, guardian, e-sig) and
-- team event registration table.

-- --------------------------------------------------------------------------- --
-- rp_rider — optional ride phone + age consent fields
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_rider ADD COLUMN IF NOT EXISTS ride_phone TEXT;
ALTER TABLE rp_rider ADD COLUMN IF NOT EXISTS birth_date DATE;

-- --------------------------------------------------------------------------- --
-- rp_waiver_acceptance — enhanced signing fields
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_waiver_acceptance ADD COLUMN IF NOT EXISTS waiver_method TEXT NOT NULL DEFAULT 'in_app';
  -- 'in_app' | 'smartwaiver'
ALTER TABLE rp_waiver_acceptance ADD COLUMN IF NOT EXISTS is_minor BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE rp_waiver_acceptance ADD COLUMN IF NOT EXISTS signatory_name TEXT;
  -- typed full name as e-signature
ALTER TABLE rp_waiver_acceptance ADD COLUMN IF NOT EXISTS guardian_name TEXT;
ALTER TABLE rp_waiver_acceptance ADD COLUMN IF NOT EXISTS guardian_phone TEXT;
ALTER TABLE rp_waiver_acceptance ADD COLUMN IF NOT EXISTS age_certified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE rp_waiver_acceptance ADD COLUMN IF NOT EXISTS esign_consented BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE rp_waiver_acceptance ADD COLUMN IF NOT EXISTS smartwaiver_id TEXT;
  -- SmartWaiver submission ID if waiver_method = 'smartwaiver'

-- --------------------------------------------------------------------------- --
-- rp_event_signup — track ride phone at registration time (may differ from profile)
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_event_signup ADD COLUMN IF NOT EXISTS ride_phone TEXT;

-- --------------------------------------------------------------------------- --
-- rp_team_registration — team events (Flèche, Dart, Dart Populaire)
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_team_registration (
    id                  SERIAL PRIMARY KEY,
    event_id            INTEGER REFERENCES rp_brevet_event(id),
    team_name           TEXT NOT NULL,
    team_event_type     TEXT,            -- 'fleche' | 'dart' | 'dart_populaire'
    captain_rider_id    INTEGER REFERENCES rp_rider(id),
    proof_method        TEXT,            -- 'brevet_card' | 'gps_track'
    rwgps_url           TEXT,
    notes               TEXT,
    fee_paid            BOOLEAN NOT NULL DEFAULT FALSE,
    registration_status TEXT NOT NULL DEFAULT 'pending',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rp_team_member (
    id                    SERIAL PRIMARY KEY,
    team_registration_id  INTEGER NOT NULL REFERENCES rp_team_registration(id) ON DELETE CASCADE,
    rider_id              INTEGER REFERENCES rp_rider(id),
    rusa_id               TEXT,
    first_name            TEXT,
    last_name             TEXT,
    member_order          INTEGER NOT NULL DEFAULT 2,  -- captain = 1, members start at 2
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS rp_team_registration_event_idx ON rp_team_registration(event_id);
CREATE INDEX IF NOT EXISTS rp_team_registration_captain_idx ON rp_team_registration(captain_rider_id);
CREATE INDEX IF NOT EXISTS rp_team_member_team_idx ON rp_team_member(team_registration_id);
