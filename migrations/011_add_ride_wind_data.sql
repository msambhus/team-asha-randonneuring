-- Migration 011: Add ride_wind_data table for historical and forecast wind storage
-- Stores per-stop wind data for brevet rides so completed rides never re-fetch from archive API.

CREATE TABLE IF NOT EXISTS ride_wind_data (
    id SERIAL PRIMARY KEY,
    ride_id INTEGER NOT NULL REFERENCES ride(id) ON DELETE CASCADE,
    stop_order INTEGER NOT NULL,
    stop_name TEXT,
    wind_speed_kmh NUMERIC,
    wind_direction_deg INTEGER,
    headwind_kmh NUMERIC,
    crosswind_kmh NUMERIC,
    wind_type TEXT CHECK (wind_type IN ('headwind', 'tailwind', 'crosswind')),
    temperature_c NUMERIC,
    conditions TEXT,
    data_source TEXT NOT NULL CHECK (data_source IN ('archive', 'forecast_past_days')),
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (ride_id, stop_order)
);

CREATE INDEX IF NOT EXISTS idx_ride_wind_data_ride_id ON ride_wind_data(ride_id);
