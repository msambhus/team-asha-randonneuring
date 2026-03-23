# Project Research Summary

**Project:** Wind Integration — Team Asha Randonneuring
**Domain:** Wind forecast + historical weather visualization for a Flask-based cycling randonneuring app
**Researched:** 2026-03-23
**Confidence:** HIGH

## Executive Summary

This milestone adds per-stop wind forecasting and historical wind analysis to an existing Flask randonneuring app. The core approach is conservative and well-bounded: no new runtime dependencies are needed, the Open-Meteo API (forecast and archive) already fits the existing `requests`-based fetch pattern, and wind data persistence requires only one new database table. The primary value delivered — color-coded wind columns in the ride plan control sheet — is a genuine gap in the market since no competing tool (myWindsock, Headwind, Epic Ride Weather) presents wind in a brevet control sheet format.

The recommended implementation follows the architecture already present in the codebase: all wind computation extends `services/weather.py`, stop-to-coordinate interpolation extends `services/rwgps.py`, and routes remain thin orchestrators that assemble data and pass it to Jinja2 templates. Forecast wind is cached in Flask-Caching (1-hour TTL), historical wind is persisted to a `ride_wind_data` PostgreSQL table to avoid re-fetching the archive API on every page load. Dynamic wind intensity colors require Python-computed inline CSS styles — Tailwind's static class purging makes dynamic class names impossible.

The most significant risks are two API-level pitfalls specific to Open-Meteo: the archive API has a 5-day ERA5 data lag (rides completed in the past 1-5 days require a fallback to the forecast API's `past_days` parameter), and the API returns a dict for single-location requests but a list for multi-location batch requests (the existing codebase already normalizes this for forecast calls, but the archive fetch must apply the same normalization). A third critical pitfall — the meteorological "wind from" convention must be inverted by 180 degrees before any vector projection — is already handled in `headwind_component()` but must be explicitly replicated in the new crosswind calculation.

## Key Findings

### Recommended Stack

The existing stack is sufficient for this entire milestone. Flask 3.0.0, Jinja2 3.1.2, psycopg2-binary 2.9.9, requests 2.31.0, Flask-Caching 2.1.0, and Vercel serverless hosting are all already present and already handle every need. Zero new production dependencies are required. The only new infrastructure is a single PostgreSQL table (`ride_wind_data`) using the existing raw SQL migration pattern (`migrations/apply_migration_*.py`).

**Core technologies:**
- `requests` 2.31.0: Open-Meteo API calls — same pattern as `fetch_route_weather()`, just different URL and date params
- `psycopg2-binary` 2.9.9: Historical wind persistence via JSONB column in new `ride_wind_data` table
- `Flask-Caching` 2.1.0 SimpleCache: 1-hour TTL for forecast wind; already used in `weather.py`
- `Jinja2` 3.1.2: Wind columns with inline `style=` attributes for dynamic color intensity
- Python stdlib `math`: `math.sin()` / `math.cos()` cover all wind vector math — no numpy needed

**Explicitly rejected additions:** `openmeteo-requests` SDK (wraps what `requests` already does), Celery/RQ (incompatible with Vercel serverless), numpy (dev dependency only; inflates bundle), Chart.js/D3 (PROJECT.md specifies table cells, not charts).

### Expected Features

Features are well-defined from PROJECT.md and validated against competing tools (myWindsock, Headwind, Epic Ride Weather). The feature dependency tree is clear and drives the build order.

**Must have (table stakes):**
- Stop-to-coordinate interpolation via RWGPS track points — prerequisite for all per-stop wind; hardest table-stakes item
- Wind type classification (headwind / tailwind / crosswind) with 45-degree threshold — prerequisite for all visual display
- Wind columns in base ride plan (color-coded cells with speed text and intensity scaling) — primary planning surface; the core differentiator
- Heavy wind warning banner on upcoming brevets page (>30 km/h max or >15 km/h avg headwind) — quick-scan safety signal
- Historical wind for completed 2026 rides in Strava analysis — closes the "before and after" loop for riders

**Should have (competitive):**
- Wind columns in custom ride plan — extends base plan work; add after base plan is validated
- Wind persistence in DB (`ride_wind_data` table) — makes repeat historical views instant; prevents archive API hammering
- Clickable 2025/2026 season ride headers — navigation enhancement; only useful once detail pages have wind data

**Defer (v2+):**
- Wind in chat agent — requires agent to understand route + timing context; too much scope; defer until data model is stable
- Wind data for 2025 season (historical backfill) — validate 2026 coverage first; backfill only if warranted

### Architecture Approach

All wind logic extends existing services without creating new service files. `services/weather.py` gains crosswind math, historical fetch, and the `get_wind_for_stops()` orchestrator. `services/rwgps.py` gains `get_stop_coordinates()` interpolation. `models.py` gains `ride_wind_data` read/write queries. Route handlers (`riders.py`, `main.py`) remain thin orchestrators — no wind math lives in routes. Forecast wind and historical wind use strictly separate storage layers (Flask-Caching vs. PostgreSQL) to prevent stale-forecast-in-DB bugs.

**Major components:**
1. `services/weather.py` — all wind math (classify, crosswind, headwind), both Open-Meteo API calls (forecast + archive), wind intensity helpers
2. `services/rwgps.py` — RWGPS route fetch + stop-to-coordinate interpolation from track points
3. `models.py` + `ride_wind_data` table — persist and retrieve historical wind keyed by `ride_id + stop_order`
4. `routes/riders.py` / `routes/main.py` — assemble wind context, pass to templates (no computation here)
5. Jinja2 templates — render wind columns with inline `rgba()` styles; conditional warning banner

### Critical Pitfalls

1. **Archive API 5-day ERA5 data lag** — Requesting `end_date` within 5 days of today returns 400 or empty data. Always compute `latest_available_date = today - timedelta(days=5)` and fall back to the forecast API's `past_days` parameter for recent rides. Add a `wind_data_source` column to `ride_wind_data` to track which source was used.

2. **Single vs. multi-location API response shape** — Open-Meteo returns a `dict` for 1 location but a `list` for 2+ locations. The forecast path already handles this. The archive fetch must apply the same normalization via a shared `_normalize_open_meteo_response()` helper — failing to do so causes `TypeError` crashes specifically for multi-stop routes.

3. **Meteorological "wind from" direction inversion** — `wind_direction_10m` is the direction wind blows FROM, not toward. The crosswind `sin` projection must apply `wind_travel_deg = (wind_from_deg + 180) % 360` — the same inversion already in `headwind_component()`. Skipping this inverts all wind labels.

4. **Stop distance unit mismatch (miles vs. meters)** — RWGPS track points store `d` in meters; ride plan stops store `distance_miles`. All interpolation must convert to meters at function entry and never re-convert internally. A unit bug places the "40-mile control" 40 meters into the route.

5. **Wind threshold constants defined in two places** — The warning banner (30 km/h / 15 km/h thresholds) and cell intensity scaling must share constants defined once in `services/weather.py`. Inline magic numbers in route handlers and templates drift over time and produce contradictory UI signals.

## Implications for Roadmap

Based on research, the architecture's build order is clear and dependency-driven. Seven coherent phases emerge; phases 1-2 are pure Python with no UI, making them fast to build and easy to test in isolation.

### Phase 1: Wind Math Foundation
**Rationale:** Pure Python service extensions with no external dependencies — `crosswind_component()`, `classify_wind()`, `wind_cell_style()`, and named threshold constants in `services/weather.py`. All downstream features depend on these. Unit-testable in isolation before any API or DB work begins.
**Delivers:** Correct wind classification logic, color intensity helper, centralized constants
**Addresses:** Headwind/tailwind/crosswind classification (table stakes), color-coded cells (table stakes)
**Avoids:** Wind direction inversion bug (Pitfall 3), threshold constant divergence (Pitfall 6)

### Phase 2: Stop-to-Coordinate Interpolation
**Rationale:** `get_stop_coordinates()` in `services/rwgps.py` is the prerequisite for every wind column — forecast and historical. Must be built and unit-tested (with miles/meters edge cases) before any API fetch work.
**Delivers:** Per-stop lat/lng from RWGPS track points via linear interpolation by cumulative distance
**Addresses:** Stop coordinate resolution (hardest table-stakes item per FEATURES.md)
**Avoids:** Distance unit mismatch bug (Pitfall 4)

### Phase 3: Forecast Wind in Base Ride Plan
**Rationale:** First end-to-end feature combining Phase 1 + Phase 2 with the existing Open-Meteo forecast fetch. Proves the full data flow (stop interpolation → batch API call → wind classification → Jinja2 template render) before tackling historical data or the warning banner.
**Delivers:** Color-coded wind columns in the base ride plan table (the primary milestone deliverable)
**Uses:** `requests`, Flask-Caching SimpleCache (1h TTL), inline Jinja2 styles
**Avoids:** Per-stop API calls anti-pattern (batch everything in one call), Tailwind dynamic class bug
**Implements:** Forecast data flow: RWGPS track points → stop interpolation → Open-Meteo batch → cache → template

### Phase 4: Heavy Wind Warning Banner (Upcoming Brevets)
**Rationale:** Shares the Phase 3 forecast data flow; adds only a threshold check and a conditional banner block. Low implementation cost once Phase 3 exists. Delivers high-value safety signal for upcoming rides within 7-day forecast window.
**Delivers:** Warning banner on upcoming brevets page when max wind >30 km/h or avg headwind >15 km/h
**Addresses:** Heavy wind warning (table stakes), forecast window boundary check
**Avoids:** Banner firing for rides outside 7-day forecast range (UX pitfall)

### Phase 5: Forecast Wind in Custom Ride Plan
**Rationale:** Depends on Phase 3. Custom plan view uses the same template extension and same data flow — the only complexity is ensuring wind is resolved for the merged stop list (base + overrides), not just base stops.
**Delivers:** Wind columns in custom ride plans with rider-overridden timing
**Addresses:** Custom plan wind (differentiator per FEATURES.md)
**Avoids:** Custom plan fetching only base stops and missing override stops ("Looks Done But Isn't" item)

### Phase 6: Historical Wind — Archive API + DB Persistence
**Rationale:** Independent of forecast work (different endpoint, different parameters, different storage). Must be built as a complete unit: `ride_wind_data` DB schema (with `wind_data_source` column) + archive fetch + fallback logic + DB persistence. Do not build the Strava analysis UI until this layer is proven correct.
**Delivers:** `ride_wind_data` table; `fetch_historical_wind()` with 5-day lag fallback; one-time-per-ride fetch-and-persist pattern
**Uses:** Open-Meteo Archive API (`archive-api.open-meteo.com/v1/archive`), `psycopg2.extras.Json()`, raw SQL migration
**Avoids:** Archive data lag bug (Pitfall 1), archive vs. forecast source mismatch (Pitfall 2), multi-location response normalization bug (Pitfall 5), storing forecast wind in DB (anti-pattern)

### Phase 7: Historical Wind in Strava Analysis
**Rationale:** Depends on Phase 6 (DB persistence layer must exist). Last phase because it touches the most complex existing view and has no UI until Phase 6 is complete. Also adds clickable 2025/2026 season headers as a low-effort addition to this phase.
**Delivers:** Historical wind column in Strava ride analysis page; "store once, serve many" for completed 2026 rides; clickable season headers
**Addresses:** Historical wind for completed rides (table stakes), DB persistence differentiator, clickable headers (P2)
**Avoids:** Re-fetching archive API on every Strava analysis page load, timezone UTC misalignment for local ride start times

### Phase Ordering Rationale

- Phases 1-2 are pure service-layer work (no routes, no templates, no API calls beyond existing) — they build a verified foundation before any user-visible work begins.
- Phases 3-5 all use the forecast data flow established in Phase 3; each adds a new surface without changing the underlying logic.
- Phase 6 is deliberately isolated from Phase 3 — forecast and historical are separate storage layers and mixing them creates Pitfall 2 (source mismatch). Building Phase 6 as a standalone unit prevents that.
- Phase 7 requires Phase 6 to be complete and correct before building any UI on top of it. Clickable headers are bundled here because they have no value until the destination (detail page with wind) exists.
- The build order matches ARCHITECTURE.md's explicit dependency sequence: weather.py extensions → rwgps.py interpolation → base plan → custom plan → warning → archive → historical display.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 6 (Archive API + DB Persistence):** The 5-day data lag fallback logic and `wind_data_source` tracking require careful implementation. The boundary between archive and `past_days` data has known accuracy differences (Open-Meteo GitHub issue #1231). Validate the fallback logic with real API calls before writing any production code.
- **Phase 7 (Strava Analysis):** UTC-to-local time conversion for Strava `activity.start_date` needs verification against actual Strava API response format. Timezone alignment for hourly wind index is the most likely source of subtle bugs in this phase.

Phases with standard patterns (research-phase not needed):
- **Phase 1 (Wind Math):** Pure math (sin/cos projections) with established formulas. Existing tests in `tests/test_weather.py` confirm the pattern.
- **Phase 2 (Stop Interpolation):** Linear interpolation over a sorted array — standard algorithm. No research needed, just careful unit testing.
- **Phase 3 (Forecast Wind):** Extends an existing, working API fetch pattern. The batch call shape is already proven in `fetch_route_weather()`.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Existing codebase verified directly; Open-Meteo API verified against official docs; no new dependencies to validate |
| Features | MEDIUM | Ecosystem surveyed via web; no authoritative spec for randonneuring-specific wind tools; PROJECT.md fills the gap for this app's specific requirements |
| Architecture | HIGH | Based on direct codebase inspection; build order derived from explicit dependency analysis; Vercel serverless constraints verified against official docs |
| Pitfalls | HIGH | Critical pitfalls verified against Open-Meteo GitHub issues (#1231, #1480, #696, #850), official docs, and codebase inspection |

**Overall confidence:** HIGH

### Gaps to Address

- **Open-Meteo free tier rate limits in practice:** 10,000 req/day limit is documented; actual behavior at limit (429 vs. silent drop) is unverified for this use case. Monitor in early production; add retry logic if 429s appear.
- **RWGPS API track point coverage for Indian routes (2025 brevets):** The archive API claims 0.1° resolution globally, but ERA5 coverage quality for the Bay Area vs. Pune/Mumbai routes has not been empirically tested. Validate historical fetch against a known 2025 completed ride before committing to the 2025 backfill scope.
- **Custom plan stop list merge behavior:** FEATURES.md notes custom plans merge base stops with overrides and that hidden stops must be excluded, added stops included. The exact merge logic in the existing codebase should be reviewed before Phase 5 planning to confirm wind can be fetched for the correct merged set.

## Sources

### Primary (HIGH confidence)
- [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api) — endpoint URL, wind variables, batch format, ERA5 date range
- [Open-Meteo GitHub issue #1231](https://github.com/open-meteo/open-meteo/issues/1231) — archive vs. forecast data source differences confirmed by maintainer
- [Open-Meteo GitHub issue #1480](https://github.com/open-meteo/open-meteo/issues/1480) — 5-day archive lag and `past_days` fallback
- [Open-Meteo GitHub discussion #696](https://github.com/open-meteo/open-meteo/discussions/696) — single vs. multi-location response shape normalization
- [Open-Meteo GitHub issue #850](https://github.com/open-meteo/open-meteo/issues/850) — timezone parameter behavior
- [Flask-Caching on PyPI](https://pypi.org/project/Flask-Caching/) — version 2.1.0 compatibility with Flask 3.0.0
- [Tailwind CSS dynamic styles docs](https://tailwindcss.com/docs/adding-custom-styles) — JIT static purging limitation confirmed
- [Vercel serverless function constraints](https://vercel.com/docs/limits) — stateless invocations, no persistent in-memory state
- Codebase analysis — `services/weather.py`, `services/rwgps.py`, `models.py`, `cache.py`, `requirements.txt`, `tailwind.config.js`, `tests/test_weather.py`

### Secondary (MEDIUM confidence)
- [myWindsock](https://mywindsock.com/plot/) — wind visualization patterns, per-point data, Strava integration, 75th percentile wind threshold norms
- [Headwind App](https://headwindapp.com/) — route color coding, difficulty rating, historical modes
- [Epic Ride Weather](https://www.epicrideweather.com/) — wind vectors, minute-by-minute forecast approach
- [RoadBikeRider — wind speed thresholds](https://www.roadbikerider.com/too-much-wind-cycling/) — community cycling wind danger thresholds supporting 30 km/h warning level

### Tertiary (LOW confidence)
- [ECMWF ERA5 wind component calculation](https://confluence.ecmwf.int/pages/viewpage.action?pageId=133262398) — ERA5 data processing pipeline context; referenced for archive lag explanation

---
*Research completed: 2026-03-23*
*Ready for roadmap: yes*
