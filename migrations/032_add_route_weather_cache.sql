-- 032_add_route_weather_cache.sql
-- Durable weather cache for the async forecast cron (TA-237). Weather used to be
-- fetched LIVE from Open-Meteo on the request path (brevet calendar, live map, weather
-- page, mobile plan). When Open-Meteo began stalling at the TLS handshake it throttled
-- Vercel's shared egress IPs and hung the calendar. This table moves all fetching into
-- an hourly cron (/api/cron/fetch-route-weather) and every page READS from here.
--
-- One row per (route_id, forecast_date): the raw Open-Meteo forecast list sampled at a
-- dense (15 km) interval, plus the sample points it was sampled at, so every read site
-- (calendar wind warnings, live charts/wind, weather page, mobile plan, chat) can rebuild
-- its view from stored data. Public data (route-level, not per-rider) — one row serves
-- every rider. fetched_at exposes staleness; on a cron fetch failure the last-good row is
-- kept and served (the cron simply skips the upsert), so a user request never hangs.

CREATE TABLE IF NOT EXISTS route_weather_cache (
    id            SERIAL PRIMARY KEY,
    route_id      BIGINT NOT NULL,
    forecast_date DATE NOT NULL,
    weather_data  JSONB NOT NULL,
    sample_points JSONB NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (route_id, forecast_date)
);

-- Lookup index for the request-path reads: (route_id, forecast_date).
CREATE INDEX IF NOT EXISTS route_weather_cache_lookup_idx
    ON route_weather_cache (route_id, forecast_date);
