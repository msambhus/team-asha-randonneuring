# Wind Forecast Integration

## What This Is

Deep wind forecast and historical wind integration for the Team Asha randonneuring web app. Adds wind condition columns to ride plans (base and custom), heavy wind warnings on upcoming brevets, actual wind data for completed 2026 rides in Strava analysis, and clickable ride headers for 2025/2026 seasons. Builds on the existing Open-Meteo weather service and RWGPS route data.

## Core Value

Riders can see wind conditions — forecast and historical — integrated directly into their ride plans, so they can make informed preparation decisions before brevets and analyze wind impact after rides.

## Requirements

### Validated

- ✓ Open-Meteo forecast API integration — existing (`services/weather.py`)
- ✓ Headwind/tailwind calculation with bearing math — existing
- ✓ RWGPS route track point fetching — existing (`services/rwgps.py`)
- ✓ Ride plan stop display (base + custom) — existing
- ✓ Strava activity sync and ride matching — existing
- ✓ Upcoming brevets page with weather links — existing

### Active

- [ ] Crosswind component calculation (sine projection)
- [ ] Wind type classification (headwind/tailwind/crosswind) with color + intensity
- [ ] Stop-to-coordinate interpolation via RWGPS track points
- [ ] Historical weather fetch via Open-Meteo archive API
- [ ] Wind data persistence in database (`ride_wind_data` table)
- [ ] Wind columns in base ride plan detail (color-coded cells)
- [ ] Green = tailwind, Red = headwind, Blue = crosswind color scheme
- [ ] Wind speed text, color shading intensity, and font size scale with wind speed
- [ ] Wind columns in custom ride plan view
- [ ] Wind warning banner on upcoming brevets page (3-4 week window)
- [ ] Heavy wind detection (>30 km/h max or >15 km/h avg headwind)
- [ ] Historical actual wind for completed 2026 rides in Strava analysis
- [ ] Same color-coded column format for historical wind
- [ ] 2025/2026 season ride headers link to ride detail pages

### Out of Scope

- Real-time wind updates during rides — complexity, not core to planning
- Wind integration in chat agent — can add later as a phase
- Precipitation/temperature columns — existing weather button already covers this
- Wind data for rides older than 2025 season — limited value, API data availability

## Context

- **Existing weather service** (`services/weather.py`, 269 lines): has `headwind_component()`, `calculate_bearing()`, `sample_track_points()`, `fetch_route_weather()`, `format_weather_response()`, and caching. Missing: crosswind calculation, stop-level interpolation, historical fetch.
- **Open-Meteo Archive API**: `https://archive-api.open-meteo.com/v1/archive` — same parameters as forecast API but with `start_date`/`end_date`. Free, unlimited.
- **Ride plan stops** don't store lat/lng — need to interpolate from RWGPS track points using cumulative distance.
- **RWGPS track point format**: `y`=lat, `x`=lng, `d`=distance_m.
- **Custom plans** merge base stops with rider overrides (hidden stops, added stops, time adjustments).

## Constraints

- **API**: Open-Meteo free tier (10,000 requests/day) — batch lat/lng in single calls, cache aggressively
- **Stack**: Flask + Jinja2 + Tailwind CSS (no JS framework), inline styles for dynamic colors
- **Performance**: Wind fetch adds RWGPS API call per ride plan view — must cache track points
- **Data**: Historical wind only reliable for recent years via Open-Meteo archive
- **DB**: Store historical wind in `ride_wind_data` table to avoid repeated API calls

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Open-Meteo Archive for historical wind | Free, unlimited, same API shape as existing forecast integration | — Pending |
| Store wind data in DB | User requested persistence; avoids repeated archive API calls | — Pending |
| 45-degree crosswind threshold | \|headwind\| > \|crosswind\| → head/tailwind; else → crosswind. Matches cycling feel | — Pending |
| No new DB tables for forecast data | Forecasts are ephemeral, cache in Flask-Caching (1-hour TTL) | — Pending |
| Inline styles for wind cell colors | Dynamic color intensity requires computed values, not static CSS classes | — Pending |

---
*Last updated: 2026-03-23 after initialization*
