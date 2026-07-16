-- 038_brevethub_brevet_weather.sql
-- BrevetHub M7 (weather): the point-forecast cache for upcoming brevets.
--
-- rp_brevet_weather stores ONE raw Open-Meteo point forecast per cached brevet
-- (rp_brevet_event) and date. The weather cron (/cron/fetch-brevet-weather) warms
-- it OFF the request path — it resolves each near-term event's RUSA region to an
-- approximate start coordinate, fetches a keyless Open-Meteo daily forecast, and
-- upserts the raw JSON here. The calendar READS this cache only and never fetches
-- Open-Meteo on a page load (the TA-237 lesson: weather must stay off the hot
-- path). Public data (event-level, not per-rider) — one row serves every viewer;
-- fetched_at exposes staleness, and a cron fetch failure keeps the last-good row.
--
-- Only near-term events (within Open-Meteo's ~16-day horizon) ever get a row; a
-- brevet further out simply has no cache row and the calendar shows an honest
-- "forecast not available yet" state rather than a fabricated value.
--
-- Strictly additive + idempotent + rp_*-only, exactly like migrations 033/035/036/
-- 037: a guarded CREATE TABLE plus a guarded index, referencing only rp_* tables.
-- Applying (or re-applying) it cannot alter any Team Asha table; old code that
-- predates the table simply ignores it, so it is safe to apply ahead of the deploy.

-- --------------------------------------------------------------------------- --
-- rp_brevet_weather — cached point forecast per (event, date).
--
-- weather_data holds the raw Open-Meteo daily-forecast JSON verbatim; the read
-- path summarizes it in-process (shared/weather.py summarize_point_forecast) so the
-- stored shape stays source-of-truth and the display payload can evolve without a
-- re-fetch. UNIQUE(event_id, forecast_date) makes the cron's upsert idempotent.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_brevet_weather (
    id             SERIAL PRIMARY KEY,
    event_id       INTEGER NOT NULL REFERENCES rp_brevet_event(id),
    forecast_date  DATE NOT NULL,
    weather_data   JSONB NOT NULL,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (event_id, forecast_date)
);

CREATE INDEX IF NOT EXISTS rp_brevet_weather_event_idx ON rp_brevet_weather (event_id);
