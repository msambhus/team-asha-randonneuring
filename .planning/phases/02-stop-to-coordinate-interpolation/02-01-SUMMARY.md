---
phase: 02-stop-to-coordinate-interpolation
plan: "01"
subsystem: weather
tags: [interpolation, track-points, unit-conversion, tdd, pure-function]
dependency_graph:
  requires: []
  provides: [services.weather.MILES_TO_METERS, services.weather.get_stop_coordinates]
  affects: [Phase 3 per-stop wind forecast fetching]
tech_stack:
  added: []
  patterns: [linear-scan-interpolation, rwgps-track-point-field-names, tdd-red-green]
key_files:
  created: []
  modified:
    - services/weather.py
    - tests/test_weather.py
decisions:
  - "MILES_TO_METERS = 1609.344 defined locally in weather.py to keep modules decoupled (not imported from rwgps.py)"
  - "get_stop_coordinates placed in weather.py alongside sample_track_points() since both bridge RWGPS track data to the weather pipeline"
  - "Linear scan used over bisect — RWGPS tracks have 1k-5k points with at most ~30 stops per plan; O(n) scan is readable and sufficient"
metrics:
  duration: "1.3 minutes"
  completed: "2026-03-23"
  tasks_completed: 2
  files_modified: 2
---

# Phase 02 Plan 01: get_stop_coordinates TDD Implementation Summary

**One-liner:** MILES_TO_METERS constant and get_stop_coordinates() in weather.py using linear scan interpolation over RWGPS track points, with 9 tests covering unit conversion, boundary clamping, None filtering, and zero-length segments.

## What Was Built

`MILES_TO_METERS = 1609.344` constant and `get_stop_coordinates(stops, track_points)` function added to `services/weather.py`. The function takes a list of ride plan stops (with `distance_miles` field) and RWGPS track points (with `y`, `x`, `d` fields), and returns a parallel list of `{'lat': float, 'lng': float}` dicts for use by Phase 3 per-stop wind fetching.

`TestGetStopCoordinates` class added to `tests/test_weather.py` with 9 test methods.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| RED | Add failing TestGetStopCoordinates tests | e1440f9 | tests/test_weather.py (+157 lines) |
| GREEN | Implement MILES_TO_METERS + get_stop_coordinates | d78f298 | services/weather.py (+55 lines) |

## Test Results

- TestGetStopCoordinates: 9/9 passing
- Full suite: 263 passed, 6 skipped (up from 254 pre-existing — no regressions)

### Tests Added

| Test | Behavior Validated |
|------|--------------------|
| test_mid_route_stop | Stop at exact track point distance returns that point's coords |
| test_interpolated_between_points | Stop between two points returns linearly interpolated lat/lng |
| test_40_mile_stop_unit_conversion | 40-mile stop within 0.5 km of correct position at 64374m (not 40m) |
| test_beyond_track_end_clamped | Stop at 999 miles returns final track point — no error |
| test_start_stop_returns_first_point | Stop at 0.0 miles returns first track point coords |
| test_empty_track_returns_none | Empty track returns [None] * len(stops) |
| test_skips_none_coordinates | None y/x track points filtered; interpolation uses remaining valid points |
| test_zero_length_segment | Duplicate distance values don't cause ZeroDivisionError |
| test_multiple_stops_ordered | Three stops return three correct coords in input order |

## Decisions Made

- **MILES_TO_METERS local to weather.py:** The RWGPS service already has `METERS_TO_MILES = 1 / 1609.344`. Rather than importing a single constant cross-module, define `MILES_TO_METERS = 1609.344` locally in `weather.py`. Keeps modules decoupled.

- **Location: weather.py alongside sample_track_points():** The function bridges RWGPS track data to the weather pipeline — same responsibility as `sample_track_points()`. Co-location makes the pipeline clear: track -> coordinates -> weather fetch.

- **Linear scan over bisect:** `bisect` requires extra key function plumbing for dicts. RWGPS tracks are 1k-5k points; brevet plans have at most ~30 stops. O(n) scan per stop is well within performance bounds and is more readable.

## Deviations from Plan

None — plan executed exactly as written. Algorithm from 02-RESEARCH.md Pattern 1 implemented verbatim.

## Self-Check

- [x] services/weather.py modified with MILES_TO_METERS and get_stop_coordinates
- [x] tests/test_weather.py modified with TestGetStopCoordinates (9 tests)
- [x] Commit e1440f9 exists (TDD RED)
- [x] Commit d78f298 exists (TDD GREEN)
- [x] 263 tests pass, no regressions
