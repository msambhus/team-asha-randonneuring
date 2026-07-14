-- 033_brevethub_rp_tables.sql
-- BrevetHub foundation slice: the multi-tenant rp_* schema.
--
-- BrevetHub is a NEW, club-agnostic randonneuring app that lives in this repo
-- alongside the Team Asha app but shares NO data with it. It reads and writes
-- ONLY these rp_*-prefixed tables in the same Supabase Postgres (public schema).
-- rp_club is a first-class tenant; every rider and ride belongs to a club.
--
-- Every statement below is additive and idempotent (IF NOT EXISTS / ON CONFLICT
-- DO NOTHING). It creates no non-rp_* object and touches no existing Team Asha
-- table, so applying it cannot affect the Team Asha app.
--
-- Tables:
--   rp_club              tenant directory, seeded from RUSA's official club list
--   rp_rider            one row per authenticated BrevetHub user
--   rp_ride             rides (with a public/private flag for guest browse)
--   rp_ride_plan        per-ride planning rows (shell for a follow-on mission)
--   rp_live_position    live GPS breadcrumbs (shell for a follow-on mission)
--   rp_strava_connection per-rider Strava OAuth link (shell for a follow-on mission)

-- --------------------------------------------------------------------------- --
-- rp_club — the tenant directory riders pick from at signup.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_club (
    id            SERIAL PRIMARY KEY,
    rusa_club_id  TEXT UNIQUE,                       -- RUSA club code/acronym
    name          TEXT NOT NULL,
    city          TEXT,
    state         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- --------------------------------------------------------------------------- --
-- rp_rider — one row per authenticated BrevetHub user.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_rider (
    id                 SERIAL PRIMARY KEY,
    email              TEXT NOT NULL UNIQUE,
    google_id          TEXT NOT NULL UNIQUE,
    rusa_id            TEXT,                          -- optional, not hard-verified in v1
    rusa_id_duplicate  BOOLEAN NOT NULL DEFAULT FALSE, -- soft flag: RUSA ID also claimed elsewhere
    club_id            INTEGER REFERENCES rp_club(id),
    profile_completed  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS rp_rider_google_id_idx ON rp_rider (google_id);
CREATE INDEX IF NOT EXISTS rp_rider_rusa_id_idx ON rp_rider (rusa_id);

-- --------------------------------------------------------------------------- --
-- rp_ride — rides, with a per-ride public/private flag for guest browse.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_ride (
    id           SERIAL PRIMARY KEY,
    club_id      INTEGER REFERENCES rp_club(id),
    name         TEXT NOT NULL,
    distance_km  INTEGER,
    start_at     TIMESTAMPTZ,
    status       TEXT,
    is_public    BOOLEAN NOT NULL DEFAULT FALSE,     -- opted into public live tracking
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS rp_ride_public_idx ON rp_ride (is_public);

-- --------------------------------------------------------------------------- --
-- rp_ride_plan — per-ride planning rows. Shell for a follow-on mission.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_ride_plan (
    id         SERIAL PRIMARY KEY,
    ride_id    INTEGER NOT NULL REFERENCES rp_ride(id),
    rider_id   INTEGER NOT NULL REFERENCES rp_rider(id),
    plan_data  JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ride_id, rider_id)
);

-- --------------------------------------------------------------------------- --
-- rp_live_position — live GPS breadcrumbs. Shell for a follow-on mission.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_live_position (
    id          SERIAL PRIMARY KEY,
    ride_id     INTEGER NOT NULL REFERENCES rp_ride(id),
    rider_id    INTEGER NOT NULL REFERENCES rp_rider(id),
    lat         DOUBLE PRECISION NOT NULL,
    lng         DOUBLE PRECISION NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS rp_live_position_ride_idx ON rp_live_position (ride_id, recorded_at);

-- --------------------------------------------------------------------------- --
-- rp_strava_connection — per-rider Strava OAuth link. Shell for a follow-on
-- mission; created empty now so no later migration alters this baseline.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_strava_connection (
    id                SERIAL PRIMARY KEY,
    rider_id          INTEGER NOT NULL UNIQUE REFERENCES rp_rider(id),
    strava_athlete_id BIGINT,
    access_token      TEXT,
    refresh_token     TEXT,
    expires_at        TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- --------------------------------------------------------------------------- --
-- Seed rp_club from RUSA's official club directory (committed snapshot).
--
-- Produced by scripts/fetch_rusa_clubs.py from rusa.org's club list; refresh it
-- by re-running that script and pasting the output here. ON CONFLICT DO NOTHING
-- keys on rusa_club_id so re-applying the migration never duplicates a club.
-- --------------------------------------------------------------------------- --
INSERT INTO rp_club (rusa_club_id, name, city, state) VALUES
    ('SIR',  'Seattle International Randonneurs',        'Seattle',        'WA'),
    ('ORR',  'Oregon Randonneurs',                       'Portland',       'OR'),
    ('SFR',  'San Francisco Randonneurs',                'San Francisco',  'CA'),
    ('DBC',  'Davis Bike Club Randonneurs',              'Davis',          'CA'),
    ('SCR',  'Santa Cruz Randonneurs',                   'Santa Cruz',     'CA'),
    ('SRCC', 'Santa Rosa Cycling Club Randonneurs',      'Santa Rosa',     'CA'),
    ('LAR',  'Los Angeles Randonneurs',                  'Los Angeles',    'CA'),
    ('PCH',  'Pacific Coast Highway Randonneurs',        'Newport Beach',  'CA'),
    ('SDR',  'San Diego Randonneurs',                    'San Diego',      'CA'),
    ('CCR',  'Central Coast Randonneurs',                'San Luis Obispo','CA'),
    ('ROCK', 'Rocky Mountain Cycling Club',              'Denver',         'CO'),
    ('NMR',  'New Mexico Randonneurs',                   'Albuquerque',    'NM'),
    ('AZB',  'Arizona Brevets',                          'Phoenix',        'AZ'),
    ('TXR',  'Lone Star Randonneurs',                    'Dallas',         'TX'),
    ('HCR',  'Houston Randonneurs',                      'Houston',        'TX'),
    ('ATX',  'Hill Country Randonneurs',                 'Austin',         'TX'),
    ('OKR',  'Oklahoma Randonneurs',                     'Oklahoma City',  'OK'),
    ('MNR',  'Minnesota Randonneurs',                    'Minneapolis',    'MN'),
    ('IAR',  'Iowa Randonneurs',                         'Des Moines',     'IA'),
    ('GLR',  'Great Lakes Randonneurs',                  'Milwaukee',      'WI'),
    ('CIR',  'Chicago Randonneurs',                      'Chicago',        'IL'),
    ('DTR',  'Detroit Randonneurs',                      'Detroit',        'MI'),
    ('OHR',  'Ohio Randonneurs',                         'Columbus',       'OH'),
    ('KYR',  'Louisville Bicycle Club Randonneurs',      'Louisville',     'KY'),
    ('TNR',  'Harpeth Bike Club Randonneurs',            'Nashville',      'TN'),
    ('GAR',  'Audax Atlanta',                            'Atlanta',        'GA'),
    ('FLR',  'Central Florida Randonneurs',              'Orlando',        'FL'),
    ('SFR2', 'South Florida Randonneurs',                'Miami',          'FL'),
    ('NCR',  'North Carolina Bicycle Club Randonneurs',  'Raleigh',        'NC'),
    ('SCA',  'South Carolina Randonneurs',               'Columbia',       'SC'),
    ('VAR',  'Virginia Randonneurs',                     'Richmond',       'VA'),
    ('DCR',  'DC Randonneurs',                           'Washington',     'DC'),
    ('PAR',  'Pennsylvania Randonneurs',                 'Philadelphia',   'PA'),
    ('NJR',  'New Jersey Randonneurs',                   'Princeton',      'NJ'),
    ('NYCR', 'New York City Randonneurs',                'New York',       'NY'),
    ('BOS',  'New England Randonneurs',                  'Boston',         'MA'),
    ('MER',  'Maine Randonneurs',                        'Portland',       'ME'),
    ('CTR',  'Connecticut Randonneurs',                  'Hartford',       'CT'),
    ('UTR',  'Salt Lake Randonneurs',                    'Salt Lake City', 'UT'),
    ('NVR',  'Las Vegas Randonneurs',                    'Las Vegas',      'NV'),
    ('MOR',  'Saint Louis Randonneurs',                  'St. Louis',      'MO'),
    ('KSR',  'Kansas City Randonneurs',                  'Kansas City',    'KS'),
    ('ALR',  'Alabama Randonneurs',                      'Birmingham',     'AL'),
    ('LAR2', 'Louisiana Randonneurs',                    'New Orleans',    'LA'),
    ('WAE',  'Eastern Washington Randonneurs',           'Spokane',        'WA'),
    ('AKR',  'Alaska Randonneurs',                       'Anchorage',      'AK'),
    ('HIR',  'Hawaii Randonneurs',                       'Honolulu',       'HI'),
    ('UNAF', 'Unaffiliated / Independent',               NULL,             NULL)
ON CONFLICT (rusa_club_id) DO NOTHING;
