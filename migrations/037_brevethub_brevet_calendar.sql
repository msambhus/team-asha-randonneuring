-- 037_brevethub_brevet_calendar.sql
-- BrevetHub M5 (brevet calendar + rider sign-up): the upcoming-brevets cache and
-- the per-rider participation table.
--
-- rp_brevet_event caches the RUSA national-feed scrape (parsed by the shared
-- shared/rusa_calendar.py engine) so /calendar does not re-scrape on every load.
-- The national feed carries NO per-event start time or start location, so those
-- two columns exist but are populated only when a source actually provides them;
-- the calendar renders an honest placeholder otherwise and never fabricates them.
--
-- rp_event_signup records a rider's interest/going/withdraw on a cached brevet —
-- one row per (event, rider), transitioning status in place.
--
-- Strictly additive + idempotent + rp_*-only, exactly like migrations 033/035/036:
-- two additive table creations plus guarded indexes (every statement carries
-- IF NOT EXISTS), all referencing only rp_* tables. Applying (or re-applying) it
-- cannot alter any Team
-- Asha table; old code that predates these tables simply ignores them, so it is
-- safe to apply ahead of the code deploy.

-- --------------------------------------------------------------------------- --
-- rp_brevet_event — cached upcoming RUSA brevets.
--
-- The natural key (date, name, distance_km) dedups the same brevet across repeat
-- scrapes; club_id is a nullable FK to rp_club (the RUSA national feed's region
-- LABEL does not map cleanly to a specific club for a generic multi-club app, so
-- region holds the raw label and club_id stays NULL until a club is resolved).
-- start_location / start_time are nullable and NULL for national-feed events.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_brevet_event (
    id                SERIAL PRIMARY KEY,
    rusa_route_id     TEXT,                              -- RUSA route id (rtid), when present
    name              TEXT NOT NULL,
    date              DATE NOT NULL,
    distance_km       INTEGER NOT NULL,
    region            TEXT,                              -- RUSA region label, e.g. "CA: San Francisco"
    club_id           INTEGER REFERENCES rp_club(id),    -- resolved club, when known (nullable)
    ride_type         TEXT,                              -- "ACP brevet" / "RUSA brevet"
    elevation_ft      INTEGER,
    rwgps_url         TEXT,
    start_location    TEXT,                              -- NULL for national-feed events (never fabricated)
    start_time        TEXT,                              -- NULL for national-feed events (never fabricated)
    time_limit_hours  NUMERIC,
    scraped_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (date, name, distance_km)
);

CREATE INDEX IF NOT EXISTS rp_brevet_event_date_idx ON rp_brevet_event (date);
CREATE INDEX IF NOT EXISTS rp_brevet_event_region_idx ON rp_brevet_event (region);

-- --------------------------------------------------------------------------- --
-- rp_event_signup — a rider's participation on a cached brevet. One row per
-- (event, rider); status is BrevetHub's own enum value (interested/going/
-- withdraw/…). WITHDRAW is stored as a status flag rather than a delete so the
-- rider's intent history is preserved.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_event_signup (
    id          SERIAL PRIMARY KEY,
    event_id    INTEGER NOT NULL REFERENCES rp_brevet_event(id),
    rider_id    INTEGER NOT NULL REFERENCES rp_rider(id),
    status      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (event_id, rider_id)
);

CREATE INDEX IF NOT EXISTS rp_event_signup_rider_idx ON rp_event_signup (rider_id);
CREATE INDEX IF NOT EXISTS rp_event_signup_event_idx ON rp_event_signup (event_id);
