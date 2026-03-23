---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: planning
stopped_at: Completed 06-02-PLAN.md — archive API fetch, forecast past_days fallback, get_historical_stop_wind with STOR-02
last_updated: "2026-03-23T21:08:46.107Z"
last_activity: 2026-03-23 — Roadmap created; 7 phases, 31/31 requirements mapped
progress:
  total_phases: 7
  completed_phases: 6
  total_plans: 9
  completed_plans: 9
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-23)

**Core value:** Riders can see wind conditions — forecast and historical — integrated directly into ride plans for informed preparation decisions
**Current focus:** Phase 1 — Wind Math Foundation

## Current Position

Phase: 1 of 7 (Wind Math Foundation)
Plan: 0 of TBD in current phase
Status: Ready to plan
Last activity: 2026-03-23 — Roadmap created; 7 phases, 31/31 requirements mapped

Progress: [██████████] 100%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: —
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**
- Last 5 plans: —
- Trend: —

*Updated after each plan completion*
| Phase 01-wind-math-foundation P01 | 1 | 2 tasks | 2 files |
| Phase 02 P01 | 1 | 2 tasks | 2 files |
| Phase 03 P01 | 110 | 1 tasks | 2 files |
| Phase 03 P02 | 2 | 2 tasks | 2 files |
| Phase 04-heavy-wind-warning-banner P01 | 3 | 1 tasks | 2 files |
| Phase 04-heavy-wind-warning-banner P02 | 15 | 2 tasks | 2 files |
| Phase 05-forecast-wind-in-custom-ride-plan P01 | 4 | 2 tasks | 2 files |
| Phase 06-historical-wind-archive-api-and-db-persistence P01 | 8 | 1 tasks | 3 files |
| Phase 06-historical-wind-archive-api-and-db-persistence P02 | 2 | 2 tasks | 2 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Open-Meteo Archive for historical wind — free, unlimited, same API shape as forecast
- Store wind in DB — prevents repeated archive API calls; data_source column tracks archive vs. forecast_past_days
- Inline styles for wind cell colors — Tailwind JIT static purging makes dynamic classes impossible
- No new DB tables for forecast data — forecasts are ephemeral, cache in Flask-Caching (1-hour TTL)
- [Phase 01-wind-math-foundation]: classify_wind uses strict > so equal headwind/crosswind magnitudes go to crosswind
- [Phase 01-wind-math-foundation]: wind_cell_style falls back to crosswind blue for unknown wind types via dict.get default
- [Phase 02]: MILES_TO_METERS defined locally in weather.py (not imported from rwgps.py) to keep modules decoupled
- [Phase 02]: get_stop_coordinates placed in weather.py alongside sample_track_points() since both bridge RWGPS track data to the weather pipeline
- [Phase 03]: fetch_stop_wind uses arrival_time_min per stop for accurate forecast hour selection via get_hour_index()
- [Phase 03]: Cache key wind:{plan_slug}:{YYYYMMDD}{HH} distinct from weather: prefix to prevent collision
- [Phase 03-02]: stop_wind passed as None when weather_route_id absent — all wind markup gated on {% if stop_wind %} for graceful degradation
- [Phase 03-02]: current_app.logger.exception used in route handler (not app.logger) — consistent with Flask proxy pattern
- [Phase 04-heavy-wind-warning-banner]: headwind_kmh added to fetch_stop_wind() output is backward-compatible — existing callers only read keys they need
- [Phase 04-heavy-wind-warning-banner]: detect_heavy_wind uses strict > (not >=) for both thresholds, consistent with Phase 01 classify_wind decision
- [Phase 04-heavy-wind-warning-banner]: plan_slug_to_id moved to unconditional scope before if user_id block to prevent NameError for anonymous visitors
- [Phase 04-heavy-wind-warning-banner]: try/except wraps each event wind fetch independently so one API failure does not suppress the entire upcoming brevets page
- [Phase 04-heavy-wind-warning-banner]: Banner uses HTML entities for warning icon instead of emoji literals for cross-platform safety in Jinja2 templates
- [Phase 05-forecast-wind-in-custom-ride-plan]: custom_ride_plan_view uses str(plan.get('start_time') or '07:00')[:5] for start_time_str to safely handle time objects
- [Phase 05-forecast-wind-in-custom-ride-plan]: Tests patch services.custom_plan_service.get_merged_plan_stops (not routes.riders.*) because the route handler uses a local import inside the function body
- [Phase 06-historical-wind-archive-api-and-db-persistence]: get_ride_wind_data uses _execute() helper for reads; save_ride_wind_data uses raw cursor with explicit conn.commit() — autocommit=False requires it
- [Phase 06-historical-wind-archive-api-and-db-persistence]: ON CONFLICT (ride_id, stop_order) DO NOTHING — second save for same stop silently skipped, never errors (STOR-02 idempotency)
- [Phase 06-historical-wind-archive-api-and-db-persistence]: data_source CHECK constraint enforces only 'archive' or 'forecast_past_days' at DB layer (STOR-03)
- [Phase 06-historical-wind-archive-api-and-db-persistence]: fetch_historical_wind returns (data, source) tuple so callers always know provenance
- [Phase 06-historical-wind-archive-api-and-db-persistence]: get_historical_stop_wind checks DB (STOR-02) before any network call — read-through cache pattern for ride wind data

### Pending Todos

None yet.

### Blockers/Concerns

- Phase 6: Archive API 5-day ERA5 lag requires fallback logic — validate with real API calls during planning before writing production code
- Phase 7: UTC-to-local time conversion for Strava activity.start_date needs verification against actual API response format

## Session Continuity

Last session: 2026-03-23T21:05:37.742Z
Stopped at: Completed 06-02-PLAN.md — archive API fetch, forecast past_days fallback, get_historical_stop_wind with STOR-02
Resume file: None
