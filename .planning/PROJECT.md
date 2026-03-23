# Wind Forecast Integration

## What This Is

Deep wind forecast and historical wind integration for the Team Asha randonneuring web app. Color-coded wind columns in base and custom ride plans show headwind/tailwind/crosswind at every stop. Heavy wind warnings appear on the upcoming brevets page. Actual wind data for completed 2026 rides appears in Strava analysis. Ride names for 2025/2026 seasons link to ride detail pages.

## Core Value

Riders can see wind conditions — forecast and historical — integrated directly into their ride plans, so they can make informed preparation decisions before brevets and analyze wind impact after rides.

## Requirements

### Validated

- ✓ Crosswind component calculation (sine projection) — v1.0
- ✓ Wind type classification (headwind/tailwind/crosswind) with color + intensity — v1.0
- ✓ Stop-to-coordinate interpolation via RWGPS track points — v1.0
- ✓ Historical weather fetch via Open-Meteo archive API with 5-day fallback — v1.0
- ✓ Wind data persistence in ride_wind_data table — v1.0
- ✓ Wind columns in base ride plan detail (color-coded cells) — v1.0
- ✓ Green=tailwind, Red=headwind, Blue=crosswind color scheme — v1.0
- ✓ Wind speed text, color shading intensity, and font size scale with wind speed — v1.0
- ✓ Wind columns in custom ride plan view — v1.0
- ✓ Wind warning banner on upcoming brevets page (28-day window) — v1.0
- ✓ Heavy wind detection (>30 km/h max or >15 km/h avg headwind) — v1.0
- ✓ Historical actual wind for completed 2026 rides in Strava analysis — v1.0
- ✓ Same color-coded column format for historical wind — v1.0
- ✓ 2025/2026 season ride headers link to ride detail pages — v1.0

### Active

(None — v1.0 shipped. Define in next milestone via `/gsd:new-milestone`)

### Out of Scope

- Real-time wind updates during rides — complexity, not core to planning
- Animated wind arrows on map — requires JS mapping library, incompatible with Flask/Jinja2 stack
- User-configurable wind thresholds — 30/15 km/h matches community norms
- Precipitation/temperature columns in plan table — existing weather button covers this
- Wind for rides without RWGPS routes — no track points = no per-stop interpolation
- Wind integration in chat agent — can add later as a separate milestone

## Context

Shipped v1.0 with 2,588 lines added across 12 files.
Tech stack: Flask + PostgreSQL + Jinja2 + Tailwind CSS + Open-Meteo API.
Key modules: `services/weather.py` (wind math + forecast + archive), `models.py` (ride_wind_data persistence), `routes/riders.py` (wind in ride plans + Strava analysis).
Test coverage: `tests/test_weather.py` (1,423 lines), `tests/test_models_wind.py` (326 lines).

## Constraints

- **API**: Open-Meteo free tier (10,000 requests/day) — batch lat/lng in single calls, cache aggressively
- **Stack**: Flask + Jinja2 + Tailwind CSS (no JS framework), inline styles for dynamic colors
- **Performance**: Wind fetch adds RWGPS API call per ride plan view — cached with Flask-Caching
- **Data**: Historical wind via Open-Meteo archive, 5-day ERA5 lag requires forecast fallback
- **DB**: Historical wind stored in ride_wind_data table to avoid repeated API calls

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Open-Meteo Archive for historical wind | Free, unlimited, same API shape as existing forecast integration | ✓ Good |
| Store wind data in DB | User requested persistence; avoids repeated archive API calls | ✓ Good |
| 45-degree crosswind threshold | \|headwind\| > \|crosswind\| → head/tailwind; else → crosswind. Matches cycling feel | ✓ Good |
| No new DB tables for forecast data | Forecasts are ephemeral, cache in Flask-Caching (1-hour TTL) | ✓ Good |
| Inline styles for wind cell colors | Dynamic color intensity requires computed values; Tailwind JIT can't purge dynamic classes | ✓ Good |
| ON CONFLICT DO NOTHING for idempotent persistence | Second save for same stop silently skipped, never errors | ✓ Good |
| data_source CHECK constraint at DB layer | Enforces only 'archive' or 'forecast_past_days' — provenance always tracked | ✓ Good |
| MILES_TO_METERS defined locally in weather.py | Keeps modules decoupled from rwgps.py | ✓ Good |
| stop_wind keyed by stop_name in Strava analysis | Comparison rows may include extra unplanned stops; name-keyed avoids index mismatch | ✓ Good |

---
*Last updated: 2026-03-23 after v1.0 milestone*
