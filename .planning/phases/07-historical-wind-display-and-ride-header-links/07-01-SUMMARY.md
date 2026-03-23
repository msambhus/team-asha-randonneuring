---
phase: 07-historical-wind-display-and-ride-header-links
plan: "01"
subsystem: ui
tags: [jinja2, sql, postgresql, rider-profile, ride-plan]

# Dependency graph
requires:
  - phase: 06-historical-wind-archive-api-and-db-persistence
    provides: ride_plan table with slug column used for linking
provides:
  - get_rider_participation returns plan_slug via LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
  - rider_profile.html conditionally wraps ride names as clickable links for 2024-2025 and 2025-2026 seasons
affects: [rider-profile, brevet-history, ride-plan-detail]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - LEFT JOIN for optional relationship — ride_plan is optional; LEFT JOIN preserves all rides, NULL slug for unlinked rides
    - Jinja2 conditional link rendering — {% if season and slug %} pattern for safe conditional <a> tags

key-files:
  created:
    - tests/test_models.py
  modified:
    - models.py
    - templates/rider_profile.html

key-decisions:
  - "plan_slug added after club_code in SELECT list — preserves column order, non-breaking for existing callers"
  - "LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id — LEFT JOIN ensures rides without plans still appear"
  - "Season filter uses ['2024-2025', '2025-2026'] list — both seasons per LINK-01 requirement"

patterns-established:
  - "Conditional link pattern: {% if sd.season.name in [...] and p.plan_slug %} wraps ride name in <a>, else <strong>"

requirements-completed: [LINK-01, LINK-02]

# Metrics
duration: 15min
completed: 2026-03-23
---

# Phase 07 Plan 01: Ride Name Links in Brevet History Summary

**Rider brevet history now links ride names to ride plan detail pages for 2024-2025 and 2025-2026 seasons via LEFT JOIN plan_slug in get_rider_participation**

## Performance

- **Duration:** 15 min
- **Started:** 2026-03-23T21:08:46Z
- **Completed:** 2026-03-23T21:23:50Z
- **Tasks:** 2
- **Files modified:** 3 (models.py, templates/rider_profile.html, tests/test_models.py)

## Accomplishments
- Extended `get_rider_participation` SQL to return `plan_slug` via `LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id`
- Updated `rider_profile.html` brevet history table to conditionally render ride names as `<a>` links when `plan_slug` is present for 2024-2025 and 2025-2026 seasons
- Created `tests/test_models.py` with `TestRiderParticipationPlanSlug` verifying SQL content, plan_slug key presence, slug value for linked plans, and None for unlinked rides

## Task Commits

Each task was committed atomically:

1. **Task 1: RED — failing tests for plan_slug** - `1aeb38d` (test)
2. **Task 1: GREEN — add plan_slug to get_rider_participation** - `ac0bb7a` (feat)
3. **Task 2: Conditional ride name links in rider_profile.html** - `8684337` (feat)

_Note: TDD task has two commits (test RED → feat GREEN)_

## Files Created/Modified
- `tests/test_models.py` — New test file: TestRiderParticipationPlanSlug (5 tests verifying SQL and return values)
- `models.py` — get_rider_participation: added `rp.slug as plan_slug` to SELECT and `LEFT JOIN ride_plan rp`
- `templates/rider_profile.html` — Brevet history ride name now conditionally renders as `<a>` or `<strong>` based on season and plan_slug

## Decisions Made
- LEFT JOIN preserves all rides regardless of whether they have a linked plan — rides without a plan_id return plan_slug=NULL
- Season filter includes both `'2024-2025'` and `'2025-2026'` per LINK-01 requirement
- Link style matches existing primary color (`color:var(--primary)`) with no text-decoration to feel native, bold weight matching the original `<strong>` display

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Plan 07-01 complete: ride names in 2024-2025 and 2025-2026 seasons link to ride plan detail pages when plan_slug is available
- Ready for Plan 07-02 (historical wind display integration, if applicable)

---
*Phase: 07-historical-wind-display-and-ride-header-links*
*Completed: 2026-03-23*
