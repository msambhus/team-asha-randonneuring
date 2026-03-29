---
phase: 07-historical-wind-display-and-ride-header-links
plan: "02"
subsystem: weather-display
tags: [historical-wind, strava-analysis, template, tdd]
dependency_graph:
  requires:
    - "06-02: get_historical_stop_wind service function and DB persistence"
    - "03-02: ride_plan_detail wind cell display pattern"
  provides:
    - "Actual Wind column in strava_ride_analysis.html comparison table"
    - "stop_wind dict (keyed by stop_name) from ride_strava_analysis route"
  affects:
    - "routes/riders.py ride_strava_analysis function"
    - "templates/strava_ride_analysis.html comparison table"
tech_stack:
  added: []
  patterns:
    - "stop_wind dict keyed by stop_name (vs loop.index0 in ride_plan_detail)"
    - "Local import of get_historical_stop_wind + wind_cell_style inside function body"
    - "try/except around wind fetch with current_app.logger.exception"
key_files:
  created: []
  modified:
    - routes/riders.py
    - templates/strava_ride_analysis.html
    - tests/test_weather.py
decisions:
  - "Local import of get_historical_stop_wind inside ride_strava_analysis (consistent with existing function-level import pattern in that function)"
  - "stop_wind dict keyed by stop_name not loop index — comparison.rows may include extra/unplanned stops not in plan_stops"
  - "Patch at models.get_ride_plan_stops (not routes.riders.*) — function-level import inside ride_strava_analysis creates local binding that shadows module-level"
metrics:
  duration: "~20 minutes"
  completed: "2026-03-23"
  tasks_completed: 2
  files_modified: 3
---

# Phase 07 Plan 02: Historical Wind Display in Strava Analysis Summary

Wire `get_historical_stop_wind` into the Strava analysis page and render an "Actual Wind" column using the same green/red/blue visual language as forecast wind in ride plans.

## What Was Built

**routes/riders.py:** After the comparison is built in `ride_strava_analysis`, fetch historical wind data when `has_plan`, `plan_stops`, and `ride.date` are all truthy. Look up the RWGPS route ID from the linked plan's `rwgps_url_team` or `rwgps_url`, fetch track points, then call `get_historical_stop_wind`. Augment each returned row with a `style` dict from `wind_cell_style` and build a dict keyed by `stop_name`. Pass `stop_wind` to all three `render_template` calls (no-match and error paths get `stop_wind=None`).

**templates/strava_ride_analysis.html:** Added conditional `Actual Wind` column header in `<thead>`, per-row wind cell in the `{% for row in comparison.rows %}` loop using `stop_wind.get(row.location)` dict lookup (dash fallback for extra stops), and a wind legend block below the table.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Wire get_historical_stop_wind into route (TDD) | df8c318, c99f504 | tests/test_weather.py, routes/riders.py |
| 2 | Add Actual Wind column to template | 4d26581 | templates/strava_ride_analysis.html |

## Verification

- `python3 -m pytest tests/ -x -q` — 325 passed, 6 skipped
- Template uses "Actual Wind" header text (HIST-04)
- Wind cells use `wind_cell_style` output for inline styles (HIST-03)
- Route handler gates wind fetch behind `has_plan + plan_stops + track_points` (HIST-01)
- Rides without plans render without error (HIST-02 graceful degradation)

## Deviations from Plan

### Auto-fixed Issues

None — plan executed exactly as written.

### Key Implementation Note

The plan suggested importing `get_historical_stop_wind` with `from services.weather import ...` inside the function body. During test development, discovered that `get_ride_plan_stops` is called via a local import inside `ride_strava_analysis` (line 778: `from models import ... get_ride_plan_stops ...`), which means tests must patch `models.get_ride_plan_stops` rather than `routes.riders.get_ride_plan_stops`. This is consistent with how the function-level import pattern works in Python — local bindings shadow module-level ones.

## Self-Check: PASSED

- routes/riders.py: FOUND
- templates/strava_ride_analysis.html: FOUND
- tests/test_weather.py: FOUND
- Commit df8c318 (RED tests): FOUND
- Commit c99f504 (GREEN route handler): FOUND
- Commit 4d26581 (template): FOUND
