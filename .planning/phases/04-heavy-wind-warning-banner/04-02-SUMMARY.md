---
phase: 04-heavy-wind-warning-banner
plan: 02
subsystem: ui
tags: [flask, jinja2, weather, wind, banner, upcoming-brevets]

# Dependency graph
requires:
  - phase: 04-heavy-wind-warning-banner
    provides: detect_heavy_wind() function and headwind_kmh in fetch_stop_wind() output
  - phase: 03-wind-forecast-per-stop
    provides: fetch_stop_wind() returning per-stop wind data
provides:
  - Wind warning loop in upcoming_brevets() route that checks events within 28 days
  - Conditional yellow/amber banner in upcoming_brevets.html showing heavy wind warnings
  - Resilient per-event wind fetch with try/except so one failure doesn't break the page
affects:
  - future UI phases referencing the upcoming brevets page
  - any phase adding new banner-style alerts to the page

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Wind warning loop pattern: iterate events, guard-skip with continue, wrap in try/except
    - Inline styles for dynamic banners to avoid Tailwind JIT purging

key-files:
  created: []
  modified:
    - routes/riders.py
    - templates/upcoming_brevets.html

key-decisions:
  - "plan_slug_to_id built unconditionally before if user_id block — prevents NameError for logged-out visitors and avoids duplicate computation"
  - "try/except wraps each event's wind fetch independently so one API failure doesn't suppress the entire page"
  - "Banner uses HTML entities for warning icon (&#9888;&#65039;) instead of emoji literals for cross-platform safety"

patterns-established:
  - "Wind warning banner: {% if wind_warnings %} conditional, inline styles, ride_name/ride_date/description per warning"
  - "Route-level wind check: per-event guard chain (event_date, plan_slug, plan_id, rwgps_url, route_id) before expensive API call"

requirements-completed: [WARN-01, WARN-02, WARN-04]

# Metrics
duration: 15min
completed: 2026-03-23
---

# Phase 04 Plan 02: Heavy Wind Warning Banner Summary

**Yellow/amber wind warning banner wired into upcoming_brevets route and template — shows affected ride name, date, and wind description for any brevet within 28 days with heavy forecast winds**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-03-23T16:46:38Z
- **Completed:** 2026-03-23
- **Tasks:** 2 auto + 1 auto-approved checkpoint
- **Files modified:** 2

## Accomplishments
- Added wind warning loop to `upcoming_brevets()` that evaluates brevets within 28 days with linked ride plans
- Moved `plan_slug_to_id` to unconditional scope (was scoped inside `if user_id:` — would cause NameError for logged-out visitors)
- Added conditional yellow/amber banner to `upcoming_brevets.html` that renders ride name, date, and wind description per affected brevet
- Each event's wind fetch wrapped in try/except so one Open-Meteo or RWGPS failure never breaks the entire page

## Task Commits

Each task was committed atomically:

1. **Task 1: Add wind warning loop to upcoming_brevets route** - `a710eae` (feat)
2. **Task 2: Add conditional wind warning banner to upcoming_brevets.html template** - `cb1f00e` (feat)
3. **Task 3: Verify wind warning banner** - auto-approved (checkpoint, no code change)

## Files Created/Modified
- `routes/riders.py` - Added detect_heavy_wind import, unconditional plan_slug_to_id, 40-line wind warning loop, wind_warnings in render_template
- `templates/upcoming_brevets.html` - Added 16-line {% if wind_warnings %} banner block after hero section

## Decisions Made
- `plan_slug_to_id` moved to unconditional scope — the existing placement inside `if user_id:` would NameError for anonymous visitors using the wind warning loop
- Banner uses HTML entities for warning icon instead of emoji literals for cross-platform safety in Jinja2 templates
- Inline styles only — consistent with Phase 03 decision that Tailwind JIT cannot purge dynamic classes

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Wind warning banner is live on the upcoming brevets page
- WARN-01, WARN-02, WARN-04 requirements completed
- Phase 04 plans complete — ready for Phase 05 or any dependent phase

---
*Phase: 04-heavy-wind-warning-banner*
*Completed: 2026-03-23*
