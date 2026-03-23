---
phase: 05-forecast-wind-in-custom-ride-plan
plan: "01"
subsystem: ui

tags: [flask, jinja2, weather, wind, custom-plan, rwgps]

# Dependency graph
requires:
  - phase: 03-02
    provides: fetch_stop_wind pipeline, stop_wind template variable pattern
  - phase: 04-heavy-wind-warning-banner
    provides: detect_heavy_wind, headwind_kmh in fetch_stop_wind output

provides:
  - custom_ride_plan_view wires fetch_stop_wind and passes stop_wind to render_template
  - TestCustomPlanWind test class covering CPLN-01, CPLN-02, WIND-09

affects:
  - templates/ride_plan_detail.html (already has {% if stop_wind %} wind column — now active for custom views)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "fetch_stop_wind called after _attach_break_metadata so processed stops (float distance_miles, arrival_time_min) reach the wind pipeline"
    - "Wind block in custom_ride_plan_view mirrors base plan view exactly — identical try/except with current_app.logger.exception"

key-files:
  created: []
  modified:
    - routes/riders.py
    - tests/test_weather.py

key-decisions:
  - "custom_ride_plan_view uses str(plan.get('start_time') or '07:00')[:5] for start_time_str to safely handle time objects"
  - "Tests patch services.custom_plan_service.get_merged_plan_stops (not routes.riders.*) because the function uses a local import inside the handler"

patterns-established:
  - "TDD RED/GREEN confirmed: route test fails before wiring, passes after — validates the test correctly detects the gap"

requirements-completed: [WIND-09, CPLN-01, CPLN-02]

# Metrics
duration: 4min
completed: 2026-03-23
---

# Phase 05 Plan 01: Custom Plan Wind Wiring Summary

**fetch_stop_wind wired into custom_ride_plan_view with stop_wind passed to template, enabling the existing wind column in ride_plan_detail.html for custom plan views**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-23T20:30:59Z
- **Completed:** 2026-03-23T20:34:49Z
- **Tasks:** 2 (TDD RED + GREEN)
- **Files modified:** 2

## Accomplishments

- Added `TestCustomPlanWind` class with 5 tests covering CPLN-01, CPLN-02, WIND-09
- Inserted 15-line wind block in `custom_ride_plan_view` after `_attach_break_metadata`, identical to base plan view pattern
- Added `stop_wind=stop_wind` kwarg to `render_template` call — wind column now active for all custom plan views
- Full 287-test suite passes with no regressions

## Task Commits

1. **Task 1: Add TestCustomPlanWind tests (RED)** - `e412bb0` (test)
2. **Task 2: Wire fetch_stop_wind into custom_ride_plan_view (GREEN)** - `5d7b45e` (feat)

## Files Created/Modified

- `routes/riders.py` - Added wind block (15 lines) and `stop_wind=stop_wind` kwarg in `custom_ride_plan_view`
- `tests/test_weather.py` - Added `TestCustomPlanWind` class (5 tests, 192 lines)

## Decisions Made

- `str(plan.get('start_time') or '07:00')[:5]` used for `start_time_str` — handles both string and time objects safely; custom plan always sets `plan['start_time']` at line 1544 so this will use the correct value
- Tests patch `services.custom_plan_service.get_merged_plan_stops` because the route handler uses a local import (`from services.custom_plan_service import get_merged_plan_stops`) inside the function body, not a module-level import under `routes.riders`

## Deviations from Plan

None - plan executed exactly as written. The 10-line wiring gap was confirmed by RED tests and closed in Task 2.

## Issues Encountered

Minor: Initial test setup used over-simplified stop dicts that lacked required fields (e.g., `location`, `stop_type`) needed by the route handler's processing loop. Fixed by adding a `_make_raw_stop` helper that provides all required fields. No code changes to production files required.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Wind column now active for custom plan views — riders with custom plans will see the same color-coded headwind/tailwind/crosswind data as base plan viewers
- Phase 05-02 (if any) can build on the established pattern
