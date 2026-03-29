---
phase: 06-historical-wind-archive-api-and-db-persistence
verified: 2026-03-23T21:07:22Z
status: passed
score: 12/12 must-haves verified
re_verification: false
---

# Phase 06: Historical Wind Archive API and DB Persistence — Verification Report

**Phase Goal:** Historical wind for completed rides is fetched once from the Open-Meteo archive API, persisted to the ride_wind_data table, and never re-fetched on subsequent page loads
**Verified:** 2026-03-23T21:07:22Z
**Status:** PASSED
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

Plan 01 truths (STOR-01 / STOR-02 / STOR-03):

| #  | Truth | Status | Evidence |
|----|-------|--------|---------|
| 1  | ride_wind_data table exists with all required columns including data_source CHECK constraint | VERIFIED | `migrations/011_add_ride_wind_data.sql` — CREATE TABLE IF NOT EXISTS with all 12 columns, UNIQUE(ride_id, stop_order), CHECK (data_source IN ('archive', 'forecast_past_days')) |
| 2  | get_ride_wind_data(ride_id) returns stored wind rows ordered by stop_order | VERIFIED | `models.py:2479-2483` — SELECT * FROM ride_wind_data WHERE ride_id = %s ORDER BY stop_order; fetchall() returns list |
| 3  | save_ride_wind_data(ride_id, wind_rows) inserts per-stop rows with ON CONFLICT DO NOTHING | VERIFIED | `models.py:2512` — ON CONFLICT (ride_id, stop_order) DO NOTHING; conn.commit() called after loop |
| 4  | A second save for the same ride_id + stop_order does not duplicate rows | VERIFIED | ON CONFLICT (ride_id, stop_order) DO NOTHING is the UNIQUE constraint target; test coverage in test_models_wind.py |
| 5  | data_source value is preserved exactly as passed ('archive' or 'forecast_past_days') | VERIFIED | row.get('data_source') passed verbatim in INSERT; CHECK constraint enforces valid values at DB layer |

Plan 02 truths (WIND-07 / WIND-08 / STOR-02):

| #  | Truth | Status | Evidence |
|----|-------|--------|---------|
| 6  | A ride completed 10+ days ago fetches wind from the archive API (archive-api.open-meteo.com) | VERIFIED | `weather.py:354-356` — lag_cutoff = today - 5; ride 10 days ago satisfies ride_date <= lag_cutoff; routes to _fetch_archive_wind which hits OPEN_METEO_ARCHIVE_URL |
| 7  | A ride completed 1-4 days ago fetches wind from the forecast API with past_days parameter | VERIFIED | `weather.py:357-358` — else branch calls _fetch_forecast_past_days_wind; past_days=max(days_ago+1,1) in params |
| 8  | A ride completed exactly 5 days ago uses the archive API (boundary: ride_date <= today - 5) | VERIFIED | `weather.py:355` — `if ride_date <= lag_cutoff` — inclusive boundary; test_lag_boundary_uses_archive confirms source='archive' |
| 9  | fetch_historical_wind returns a tuple of (weather_data_list, data_source_string) | VERIFIED | `weather.py:356,358` — both branches return (result, 'archive') or (result, 'forecast_past_days'); both helpers normalize to list |
| 10 | get_historical_stop_wind returns per-stop dicts with wind_speed_kmh, wind_type, headwind_kmh, crosswind_kmh, data_source | VERIFIED | `weather.py:456-467` — wind_rows dict built with all 10 required keys; test_row_keys_complete asserts presence |
| 11 | A second call to get_historical_stop_wind for the same ride_id returns DB rows without calling fetch_historical_wind (STOR-02) | VERIFIED | `weather.py:380-383` — stored = get_ride_wind_data(ride_id); if stored: return stored immediately; test_db_hit_skips_api_call asserts mock_fetch.assert_not_called() |
| 12 | After a successful fetch, get_historical_stop_wind calls save_ride_wind_data to persist wind rows | VERIFIED | `weather.py:472-474` — save_ride_wind_data(ride_id, wind_rows) called before return; test_db_miss_fetches_and_saves asserts mock_save.assert_called_once() with (42, wind_rows) |

**Score:** 12/12 truths verified

### Required Artifacts

| Artifact | Expected | Level 1: Exists | Level 2: Substantive | Level 3: Wired | Status |
|----------|----------|-----------------|----------------------|----------------|--------|
| `migrations/011_add_ride_wind_data.sql` | Idempotent DDL for ride_wind_data table | Yes | 22 lines; CREATE TABLE IF NOT EXISTS with all columns, UNIQUE, CHECK, INDEX | Referenced in SUMMARY; applied separately (migration file, not auto-run) | VERIFIED |
| `models.py` | get_ride_wind_data and save_ride_wind_data functions | Yes | Both functions at lines 2473-2528; substantive SQL with ON CONFLICT; commit call | Imported by weather.py line 8: `from models import get_ride_wind_data, save_ride_wind_data` | VERIFIED |
| `tests/test_models_wind.py` | Unit tests for wind data persistence | Yes | 326 lines; TestGetRideWindData and TestSaveRideWindData classes with 16 tests | Executed by pytest; all 16 pass | VERIFIED |
| `services/weather.py` | OPEN_METEO_ARCHIVE_URL, ARCHIVE_LAG_DAYS, fetch_historical_wind, get_historical_stop_wind | Yes | Constants at lines 15-16; fetch_historical_wind at 341; get_historical_stop_wind at 361; 192 lines of substantive implementation | Called by route handlers (Phase 07 consumers); tested by 13 new tests | VERIFIED |
| `tests/test_weather.py` | TestFetchHistoricalWind, TestGetHistoricalStopWind test classes | Yes | 1546 total lines; TestFetchHistoricalWind (6 tests) at line 1238; TestGetHistoricalStopWind (7 tests) at line 1413 | Executed by pytest; all 13 pass | VERIFIED |

### Key Link Verification

Plan 01 key links:

| From | To | Via | Pattern | Status | Detail |
|------|----|-----|---------|--------|--------|
| models.py:save_ride_wind_data | ride_wind_data table | INSERT with ON CONFLICT DO NOTHING | `ON CONFLICT.*DO NOTHING` | WIRED | models.py:2512 — exact pattern present |
| models.py:get_ride_wind_data | ride_wind_data table | SELECT WHERE ride_id ORDER BY stop_order | `SELECT.*FROM ride_wind_data WHERE ride_id` | WIRED | models.py:2479-2481 — exact pattern present |

Plan 02 key links:

| From | To | Via | Pattern | Status | Detail |
|------|----|-----|---------|--------|--------|
| services/weather.py:fetch_historical_wind | archive-api.open-meteo.com | requests.get with start_date/end_date | `OPEN_METEO_ARCHIVE_URL` | WIRED | weather.py:309 — requests.get(OPEN_METEO_ARCHIVE_URL, params=params) |
| services/weather.py:fetch_historical_wind | api.open-meteo.com/v1/forecast | requests.get with past_days param (fallback) | `past_days` | WIRED | weather.py:330 — 'past_days': max(days_ago+1,1) in params |
| services/weather.py:get_historical_stop_wind | services/weather.py:fetch_historical_wind | function call after coordinate interpolation | `fetch_historical_wind` | WIRED | weather.py:393 — weather_data, data_source = fetch_historical_wind(valid_coords, ride_date) |
| services/weather.py:get_historical_stop_wind | models.py:get_ride_wind_data | DB check before API call (STOR-02) | `get_ride_wind_data` | WIRED | weather.py:381 — stored = get_ride_wind_data(ride_id); early return if non-empty |
| services/weather.py:get_historical_stop_wind | models.py:save_ride_wind_data | persist after successful fetch | `save_ride_wind_data` | WIRED | weather.py:474 — save_ride_wind_data(ride_id, wind_rows) after wind_rows built |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| WIND-07 | 06-02 | System fetches historical wind data via Open-Meteo archive API with start_date/end_date parameters | SATISFIED | _fetch_archive_wind sends start_date=end_date=ride_date to OPEN_METEO_ARCHIVE_URL; 2 tests confirm (test_old_ride_uses_archive, test_lag_boundary_uses_archive) |
| WIND-08 | 06-02 | System falls back to forecast API past_days parameter when archive API returns no data for rides within 5 days | SATISFIED | _fetch_forecast_past_days_wind sends past_days param to OPEN_METEO_URL; ARCHIVE_LAG_DAYS=5; test_recent_ride_uses_past_days confirms; boundary test confirms 5-day rule |
| STOR-01 | 06-01 | System stores historical wind data in ride_wind_data table (all 12 columns) | SATISFIED | Migration 011 creates table with all 12 columns: ride_id, stop_order, stop_name, wind_speed_kmh, wind_direction_deg, headwind_kmh, crosswind_kmh, wind_type, temperature_c, conditions, data_source, fetched_at |
| STOR-02 | 06-01, 06-02 | System checks ride_wind_data table before fetching from archive API; only fetches if no existing data for that ride | SATISFIED | get_historical_stop_wind checks DB first (line 381-383); returns early if stored non-empty; test_db_hit_skips_api_call and test_db_miss_fetches_and_saves cover both branches |
| STOR-03 | 06-01 | System stores data_source as 'archive' or 'forecast_past_days' to track provenance | SATISFIED | data_source column with CHECK constraint in migration; save_ride_wind_data inserts row.get('data_source'); get_historical_stop_wind populates data_source from fetch_historical_wind return value |

All 5 requirement IDs from PLAN frontmatter accounted for. REQUIREMENTS.md traceability table lists WIND-07, WIND-08, STOR-01, STOR-02, STOR-03 as Phase 6, all marked Complete.

No orphaned requirements: no additional IDs mapped to Phase 6 in REQUIREMENTS.md beyond those declared in the plans.

### Anti-Patterns Found

Scanning `migrations/011_add_ride_wind_data.sql`, `models.py` (wind section), and `services/weather.py` (historical wind section):

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| None found in phase 06 files | — | — | — |

Notes:
- The word "placeholders" appears in models.py at lines 667, 687, 2323 but these are pre-existing SQL placeholder variable names (`,'.join(['%s'])`), not stub indicators. They are unrelated to Phase 06 work.
- No `print()` statements found in `services/weather.py`.
- No TODO/FIXME/HACK in any Phase 06 files.
- No empty return stubs in the new functions.

### Human Verification Required

None. All observable truths can be verified programmatically for this phase. The functions are pure computation + DB I/O with complete test coverage.

The one item requiring future human verification is that the migration `migrations/011_add_ride_wind_data.sql` is actually run against the Supabase database before Phase 07 route handlers are deployed. This is an operational step, not a code correctness issue.

### Test Suite Results

Full suite: **316 passed, 6 skipped, 0 failures**
Wind-specific tests: **124 passed** (test_models_wind.py + test_weather.py combined)
- test_models_wind.py: 16 tests covering get_ride_wind_data and save_ride_wind_data
- test_weather.py (new tests): 13 tests in TestFetchHistoricalWind (6) and TestGetHistoricalStopWind (7)

### Phase Goal Assessment

The phase goal states: "Historical wind for completed rides is fetched once from the Open-Meteo archive API, persisted to the ride_wind_data table, and never re-fetched on subsequent page loads."

All three components are delivered:

1. **Fetched once from archive API** — fetch_historical_wind routes to archive-api.open-meteo.com for rides 5+ days old; falls back to forecast past_days for rides 1-4 days old (WIND-08 ERA5 lag handling).

2. **Persisted to ride_wind_data table** — save_ride_wind_data inserts per-stop rows with ON CONFLICT DO NOTHING; explicit conn.commit() ensures durability. The migration creates the table with the correct schema.

3. **Never re-fetched on subsequent page loads** — get_historical_stop_wind checks DB first via get_ride_wind_data(ride_id); returns immediately if rows exist, skipping all network I/O. Two tests confirm the cache hit and miss branches work correctly.

---

_Verified: 2026-03-23T21:07:22Z_
_Verifier: Claude (gsd-verifier)_
