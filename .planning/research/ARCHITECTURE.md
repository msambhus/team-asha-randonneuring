# Architecture Research

**Domain:** Wind forecast integration into existing Flask randonneuring app
**Researched:** 2026-03-23
**Confidence:** HIGH — based on direct codebase inspection

## Standard Architecture

### System Overview

```
┌───────────────────────────────────────────────────────────────┐
│                     Flask Routes Layer                         │
│                                                               │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────┐  │
│  │  /ride-plan/<slug>│  │  /upcoming-brevets│  │ /strava/.. │  │
│  │  ride_plan_detail │  │  upcoming()       │  │ analysis   │  │
│  │  custom_plan_view │  │  (main.py)        │  │ (riders.py)│  │
│  │  (riders.py)      │  └────────┬─────────┘  └─────┬──────┘  │
│  └────────┬──────────┘           │                  │         │
└───────────┼──────────────────────┼──────────────────┼─────────┘
            │                      │                  │
┌───────────┼──────────────────────┼──────────────────┼─────────┐
│           ▼        Services Layer ▼                  ▼         │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              services/weather.py  (EXTEND HERE)          │  │
│  │  existing: headwind_component(), calculate_bearing(),    │  │
│  │            sample_track_points(), fetch_route_weather(), │  │
│  │            get_cached_route_weather()                    │  │
│  │  add:      crosswind_component(), classify_wind_type(),  │  │
│  │            interpolate_stop_coordinates(),               │  │
│  │            fetch_historical_wind(), get_wind_for_stops() │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                               │
│  ┌──────────────────┐  ┌──────────────────┐                   │
│  │  services/rwgps.py│  │  models.py        │                  │
│  │  fetch_route()    │  │  (add wind queries│                  │
│  │  track_points     │  │  ride_wind_data   │                  │
│  └──────────────────┘  └──────────────────┘                   │
└───────────────────────────────────────────────────────────────┘
            │                      │
┌───────────┼──────────────────────┼──────────────────────────┐
│           ▼    External APIs     ▼                           │
│  ┌────────────────────┐  ┌──────────────────┐               │
│  │  Open-Meteo         │  │  RWGPS API        │               │
│  │  Forecast API       │  │  /routes/{id}.json│               │
│  │  Archive API        │  │  track_points[]   │               │
│  │  (same shape)       │  │  y=lat, x=lng,    │               │
│  └────────────────────┘  │  d=distance_m      │               │
│                           └──────────────────┘               │
└───────────────────────────────────────────────────────────────┘
            │
┌───────────┼──────────────────────────────────────────────────┐
│           ▼     Data Layer (PostgreSQL + Flask-Caching)       │
│  ┌──────────────────┐  ┌──────────────────┐                  │
│  │  ride_wind_data   │  │  Flask-Caching    │                  │
│  │  (persist histor- │  │  SimpleCache      │                  │
│  │   ical wind only) │  │  TTL=1h forecast  │                  │
│  └──────────────────┘  │  TTL=5m track pts │                  │
│                         └──────────────────┘                  │
└───────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Current State |
|-----------|----------------|---------------|
| `services/weather.py` | All wind math and Open-Meteo API calls | Exists — extend with crosswind, historical fetch, stop-level interpolation |
| `services/rwgps.py` | Fetch route track points; stop-to-coordinate interpolation | Exists — `fetch_route()` returns `track_points`; add `get_stop_coordinates()` |
| `models.py` | SQL queries for `ride_wind_data` table | New queries needed for persist/fetch historical wind |
| `routes/riders.py` | Assemble wind data for template context on ride plan views | Extend `ride_plan_detail()` and `custom_ride_plan_view()` with wind context |
| `routes/main.py` | Pass wind warning flag to upcoming brevets template | Extend `upcoming()` with wind warning computation |
| `routes/riders.py` (analysis) | Load historical wind for Strava analysis page | Extend `ride_strava_analysis()` and `my_strava_analysis()` |
| `templates/ride_plan_detail.html` | Render wind columns with color-coded cells | Add wind columns using inline styles for dynamic intensity |
| `templates/upcoming_brevets.html` | Show heavy wind warning banner | Add conditional banner block |
| `templates/strava_ride_analysis.html` | Render historical wind column alongside pace data | Add wind column |
| `ride_wind_data` (DB table) | Persist fetched historical wind keyed by route+date | New table — avoids repeat archive API calls |

## Recommended Project Structure

```
services/
├── weather.py           # Extend: add crosswind, historical fetch, stop interpolation
├── rwgps.py             # Extend: add get_stop_coordinates(stops, track_points)
models.py                # Extend: add wind data queries (get/save ride_wind_data)
routes/
├── riders.py            # Extend: wind context in ride_plan_detail, custom view, strava analysis
├── main.py              # Extend: wind warning in upcoming()
templates/
├── ride_plan_detail.html       # Extend: wind columns
├── upcoming_brevets.html       # Extend: wind warning banner
├── strava_ride_analysis.html   # Extend: historical wind column
schema/
└── schema.sql           # Add: ride_wind_data table migration
migrations/
└── add_ride_wind_data.py       # New: run once to create table
```

### Structure Rationale

- **All wind math stays in `services/weather.py`:** It already owns headwind math, bearing calc, and Open-Meteo calls. Crosswind and historical fetch are natural extensions. No new service file needed.
- **Stop coordinate interpolation belongs in `services/rwgps.py`:** Track point data is RWGPS-specific. The interpolation logic takes `stops` (no lat/lng) + `track_points` (RWGPS format) and returns coordinates — ownership is clear.
- **`ride_wind_data` is the only new DB table:** Forecast data is ephemeral (cache only). Historical wind must persist to avoid re-fetching archive API on every page load.
- **Routes are thin orchestrators:** Routes call services, pass results to templates. No wind math in route handlers.

## Architectural Patterns

### Pattern 1: Stop-Coordinate Interpolation

**What:** Ride plan stops store `distance_miles` but no lat/lng. RWGPS track points have `y`=lat, `x`=lng, `d`=distance_m. To fetch wind per stop, each stop needs coordinates interpolated from the nearest track point by cumulative distance.

**When to use:** Any time you need a lat/lng for a named stop on a route.

**Trade-offs:** Interpolation is O(n) over track points per stop, but track points are cached after the first RWGPS fetch. Accuracy is sufficient — stops are typically spaced 20–80 miles apart; nearest-track-point within 500m is adequate.

**Example:**
```python
# In services/rwgps.py
def get_stop_coordinates(stops, track_points):
    """Return stops enriched with lat/lng from nearest track point by distance."""
    # track_points sorted by d (distance_m); stops sorted by distance_miles
    result = []
    for stop in stops:
        stop_dist_m = (stop['distance_miles'] or 0) * 1609.344
        # Binary search or linear scan for closest track point
        best = min(track_points, key=lambda tp: abs(tp['d'] - stop_dist_m))
        result.append({**stop, 'lat': best['y'], 'lng': best['x']})
    return result
```

### Pattern 2: Forecast Wind — Cache Only, No DB

**What:** Forecast wind data is time-sensitive and changes hourly. Cache with 1-hour TTL using existing `get_cached_route_weather()`. Never write forecast wind to the DB.

**When to use:** Base ride plan view, custom ride plan view, upcoming brevets warning.

**Trade-offs:** On cache miss, adds one RWGPS API call (track points) + one Open-Meteo batch call. Acceptable latency (~1–2s). Cache key: `wind:stops:{route_slug}:{start_hour}`.

### Pattern 3: Historical Wind — DB-Backed Persistence

**What:** Historical wind from the Open-Meteo Archive API is stable (the past doesn't change). Fetch once, store in `ride_wind_data`, serve from DB on all subsequent requests.

**When to use:** Strava analysis page for completed 2026 rides.

**Trade-offs:** Requires a new DB table and a write path. Avoids repeated archive API calls per page load. Archive API is free/unlimited but latency is ~1–2s — one-time cost per ride is acceptable.

**Schema:**
```sql
CREATE TABLE ride_wind_data (
    id SERIAL PRIMARY KEY,
    ride_id INTEGER NOT NULL REFERENCES ride(id) ON DELETE CASCADE,
    stop_order INTEGER NOT NULL,
    stop_name TEXT,
    wind_speed_kmh NUMERIC,
    wind_direction_deg INTEGER,
    headwind_kmh NUMERIC,
    crosswind_kmh NUMERIC,
    wind_type TEXT,          -- 'headwind' | 'tailwind' | 'crosswind'
    fetched_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(ride_id, stop_order)
);
```

### Pattern 4: Wind Type Classification with Color Intensity

**What:** Wind cells need color (green/red/blue) with intensity proportional to speed. Dynamic intensity requires computed values, not static CSS classes. Use inline styles.

**When to use:** Every wind cell in ride plan tables and strava analysis.

**Trade-offs:** Inline styles are verbose in templates but unavoidable when color values are computed at render time (Tailwind's JIT purges classes not present in source at build time).

**Example:**
```python
# In services/weather.py
def classify_wind(wind_speed_kmh, wind_from_deg, rider_bearing_deg):
    """Return wind classification for display."""
    hw = headwind_component(wind_speed_kmh, wind_from_deg, rider_bearing_deg)
    cw = crosswind_component(wind_speed_kmh, wind_from_deg, rider_bearing_deg)  # new

    if abs(hw) >= abs(cw):
        wind_type = 'headwind' if hw > 0 else 'tailwind'
    else:
        wind_type = 'crosswind'

    intensity = min(wind_speed_kmh / 40.0, 1.0)  # 0.0 → 1.0
    return {'wind_type': wind_type, 'headwind_kmh': hw,
            'crosswind_kmh': cw, 'intensity': intensity}
```

```html
{# In template — inline style for dynamic color intensity #}
{% set alpha = (stop.wind_intensity * 0.7 + 0.1) %}
{% if stop.wind_type == 'headwind' %}
  <td style="background: rgba(220,38,38,{{ alpha }})">
{% elif stop.wind_type == 'tailwind' %}
  <td style="background: rgba(22,163,74,{{ alpha }})">
{% else %}
  <td style="background: rgba(37,99,235,{{ alpha }})">
{% endif %}
```

## Data Flow

### Forecast Wind — Ride Plan Page Request

```
User loads /ride-plan/300k-pune-mumbai
    │
    ▼
ride_plan_detail() in routes/riders.py
    │
    ├─ get_ride_plan_by_slug(slug)          ← DB: plan + stops (no lat/lng)
    ├─ get_ride_plan_stops(plan_id)         ← DB: stops with distance_miles
    │
    ├─ [WIND FETCH — new]
    │   ├─ rwgps.fetch_route(weather_route_id)  ← RWGPS API (cached)
    │   ├─ rwgps.get_stop_coordinates(stops, track_points)  ← interpolation
    │   ├─ weather.get_wind_for_stops(stop_coords, start_dt)
    │   │       ├─ weather.get_cached_route_weather(...)   ← cache / Open-Meteo
    │   │       └─ weather.classify_wind(per stop)
    │   └─ enriched stops with wind_type, headwind_kmh, intensity
    │
    ▼
render_template('ride_plan_detail.html', stops=enriched_stops, ...)
    │
    ▼ Jinja2 renders wind columns with inline-style color cells
```

### Historical Wind — Strava Analysis Page

```
User loads /rider/1234/ride/567/strava-analysis
    │
    ▼
ride_strava_analysis() in routes/riders.py
    │
    ├─ get_strava_ride_match(...)           ← DB: Strava match
    ├─ fetch_and_analyze(...)              ← Strava API streams
    │
    ├─ [HISTORICAL WIND — new]
    │   ├─ get_ride_wind_data(ride_id)      ← DB: check persistence
    │   │
    │   ├─ [if not cached in DB]
    │   │   ├─ rwgps.fetch_route(route_id)  ← RWGPS API
    │   │   ├─ rwgps.get_stop_coordinates(stops, track_points)
    │   │   ├─ weather.fetch_historical_wind(stop_coords, ride_date)
    │   │   │       └─ GET archive-api.open-meteo.com/v1/archive
    │   │   └─ save_ride_wind_data(ride_id, wind_rows)  ← DB write
    │   │
    │   └─ wind data (from DB or freshly fetched)
    │
    ▼
render_template('strava_ride_analysis.html', wind_data=wind_data, ...)
```

### Upcoming Brevets Wind Warning

```
User loads /upcoming
    │
    ▼
upcoming() in routes/main.py
    │
    ├─ get_upcoming_rides()                 ← DB: rides within 3-4 weeks
    ├─ get_upcoming_rusa_events()           ← DB
    │
    ├─ [WIND WARNING — new, for each upcoming ride with ride_plan_id]
    │   ├─ rwgps.fetch_route(weather_route_id)  ← RWGPS API (cached)
    │   ├─ rwgps.get_stop_coordinates(stops, track_points)
    │   ├─ weather.get_wind_for_stops(stop_coords, ride_start_dt)
    │   └─ weather.detect_heavy_wind(wind_data)
    │           → heavy if max_wind > 30 km/h OR avg_headwind > 15 km/h
    │
    ▼
render_template('upcoming.html', rides_with_wind=..., ...)
    │
    ▼ Conditional banner in template if heavy wind detected
```

### Key Data Flows Summary

1. **Forecast wind (ride plans, upcoming):** RWGPS track points → stop interpolation → Open-Meteo batch call → Flask cache (1h TTL) → template render
2. **Historical wind (Strava analysis):** Check `ride_wind_data` DB → on miss: RWGPS + Open-Meteo Archive → DB write → template render
3. **Custom plan wind:** Same as base plan forecast flow — custom stops have same `distance_miles`, same interpolation applies
4. **Wind color rendering:** Computed `wind_type` + `intensity` float → inline CSS `rgba()` in Jinja2 template

## Scaling Considerations

This is a small-team app (dozens of riders, not thousands). Scaling concerns are about API limits, not traffic.

| Scale | Architecture Adjustments |
|-------|--------------------------|
| Current (team use) | SimpleCache in-process, no shared cache needed. Each Vercel invocation may cold-start, but TTL prevents API hammering |
| Open-Meteo 10K/day limit | Batch all stops in a single lat/lng array call per route. One call per ride plan per hour is well within limits |
| Multiple concurrent requests | Cache key must include `route_slug + start_hour` to prevent duplicate fetches. Current `get_cached_route_weather()` already implements this pattern |

### Scaling Priorities

1. **First bottleneck:** RWGPS API call on every ride plan page load if track points are not cached. Fix: cache track points in Flask-Caching (5-min TTL) keyed by `rwgps:{route_id}`.
2. **Second bottleneck:** Open-Meteo archive call for each completed ride on Strava analysis. Fix (already planned): persist in `ride_wind_data` DB table — one-time cost per ride.

## Anti-Patterns

### Anti-Pattern 1: Per-Stop Open-Meteo Requests

**What people do:** Fetch wind individually for each stop (one HTTP request per stop).

**Why it's wrong:** A 300k ride has 10–15 stops — that's 10–15 Open-Meteo calls. The batch API accepts comma-separated lat/lng arrays and returns all locations in one call.

**Do this instead:** Call `fetch_route_weather(sample_points)` once with all stop coordinates. The existing function already handles the batch pattern — extend it rather than call it per-stop.

### Anti-Pattern 2: Storing Forecast Wind in the Database

**What people do:** Cache all wind data in DB for "persistence."

**Why it's wrong:** Forecast wind changes every hour. DB-stored forecasts go stale silently. Fetching stale wind 6 hours later and showing it as "current forecast" misleads riders.

**Do this instead:** Forecast wind lives in Flask-Caching with 1-hour TTL only. DB persistence is reserved for historical wind (archive data), which is immutable once the date has passed.

### Anti-Pattern 3: Putting Wind Logic in Route Handlers

**What people do:** Add bearing math, API calls, and classification logic inline in `ride_plan_detail()`.

**Why it's wrong:** `ride_plan_detail()` is already 200 lines. Wind logic is reused across base plan, custom plan, and upcoming brevets. Duplicating it in each route creates divergence.

**Do this instead:** All wind computation in `services/weather.py`. Routes call one function (`get_wind_for_stops(stop_coords, start_dt)`), get back enriched stop dicts, pass to template. No wind math in route files.

### Anti-Pattern 4: Dynamic Tailwind Classes for Wind Colors

**What people do:** Generate class names like `bg-red-{intensity}` dynamically in templates.

**Why it's wrong:** Tailwind's JIT compiler only includes classes present as complete strings in source files at build time. Dynamically constructed class names like `bg-red-{{ value }}` are purged.

**Do this instead:** Use inline styles `style="background: rgba(220,38,38,{{ alpha }})"` for dynamic intensity. Reserve Tailwind classes for static structural styling only.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Open-Meteo Forecast API | Batch GET with comma-separated lat/lng arrays; existing `fetch_route_weather()` | 1-hour cache TTL; 10K req/day limit |
| Open-Meteo Archive API | Same batch GET shape; add `start_date` and `end_date` params | Free, unlimited; latency ~1–2s |
| RWGPS API | Authenticated GET for route JSON; returns `track_points[]` with `y`, `x`, `d` fields | Credentials via env vars; cache track points to avoid repeated calls |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| `routes/riders.py` ↔ `services/weather.py` | Direct function call; route passes stop coords + start time, receives enriched wind dicts | No shared state; weather service is pure/stateless |
| `services/weather.py` ↔ `services/rwgps.py` | Route orchestrates both; weather service does not call RWGPS directly | Keep services decoupled — route fetches track points, passes to weather service |
| `services/weather.py` ↔ `models.py` | Route reads/writes `ride_wind_data`; weather service does not touch DB | Persistence is the route's responsibility, not the service |
| Forecast cache ↔ Historical DB | Strictly separate: forecast → Flask-Caching only; historical → DB only | No crossover prevents stale-forecast-in-DB bugs |

## Build Order Implications

Dependencies between components dictate this sequence:

1. **`services/weather.py` extensions first** — crosswind math and stop-level wind function are prerequisites for all rendering work. Pure functions, no DB or API dependencies — easy to test in isolation.

2. **`services/rwgps.py` stop interpolation second** — `get_stop_coordinates()` depends only on existing `fetch_route()` output format. Must exist before any route can call it.

3. **Wind columns in base ride plan** — depends on (1) and (2). This is the core visible feature and proves the full forecast data flow end-to-end.

4. **Wind columns in custom ride plan** — depends on (3). Custom plan view reuses the same template and same data flow; it's an extension of the base plan work, not independent.

5. **Wind warning on upcoming brevets** — depends on (1) and (2). Shares forecast flow but uses a different endpoint and a different threshold check.

6. **`ride_wind_data` DB table + historical fetch** — independent of forecast work; can run in parallel with (3-5) but has no UI until (7).

7. **Historical wind in Strava analysis** — depends on (6). Last because it requires both the DB persistence layer and a new template column in an existing complex page.

8. **Clickable ride headers** (2025/2026 seasons) — independent of all wind work; pure template change with no service dependencies. Can be done at any point.

---
*Architecture research for: Wind forecast integration into Flask randonneuring app*
*Researched: 2026-03-23*
