-- 027_add_wind_gust_temp_range.sql
-- Per-segment wind GUSTS and temperature RANGE for the ride-analysis weather
-- columns + AI coach. get_historical_stop_wind (services/weather.py) now samples
-- the Open-Meteo hourly `wind_gusts_10m` / `temperature_2m` over each segment's
-- time window (previous stop's arrival hour -> this stop's arrival hour), not a
-- single point. `wind_gust_kmh` is the peak gust over that window; `temp_min_c` /
-- `temp_max_c` are the temperature extremes. `temperature_c` stays the arrival
-- temp. All nullable so old rows (pre-backfill) read as absent and the STOR-02
-- cache treats them as stale -> one deterministic re-fetch heals them.
ALTER TABLE ride_wind_data
    ADD COLUMN IF NOT EXISTS wind_gust_kmh NUMERIC,
    ADD COLUMN IF NOT EXISTS temp_min_c    NUMERIC,
    ADD COLUMN IF NOT EXISTS temp_max_c    NUMERIC;
