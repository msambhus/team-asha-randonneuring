---
phase: 06-historical-wind-archive-api-and-db-persistence
plan: 01
subsystem: database
tags: [postgresql, psycopg2, migration, wind, persistence]

# Dependency graph
requires: []
provides:
  - ride_wind_data table with UNIQUE(ride_id, stop_order) constraint and data_source CHECK
  - get_ride_wind_data(ride_id) — returns stored stop wind rows ordered by stop_order
  - save_ride_wind_data(ride_id, wind_rows) — idempotent INSERT with ON CONFLICT DO NOTHING
affects:
  - 06-02 (archive API integration — calls save_ride_wind_data and get_ride_wind_data)
  - any future route handler serving historical wind data

# Tech tracking
tech-stack:
  added: []
  patterns:
    - ON CONFLICT (ride_id, stop_order) DO NOTHING for idempotent wind row persistence
    - Explicit conn.commit() after multi-row INSERT loop (autocommit=False pattern)
    - _execute() helper for reads; raw cursor for writes requiring commit control

key-files:
  created:
    - migrations/011_add_ride_wind_data.sql
    - tests/test_models_wind.py
  modified:
    - models.py

key-decisions:
  - "get_ride_wind_data uses _execute() helper (consistent with read-only models); save_ride_wind_data uses raw cursor for explicit commit control"
  - "ON CONFLICT (ride_id, stop_order) DO NOTHING — second save for same stop is silently skipped, never errors"
  - "data_source CHECK constraint enforces only 'archive' or 'forecast_past_days' at the DB layer"

patterns-established:
  - "Wind persistence pattern: check get_ride_wind_data first (STOR-02), call save_ride_wind_data only on cache miss"

requirements-completed: [STOR-01, STOR-02, STOR-03]

# Metrics
duration: 8min
completed: 2026-03-23
---

# Phase 06 Plan 01: Historical Wind DB Persistence Summary

**ride_wind_data PostgreSQL table with idempotent get/save model functions backed by ON CONFLICT DO NOTHING and data_source CHECK constraint**

## Performance

- **Duration:** 8 min
- **Started:** 2026-03-23T20:58:02Z
- **Completed:** 2026-03-23T21:06:00Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 3

## Accomplishments

- Migration 011 creates ride_wind_data table with all STOR-01 columns, UNIQUE constraint, data_source CHECK, and index
- get_ride_wind_data(ride_id) returns empty list for unknown rides, list of dicts ordered by stop_order for known rides
- save_ride_wind_data(ride_id, wind_rows) inserts per-stop rows idempotently — calling twice for the same ride is safe
- 16 unit tests covering empty result, multi-row ordering, ON CONFLICT SQL, commit call, data_source='archive' and 'forecast_past_days'
- Full test suite: 303 passed, 6 skipped

## Task Commits

Each task was committed atomically:

1. **RED: Failing tests for get/save wind data** - `9c3000a` (test)
2. **GREEN: Migration SQL + model functions** - `92c24a5` (feat)

**Plan metadata:** (docs commit to follow)

_Note: TDD task has two commits — test (RED) then feat (GREEN)_

## Files Created/Modified

- `migrations/011_add_ride_wind_data.sql` - Idempotent DDL: ride_wind_data table, UNIQUE(ride_id, stop_order), data_source CHECK, index
- `models.py` - Added get_ride_wind_data() and save_ride_wind_data() at bottom of file
- `tests/test_models_wind.py` - 16 unit tests covering both functions with mock get_db() patches

## Decisions Made

- `get_ride_wind_data` uses the existing `_execute()` helper (consistent read-only pattern); `save_ride_wind_data` uses a raw cursor to call `conn.commit()` explicitly — necessary because `get_db()` sets `autocommit = False`
- ON CONFLICT targets `(ride_id, stop_order)` — the UNIQUE constraint — so a re-fetch for a completed ride silently skips existing rows
- `data_source` is enforced at both the DB layer (CHECK constraint) and tested at the model layer

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

Run migration before deploying:
```sql
-- migrations/011_add_ride_wind_data.sql
```

## Next Phase Readiness

- DB schema and model functions are complete; Phase 06-02 can now call `get_ride_wind_data` (DB check) and `save_ride_wind_data` (persist archive results)
- STOR-01, STOR-02, STOR-03 requirements are satisfied

---
*Phase: 06-historical-wind-archive-api-and-db-persistence*
*Completed: 2026-03-23*
