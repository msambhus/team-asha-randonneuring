---
phase: 03-forecast-wind-in-base-ride-plan
plan: "01"
subsystem: weather-service
tags: [tdd, wind, weather, forecast, caching]
dependency_graph:
  requires:
    - services/weather.py:get_stop_coordinates
    - services/weather.py:fetch_route_weather
    - services/weather.py:classify_wind
    - services/weather.py:wind_cell_style
    - services/weather.py:calculate_bearing
    - services/weather.py:headwind_component
    - services/weather.py:crosswind_component
    - services/weather.py:wind_label
    - services/weather.py:get_hour_index
    - services/weather.py:_safe_get
  provides:
    - services/weather.py:fetch_stop_wind
  affects:
    - routes/riders.py (will call fetch_stop_wind in plan 03-02)
    - templates/ride_plan_detail.html (will render stop_wind in plan 03-02)
tech_stack:
  added: []
  patterns:
    - TDD (RED -> GREEN) with pytest
    - Cache-first pattern: cache.get() / cache.set(timeout=3600)
    - Arrival-time-aware forecast hour indexing via get_hour_index()
    - None-safe result list (same length as stops)
key_files:
  created: []
  modified:
    - services/weather.py
    - tests/test_weather.py
decisions:
  - "fetch_stop_wind uses arrival_time_min from each stop (not a fixed hour index) to pick the correct forecast hour per stop via get_hour_index()"
  - "Cache key: wind:{plan_slug}:{YYYYMMDD}{HH} — distinct from weather:{route_slug}: prefix to prevent cache collision"
  - "Function returns None (not empty list) on empty track, all-None coords, or API error — caller differentiates no-data from empty data"
  - "Result list is always len(stops) with None entries for unresolvable stops — prevents Jinja2 IndexError in template"
metrics:
  duration_seconds: 110
  completed_date: "2026-03-23"
  tasks_completed: 1
  files_modified: 2
requirements: [WIND-06]
---

# Phase 03 Plan 01: fetch_stop_wind() — Per-Stop Wind Data Pipeline Summary

**One-liner:** TDD implementation of `fetch_stop_wind()` orchestrating get_stop_coordinates → fetch_route_weather → per-stop bearing/headwind/classify/style with arrival-time-aware forecast hour selection and 1-hour cache.

## What Was Built

`fetch_stop_wind(stops, track_points, plan_slug, start_time_str, cache=None)` added to `services/weather.py` after `get_cached_route_weather()`.

The function chains all Phase 1 and Phase 2 primitives into a single call that Plan 03-02 can invoke from `ride_plan_detail()`. Key behaviors:

- **Coordinate interpolation:** calls `get_stop_coordinates(stops, track_points)` — same length as stops
- **Cache-first fetch:** key `wind:{plan_slug}:{YYYYMMDD}{HH}`, 1-hour TTL
- **Arrival-time-aware:** uses `stop['arrival_time_min'] + start_time_str` to compute a per-stop `arrival_dt`, then `get_hour_index()` picks the correct forecast hour — all stops do not show the same wind value
- **Bearing direction:** middle stops use bearing to next stop; final stop uses bearing from previous stop (correct directional wind for each leg)
- **Error safety:** `try/except` wraps `fetch_route_weather()`; returns `None` on any failure so the caller can degrade gracefully
- **Length invariant:** result list always equals `len(stops)` — `None` entries for stops with no coordinate, preventing Jinja2 `IndexError`

## Test Coverage (TestFetchStopWind — 8 tests)

| Test | Behavior Verified |
|------|-------------------|
| `test_returns_wind_data_for_each_stop` | 3-stop result has all required keys: wind_speed_kmh, wind_type, style, label |
| `test_result_length_matches_stops` | Output len == input len even with beyond-track stops |
| `test_cache_hit` | Second call returns cached result; fetch_route_weather called only once |
| `test_empty_track_returns_none` | [] and None track_points both return None |
| `test_api_error_returns_none` | requests.RequestException propagated as None return |
| `test_none_coordinate_produces_none_entry` | None coord -> None entry; others remain valid |
| `test_bearing_uses_consecutive_stops` | calculate_bearing called with correct lat/lng pairs |
| `test_uses_arrival_time_for_hour_index` | Different arrival offsets produce different wind speeds |

## Commits

| Hash | Type | Description |
|------|------|-------------|
| 79e86aa | test | Add failing TestFetchStopWind class (8 tests, RED) |
| 2591df8 | feat | Implement fetch_stop_wind() in services/weather.py (GREEN) |

## Verification

```
python3 -m pytest tests/test_weather.py::TestFetchStopWind -x -q
# 8 passed

python3 -m pytest tests/ -x -q
# 271 passed, 6 skipped
```

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check

- [x] `services/weather.py` contains `def fetch_stop_wind`
- [x] `tests/test_weather.py` contains `class TestFetchStopWind`
- [x] 8 tests pass in TestFetchStopWind
- [x] Full suite 271 passed, 0 failed
- [x] RED commit: 79e86aa
- [x] GREEN commit: 2591df8
