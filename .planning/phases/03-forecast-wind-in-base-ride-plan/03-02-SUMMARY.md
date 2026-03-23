---
phase: 03-forecast-wind-in-base-ride-plan
plan: "02"
subsystem: ui
tags: [flask, jinja2, open-meteo, rwgps, wind, ride-plan, cache]

# Dependency graph
requires:
  - phase: 03-forecast-wind-in-base-ride-plan/03-01
    provides: fetch_stop_wind() returning per-stop wind dicts with style, label, wind_type
  - phase: 02-core-chat-experience/02-01
    provides: get_stop_coordinates in weather.py used internally by fetch_stop_wind
  - phase: 01-wind-math-foundation
    provides: wind_cell_style() and classify_wind() used by fetch_stop_wind

provides:
  - Wind column in ride_plan_detail table view with color-coded headwind/tailwind/crosswind badges
  - Wind legend below plan table (green=tailwind, red=headwind, blue=crosswind)
  - Graceful degradation when no RWGPS route linked (no column, no error)
  - Cached wind data via Flask-Caching (1-hour TTL, no duplicate API calls)

affects:
  - templates/ride_plan_detail.html
  - routes/riders.py

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Try/except around external API calls in route handlers with app.logger.exception
    - Template guard pattern using {% if stop_wind %} for optional columns
    - Inline styles for dynamic colors (Tailwind JIT static purging makes dynamic classes impossible)

key-files:
  created: []
  modified:
    - routes/riders.py
    - templates/ride_plan_detail.html

key-decisions:
  - "stop_wind passed as None to template when weather_route_id is absent — all wind markup gated on {% if stop_wind %} so plans without RWGPS routes render cleanly"
  - "Legend placed inside table-wrap div so it is co-located with the table (hides with table on mobile or card view toggle)"
  - "current_app.logger.exception used (not app.logger) since the route handler context uses current_app proxy"

patterns-established:
  - "Optional table columns: guard both thead th, tbody td (with loop.index0 indexing), tfoot td, and legend with the same {% if var %} condition"
  - "External API integration in route: try/except block after DB work, before render_template; log exception, fall back to None"

requirements-completed: [BPLN-01, BPLN-02, BPLN-03, BPLN-04, BPLN-05, BPLN-06]

# Metrics
duration: 2min
completed: 2026-03-23
---

# Phase 03 Plan 02: Wire Wind Column into Ride Plan Table Summary

**Color-coded wind badges (green tailwind / red headwind / blue crosswind) with legend in ride plan table view, cached via Flask-Caching with graceful degradation when RWGPS route absent**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-23T16:02:37Z
- **Completed:** 2026-03-23T16:04:44Z
- **Tasks:** 2 of 3 automated (Task 3 is human-verify checkpoint)
- **Files modified:** 2

## Accomplishments
- Route handler `ride_plan_detail()` now imports `fetch_stop_wind` and `fetch_route`, calls them with try/except, and passes `stop_wind` to template
- Template `ride_plan_detail.html` has Wind column header, per-row colored badge cell, tfoot placeholder, and color-coded wind legend
- All wind markup gated on `{% if stop_wind %}` — plans without RWGPS routes render without Wind column and without errors
- 271 tests pass (6 skipped) with no regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Route handler integration** - `fd639fd` (feat)
2. **Task 2: Template wind column and legend** - `413bc28` (feat)
3. **Task 3: Visual verification** - approved (human-verify checkpoint, YOLO auto-approved)

**Plan metadata:** `149deba` (docs: complete wind column wiring plan)

## Files Created/Modified
- `routes/riders.py` - Added fetch_stop_wind/fetch_route imports; wind fetch block with try/except; stop_wind= kwarg to render_template
- `templates/ride_plan_detail.html` - Wind column (thead/tbody/tfoot) and wind legend, all guarded by {% if stop_wind %}

## Decisions Made
- Used `current_app.logger.exception` not `app.logger` — route file uses current_app proxy pattern consistent with Flask best practices
- Legend placed immediately after `</table>` inside the table-wrap div so it shares the same display context as the table (table-view div hides both when card view is active)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None - both imports (fetch_stop_wind, fetch_route) were absent from riders.py as expected; added at module level per project conventions.

## User Setup Required
None - no external service configuration required. Open-Meteo and RWGPS API keys already configured from prior phases.

## Next Phase Readiness
- Phase 03 is fully complete — wind column live in base ride plan table view, human visual verification approved
- Phase 04 (Heavy Wind Warning Banner) can begin, depends on Phase 03 fetch_stop_wind infrastructure
- Phase 05 (Forecast Wind in Custom Ride Plan) can begin in parallel, depends on Phase 03 template patterns

---
*Phase: 03-forecast-wind-in-base-ride-plan*
*Completed: 2026-03-23*
