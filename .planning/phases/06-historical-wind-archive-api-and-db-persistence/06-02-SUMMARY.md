---
phase: 06-historical-wind-archive-api-and-db-persistence
plan: "02"
subsystem: weather-service
tags: [historical-wind, archive-api, db-persistence, tdd, open-meteo, stor-02]
dependency_graph:
  requires: ["06-01"]
  provides: ["fetch_historical_wind", "get_historical_stop_wind", "STOR-02"]
  affects: ["services/weather.py", "tests/test_weather.py"]
tech_stack:
  added: []
  patterns: ["TDD red-green", "DB-check-before-fetch", "archive-API-with-fallback"]
key_files:
  created: []
  modified:
    - services/weather.py
    - tests/test_weather.py
decisions:
  - "fetch_historical_wind returns (data, source) tuple so callers always know provenance"
  - "ARCHIVE_LAG_DAYS=5: ride_date <= today-5 routes to archive; strictly newer uses forecast past_days"
  - "get_historical_stop_wind checks DB before any network call (STOR-02 read-through cache)"
  - "save_ride_wind_data called after successful fetch so second call for same ride_id reads from DB"
  - "conditions field populated as empty string for historical rows (archive API has no WMO codes)"
  - "from models import ... at module level in weather.py — avoids circular imports at import time"
metrics:
  duration_minutes: 2
  completed_date: "2026-03-23"
  tasks_completed: 2
  tasks_total: 2
  files_changed: 2
---

# Phase 06 Plan 02: Archive API Fetch and Historical Stop Wind Orchestration Summary

**One-liner:** Archive/forecast-past-days routing via 5-day ERA5 lag cutoff with DB-check-before-fetch caching in `get_historical_stop_wind` (STOR-02).

## What Was Built

Two new functions in `services/weather.py` plus two new constants that implement WIND-07, WIND-08, and STOR-02:

**`fetch_historical_wind(stop_coords, ride_date)`** — Routes based on whether `ride_date <= date.today() - 5`:
- Old rides (5+ days): calls `_fetch_archive_wind` which hits `archive-api.open-meteo.com/v1/archive` with `start_date=end_date=ride_date`
- Recent rides (1-4 days): calls `_fetch_forecast_past_days_wind` which hits `api.open-meteo.com/v1/forecast` with `past_days` parameter
- Both helpers send comma-separated lat/lng strings for batch fetching and normalize single-dict responses to lists
- Returns `(weather_data_list, data_source)` tuple where `data_source` is `'archive'` or `'forecast_past_days'`

**`get_historical_stop_wind(stops, track_points, ride_date, ride_id=None)`** — Full orchestration:
1. Early return `(None, None)` on empty track_points
2. DB check (STOR-02): if `ride_id` given and `get_ride_wind_data(ride_id)` returns rows, return stored rows immediately — no API call
3. Interpolate stop coordinates via existing `get_stop_coordinates()`
4. Call `fetch_historical_wind()` in try/except — return `(None, None)` on any error
5. Compute per-stop bearing, headwind, crosswind, wind_type for each stop
6. Build `wind_rows` list of dicts with all STOR-01 columns: `stop_order`, `stop_name`, `wind_speed_kmh`, `wind_direction_deg`, `headwind_kmh`, `crosswind_kmh`, `wind_type`, `temperature_c`, `conditions`, `data_source`
7. DB save (STOR-02): call `save_ride_wind_data(ride_id, wind_rows)` so the next call reads from DB
8. Return `(wind_rows, data_source)`

**Constants added:**
- `OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"`
- `ARCHIVE_LAG_DAYS = 5`

## Tests Added

**`TestFetchHistoricalWind`** (6 tests):
- `test_old_ride_uses_archive` — ride 10 days ago: URL contains archive domain, params have start_date/end_date, source='archive'
- `test_recent_ride_uses_past_days` — ride 3 days ago: URL contains forecast domain, params have past_days, source='forecast_past_days'
- `test_lag_boundary_uses_archive` — ride exactly 5 days ago: source='archive' (boundary is inclusive)
- `test_archive_single_dict_normalized` — single-dict response wrapped in list
- `test_batch_coords` — 3 coordinates produce comma-separated string with 2 commas
- `test_http_error_propagates` — HTTPError from raise_for_status propagates to caller

**`TestGetHistoricalStopWind`** (7 tests):
- `test_empty_track_points_returns_none` — returns (None, None) immediately
- `test_api_error_returns_none` — exception in fetch_historical_wind yields (None, None), not raised
- `test_returns_wind_rows_with_classification` — happy path returns 2 rows with data_source='archive'
- `test_row_keys_complete` — all 10 required keys present in each row
- `test_bearing_from_consecutive_coords` — west wind on eastward route produces non-trivial headwind
- `test_db_hit_skips_api_call` (STOR-02) — 2 DB rows: fetch_historical_wind.assert_not_called()
- `test_db_miss_fetches_and_saves` (STOR-02) — empty DB: fetch called, save called with (42, wind_rows)

Total: 13 new tests (6 + 7). Full suite: 316 passed, 6 skipped, 0 failures.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 1 (RED+GREEN) | 1413593 | `feat(06-02): implement fetch_historical_wind with archive/forecast-past-days routing` |
| Task 2 (RED+GREEN) | 1fbd8d7 | `feat(06-02): implement get_historical_stop_wind with DB-check-before-fetch (STOR-02)` |

## Deviations from Plan

None — plan executed exactly as written.

## Success Criteria Verification

- [x] `OPEN_METEO_ARCHIVE_URL` and `ARCHIVE_LAG_DAYS` constants defined in `weather.py`
- [x] Rides >5 days old fetch from archive API; rides <=4 days old use forecast past_days (WIND-07, WIND-08)
- [x] `get_historical_stop_wind` returns per-stop wind dicts with all required fields
- [x] `get_historical_stop_wind` checks `ride_wind_data` DB before calling archive API (STOR-02)
- [x] `get_historical_stop_wind` saves wind rows to `ride_wind_data` after successful fetch (STOR-02)
- [x] A second request for the same `ride_id` reads from DB — archive API not called again
- [x] All tests pass including existing test suite (no regressions) — 316 passed

## Self-Check: PASSED
