# Milestones

## v1.0 Wind Forecast Integration (Shipped: 2026-03-23)

**Phases completed:** 7 phases, 11 plans
**Files changed:** 12 (2,588 lines added)
**Timeline:** 2026-03-23 (single day, 7 ralph iterations)
**Git range:** feat(01-01)..docs(phase-07)

**Key accomplishments:**
1. Wind math foundation — crosswind projection, classification, color/intensity styling, threshold constants
2. Stop-to-coordinate interpolation — RWGPS track point distance matching with miles-to-meters conversion
3. Forecast wind columns in base and custom ride plans — color-coded headwind/tailwind/crosswind at every stop
4. Heavy wind warning banner on upcoming brevets — alerts when max wind >30 km/h or avg headwind >15 km/h
5. Historical wind persistence — Open-Meteo archive API with 5-day ERA5 fallback, ride_wind_data table
6. Actual Wind display in Strava analysis and clickable ride name links for 2025/2026 seasons

**Key decisions:**
- 45-degree threshold for wind classification (|headwind| > |crosswind|)
- Inline styles for dynamic wind cell colors (Tailwind JIT can't purge dynamic classes)
- No new DB tables for forecast data — cache-only via Flask-Caching (1-hour TTL)
- ON CONFLICT DO NOTHING for idempotent wind data persistence
- data_source CHECK constraint ('archive' | 'forecast_past_days') at DB layer

**Archive:** [v1.0-ROADMAP.md](milestones/v1.0-ROADMAP.md) | [v1.0-REQUIREMENTS.md](milestones/v1.0-REQUIREMENTS.md)

---

