-- 043_brevethub_brevet_route_weather.sql
-- BrevetHub along-route weather cache: the dense per-sample Open-Meteo forecast for
-- a cached brevet route on its date, warmed OFF the request path.
--
-- rp_brevet_route_weather stores ONE forecast bundle per (brevet event, date): the
-- raw Open-Meteo forecast list sampled at a dense (15 km) interval, plus the aligned
-- sample points it was sampled at. The warm cron
-- (/cron/warm-brevet-route-weather) fetches the brevet's RWGPS route, samples it,
-- batch-fetches Open-Meteo, and upserts here; the guest /plan page READS this cache
-- only and never calls Open-Meteo/RWGPS live (the TA-237 lesson: weather must stay
-- off the hot path). Public data (route-level, not per-rider) — one row serves every
-- rider; fetched_at exposes staleness, and a cron fetch failure keeps the last-good
-- row (the cron simply skips the upsert), so a user request never hangs.
--
-- Mirrors Team Asha's route_weather_cache column shape (weather_data + sample_points
-- JSONB) but is keyed on event_id (a BrevetHub calendar brevet) rather than a bare
-- RWGPS route id, and lives in an rp_-prefixed tenant table. Only near-term events
-- (within Open-Meteo's ~16-day horizon) ever get a row; a brevet further out simply
-- has no cache row and the plan page renders with no Wind column (graceful fallback).
--
-- Strictly additive + idempotent + rp_*-only, exactly like migrations 033/…/041/042:
-- one guarded table creation plus a guarded index, referencing only rp_* tables
-- (every statement carries IF NOT EXISTS). Applying (or re-applying) it cannot alter
-- any Team Asha table; old code that predates the table simply ignores it, so it is
-- safe to apply ahead of the code deploy.

-- --------------------------------------------------------------------------- --
-- rp_brevet_route_weather — cached along-route forecast per (event, date).
--
-- weather_data holds the raw Open-Meteo per-sample forecast JSON list verbatim;
-- sample_points holds the aligned [{lat, lng, distance_m}] the route was sampled at,
-- so the read path can map each stop's route distance to the nearest forecast and
-- compute per-stop wind in-process (shared/weather.py compute_stop_winds). Keeping
-- the stored shape source-of-truth lets the display payload evolve without a
-- re-fetch. UNIQUE(event_id, forecast_date) makes the cron's upsert idempotent.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_brevet_route_weather (
    id             SERIAL PRIMARY KEY,
    event_id       INTEGER NOT NULL REFERENCES rp_brevet_event(id),
    forecast_date  DATE NOT NULL,
    weather_data   JSONB NOT NULL,
    sample_points  JSONB NOT NULL,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (event_id, forecast_date)
);

CREATE INDEX IF NOT EXISTS rp_brevet_route_weather_lookup_idx
    ON rp_brevet_route_weather (event_id, forecast_date);
