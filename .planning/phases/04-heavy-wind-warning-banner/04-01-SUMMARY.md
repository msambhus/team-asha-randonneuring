---
phase: 04-heavy-wind-warning-banner
plan: 01
subsystem: api
tags: [weather, wind, pure-function, tdd, open-meteo]

# Dependency graph
requires:
  - phase: 03-fetch-stop-wind
    provides: fetch_stop_wind() with per-stop wind data pipeline

provides:
  - detect_heavy_wind() pure function in services/weather.py
  - headwind_kmh field in each fetch_stop_wind() stop dict

affects:
  - phase 04 plan 02 (banner rendering consumes detect_heavy_wind)
  - any caller of fetch_stop_wind that wants headwind component per stop

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "TDD RED/GREEN: test class first, implementation second, no separate refactor needed for simple pure function"
    - "Strict > thresholds consistent with classify_wind convention from Phase 01"

key-files:
  created: []
  modified:
    - services/weather.py
    - tests/test_weather.py

key-decisions:
  - "headwind_kmh added to fetch_stop_wind() output is backward-compatible — existing callers only read keys they need"
  - "detect_heavy_wind uses strict > (not >=) for both thresholds, consistent with Phase 01 classify_wind decision"
  - "detect_heavy_wind placed immediately after fetch_stop_wind() as it consumes its output shape"

patterns-established:
  - "detect_heavy_wind accepts stop_wind list directly (same shape as fetch_stop_wind return value)"
  - "None entries in stop_wind list are silently filtered — caller does not need to pre-filter"

requirements-completed:
  - WARN-01
  - WARN-03
  - WARN-04

# Metrics
duration: 3min
completed: 2026-03-23
---

# Phase 04 Plan 01: Heavy Wind Detection — Core Logic Summary

**detect_heavy_wind() pure function with HEAVY_WIND_MAX_KMH=30 / HEAVY_WIND_AVG_HEADWIND_KMH=15 thresholds, plus headwind_kmh added to fetch_stop_wind() per-stop dict**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-23T17:24:19Z
- **Completed:** 2026-03-23T17:27:00Z
- **Tasks:** 1 (TDD: 2 commits — test + implementation)
- **Files modified:** 2

## Accomplishments

- Added `headwind_kmh` field to each stop dict returned by `fetch_stop_wind()` — previously computed internally but discarded
- Implemented `detect_heavy_wind(stop_wind)` pure function that evaluates per-stop wind data against two thresholds
- Full test coverage via `TestDetectHeavyWind` class (11 tests) covering all edge cases including None input, empty list, all-None stops, exact threshold boundary, mixed None entries, and value correctness

## Task Commits

Each task was committed atomically (TDD pattern — two commits):

1. **RED: TestDetectHeavyWind failing tests** - `89402d9` (test)
2. **GREEN: detect_heavy_wind() + headwind_kmh field** - `f460796` (feat)

_Note: No REFACTOR commit — function is simple enough that no cleanup was needed._

## Files Created/Modified

- `services/weather.py` - Added `headwind_kmh` to `fetch_stop_wind()` result dict; added `detect_heavy_wind()` function after `fetch_stop_wind()`
- `tests/test_weather.py` - Added `TestDetectHeavyWind` class (11 tests)

## Decisions Made

- `headwind_kmh` is added right after `wind_speed_kmh` in the stop dict for visual consistency; backward-compatible since existing callers only read keys they need
- `detect_heavy_wind` uses strict `>` (not `>=`) on both thresholds — consistent with Phase 01 decision to use strict `>` in `classify_wind`
- Function placed immediately after `fetch_stop_wind()` in the file since it consumes the same output shape

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `detect_heavy_wind()` is ready to be called from the route handler or chat tool in plan 04-02
- `fetch_stop_wind()` now returns `headwind_kmh` per stop — plan 04-02 can pass the result directly to `detect_heavy_wind()`
- All 90 existing weather tests pass; no regressions

---
*Phase: 04-heavy-wind-warning-banner*
*Completed: 2026-03-23*
