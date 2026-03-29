# Stack Research

**Domain:** Wind forecast + historical weather visualization for a cycling randonneuring app
**Researched:** 2026-03-23
**Confidence:** HIGH — existing stack is verified from codebase; Open-Meteo archive API verified from official docs; Flask-Caching current version verified from PyPI

---

## Existing Stack (Do Not Change)

This milestone extends an existing system. These are fixed constraints, not choices:

| Technology | Current Version | Role |
|------------|----------------|------|
| Flask | 3.0.0 | Web framework |
| Jinja2 | 3.1.2 | Server-side templating |
| Tailwind CSS | (via npm) | Utility CSS — compiled at build time |
| psycopg2-binary | 2.9.9 | PostgreSQL driver |
| requests | 2.31.0 | HTTP client for all external APIs |
| Flask-Caching | 2.1.0 | In-memory cache (SimpleCache, Vercel-compatible) |
| Werkzeug | 3.0.1 | WSGI utilities |
| gunicorn | 21.2.0 | WSGI server |
| Vercel | serverless | Hosting platform |

---

## Recommended Stack for Wind Integration

### No New Core Dependencies Required

The wind integration milestone needs **zero new runtime dependencies**. All required capabilities are already present:

| Need | Covered By | Why Sufficient |
|------|-----------|----------------|
| Open-Meteo archive HTTP calls | `requests` 2.31.0 | Same call pattern as existing forecast fetch — just change URL and add `start_date`/`end_date` params |
| Wind data persistence | `psycopg2-binary` 2.9.9 + PostgreSQL | New `ride_wind_data` table; JSONB column for per-stop wind arrays |
| Forecast caching (1-hour TTL) | `Flask-Caching` 2.1.0 SimpleCache | Already used in `weather.py` with `cache.set()` / `cache.get()` pattern |
| Color-coded wind cells | Jinja2 inline `style=` attributes | Dynamic color intensity requires computed values — Tailwind static classes cannot express this |
| Wind classification logic | Python stdlib `math` | `math.sin()` for crosswind projection, `math.cos()` already used for headwind |
| Template rendering | Jinja2 3.1.2 | Table column additions are Jinja2 `for` loop extensions |

### New Table: `ride_wind_data`

The only new "infrastructure" is a database table. No migration library needed — the project uses raw SQL migrations via `migrations/apply_migration_*.py` scripts.

Recommended schema:

```sql
CREATE TABLE ride_wind_data (
    id SERIAL PRIMARY KEY,
    ride_id INTEGER NOT NULL,                  -- FK to ride table
    ride_plan_id INTEGER,                      -- FK to ride_plan (nullable for Strava-only)
    data_type TEXT NOT NULL CHECK (data_type IN ('forecast', 'historical')),
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    wind_data JSONB NOT NULL,                  -- array of per-stop wind objects
    UNIQUE (ride_id, ride_plan_id, data_type)
);
CREATE INDEX idx_ride_wind_data_ride_id ON ride_wind_data(ride_id);
```

Use `psycopg2.extras.Json(data)` to insert Python dicts as JSONB — this is already imported in `models.py` via `psycopg2.extras`.

---

## Supporting Libraries

### Flask-Caching 2.1.0 → Upgrade to 2.3.1 (Optional, Low Priority)

| Library | Pin | Purpose | When to Use |
|---------|-----|---------|-------------|
| Flask-Caching | 2.3.1 | Upgrade from 2.1.0 (current in repo) | If upgrading — latest stable as of 2025-02-23; no breaking changes from 2.1.0 |

**Decision:** Do not upgrade as part of this milestone. Flask-Caching 2.1.0 already provides everything needed (SimpleCache, `cache.memoize()`, `cache.set()`/`cache.get()` with timeout). Upgrading introduces risk with no wind-feature benefit. Track for a later maintenance pass.

### No New Libraries

| Library | Why Rejected |
|---------|-------------|
| `openmeteo-requests` (official SDK) | Adds a dependency for a pattern `requests` already handles. The existing `fetch_route_weather()` pattern (comma-separated lat/lng) maps directly to the archive API. Zero value over raw requests. |
| `retry` / `urllib3.Retry` | Only needed if Open-Meteo proves unreliable in production. The existing `requests.get(..., timeout=15)` pattern is sufficient for now. Add retry adapter in a follow-on patch if 429s appear. |
| Celery / RQ | Background tasks for wind fetching. Rejected because Vercel serverless has no persistent worker support. Historical wind fetch happens on first page view, then persists to DB. That's the correct pattern. |
| `numpy` | For bearing math / wind projections. Rejected — `math.sin()` / `math.cos()` from stdlib handle everything. `numpy` is in `requirements-dev.txt` for evals only; do not promote to production. |
| Chart.js / D3 | Wind rose charts. Rejected — PROJECT.md explicitly specifies color-coded table cells, not charts. The no-JS-framework constraint is intentional. |

---

## Open-Meteo Archive API (Verified)

**Confidence: HIGH** — Verified against official docs at `open-meteo.com/en/docs/historical-weather-api`.

| Property | Value |
|----------|-------|
| Endpoint | `https://archive-api.open-meteo.com/v1/archive` |
| Parameters | `latitude`, `longitude`, `start_date` (yyyy-mm-dd), `end_date` (yyyy-mm-dd), `hourly` |
| Wind variables | `wind_speed_10m`, `wind_direction_10m`, `wind_gusts_10m`, `wind_speed_100m`, `wind_direction_100m` |
| Batch support | Yes — comma-separated lat/lng arrays, same as forecast API: `latitude=37.77,37.81&longitude=-122.41,-122.38` |
| Response shape | Single location → dict. Multiple locations → list of dicts. (Matches existing `fetch_route_weather()` normalization logic.) |
| Historical range | 1940 to present, 0.1° or 0.25° resolution |
| Free tier limit | 10,000 API calls/day, 5,000/hour, 600/minute |
| Auth | None required for free tier |

**Fetch pattern for historical wind** (matches existing code shape):

```python
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

def fetch_historical_wind(sample_points, date_str):
    """Fetch historical wind for sample points on a specific date.

    date_str: 'YYYY-MM-DD' — the ride date.
    Returns list of per-location hourly dicts (same shape as forecast).
    """
    lats = ",".join(str(round(p['lat'], 4)) for p in sample_points)
    lngs = ",".join(str(round(p['lng'], 4)) for p in sample_points)
    params = {
        'latitude': lats,
        'longitude': lngs,
        'start_date': date_str,
        'end_date': date_str,
        'hourly': 'wind_speed_10m,wind_direction_10m,wind_gusts_10m',
        'timezone': 'auto',
    }
    resp = requests.get(OPEN_METEO_ARCHIVE_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return [data] if isinstance(data, dict) else data
```

---

## Wind Cell Color Rendering Pattern

**Confidence: HIGH** — Verified from existing codebase patterns and Tailwind's confirmed limitation with dynamic values.

Tailwind cannot handle computed color values — class names must be statically known at build time. Dynamic wind intensity colors (e.g., opacity or shade scaling with wind speed) require **inline `style=` attributes computed in Python and passed through Jinja2 templates**.

### Color Scheme (from PROJECT.md)

| Wind Type | Color | Intensity Rule |
|-----------|-------|---------------|
| Tailwind | Green (`#22c55e` base) | Speed × opacity |
| Headwind | Red (`#ef4444` base) | Speed × opacity |
| Crosswind | Blue (`#3b82f6` base) | Speed × opacity |

### Python helper (in `weather.py`):

```python
def wind_cell_style(wind_type, wind_speed_kmh):
    """Return inline CSS style string for a wind table cell.

    Opacity scales with speed: 0 km/h → 0.15, 30+ km/h → 0.85.
    Font size scales: < 10 km/h → 0.75rem, 30+ km/h → 0.95rem.
    """
    color_map = {
        'tailwind': '34, 197, 94',    # green-500 RGB
        'headwind': '239, 68, 68',    # red-500 RGB
        'crosswind': '59, 130, 246',  # blue-500 RGB
    }
    rgb = color_map.get(wind_type, '113, 128, 150')  # gray fallback
    opacity = min(0.85, max(0.15, wind_speed_kmh / 35.0))
    font_size = '0.95rem' if wind_speed_kmh >= 25 else '0.80rem'
    font_weight = '700' if wind_speed_kmh >= 25 else '500'
    return (
        f"background-color: rgba({rgb}, {opacity:.2f}); "
        f"font-size: {font_size}; font-weight: {font_weight};"
    )
```

### Jinja2 template pattern:

```html
<td style="{{ stop.wind_cell_style }}" class="px-2 py-1 text-center text-white">
    {{ stop.wind_speed_kmh }} km/h
    <span class="block text-xs">{{ stop.wind_type }}</span>
</td>
```

---

## Caching Strategy

**Confidence: HIGH** — Based on existing codebase analysis and Vercel serverless constraints.

| Data Type | Cache Layer | TTL | Rationale |
|-----------|------------|-----|-----------|
| Forecast wind (upcoming rides) | Flask-Caching SimpleCache | 1 hour | Forecasts change; 1hr matches existing weather cache TTL |
| Historical wind (completed rides) | PostgreSQL `ride_wind_data` table | Permanent | Archive data doesn't change; DB lookup is faster than API call on repeat views |
| RWGPS track points | Flask-Caching SimpleCache | 5 min (existing `CACHE_TIMEOUT`) | Track points don't change per route; existing pattern |

**SimpleCache on Vercel:** SimpleCache uses per-process in-memory storage. On Vercel, each function invocation may be a different process — cache will sometimes miss. This is acceptable for forecast wind (just means an extra Open-Meteo call, within rate limits). Historical wind is in PostgreSQL, so it is never lost between invocations.

Do not add Redis or Memcached. The complexity and cost are not justified for this use case and Vercel's serverless model makes persistent cache daemons impractical.

---

## Crosswind Calculation (New Function in `weather.py`)

**Confidence: HIGH** — Pure math, no library needed.

```python
def crosswind_component(wind_speed, wind_from_deg, rider_bearing_deg):
    """Return crosswind component (positive = wind from right, negative = from left).

    Magnitude indicates how much of wind_speed acts perpendicular to rider direction.
    """
    if wind_speed == 0:
        return 0
    wind_travel_deg = (wind_from_deg + 180) % 360
    angle = math.radians(wind_travel_deg - rider_bearing_deg)
    return round(wind_speed * math.sin(angle), 1)


def classify_wind(wind_speed, wind_from_deg, rider_bearing_deg):
    """Classify wind into headwind/tailwind/crosswind with component values.

    Returns dict with:
        type: 'headwind' | 'tailwind' | 'crosswind'
        headwind_kmh: float (positive = against rider)
        crosswind_kmh: float (positive = from right)
        wind_speed_kmh: float (total speed)
    """
    hw = headwind_component(wind_speed, wind_from_deg, rider_bearing_deg)
    cw = crosswind_component(wind_speed, wind_from_deg, rider_bearing_deg)

    # 45-degree threshold: if |headwind| > |crosswind|, classify as head/tail
    if abs(hw) >= abs(cw):
        wind_type = 'headwind' if hw > 0 else 'tailwind'
    else:
        wind_type = 'crosswind'

    return {
        'type': wind_type,
        'headwind_kmh': hw,
        'crosswind_kmh': cw,
        'wind_speed_kmh': wind_speed,
    }
```

---

## Stop Coordinate Interpolation (New Function in `weather.py`)

**Confidence: HIGH** — RWGPS track point format verified from existing `sample_track_points()` code.

Ride plan stops have `distance_miles` but not `lat/lng`. The track points (`y`=lat, `x`=lng, `d`=distance_m) enable interpolation.

```python
def interpolate_stop_coordinates(stop_distance_miles, track_points):
    """Find lat/lng for a stop by linear interpolation of RWGPS track points.

    stop_distance_miles: cumulative distance of the stop.
    track_points: raw RWGPS track points with y=lat, x=lng, d=distance_m.
    Returns {'lat': float, 'lng': float} or None if track_points empty.
    """
    if not track_points:
        return None

    stop_distance_m = stop_distance_miles * 1609.344

    # Find surrounding track points
    prev_pt = track_points[0]
    for pt in track_points[1:]:
        if pt['d'] >= stop_distance_m:
            # Linear interpolation between prev_pt and pt
            span = pt['d'] - prev_pt['d']
            if span == 0:
                return {'lat': pt['y'], 'lng': pt['x']}
            ratio = (stop_distance_m - prev_pt['d']) / span
            lat = prev_pt['y'] + ratio * (pt['y'] - prev_pt['y'])
            lng = prev_pt['x'] + ratio * (pt['x'] - prev_pt['x'])
            return {'lat': round(lat, 5), 'lng': round(lng, 5)}
        prev_pt = pt

    # Past end of track — use last point
    last = track_points[-1]
    return {'lat': last['y'], 'lng': last['x']}
```

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| Open-Meteo Archive API | Visual Crossing, Tomorrow.io | If Open-Meteo archive proves unreliable for India/Bay Area geographies. Both have free tiers but add API key management complexity. |
| JSONB column for wind data | Separate `ride_wind_stop` rows table | If you need to query individual stop wind data in SQL (e.g., "all headwind stops > 30 km/h across all rides"). JSONB is simpler for this use case where wind data is always fetched per-ride. |
| Flask-Caching SimpleCache | Redis | If this app moves to a persistent server or adds background workers. Redis is overkill for current Vercel serverless deployment. |
| psycopg2.extras.Json() | `json.dumps()` with cast | Both work. `Json()` is cleaner and handles edge cases. Use `Json()`. |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| Tailwind dynamic color classes (e.g., `bg-green-500`) for wind intensity | Tailwind purges unused classes at build time — dynamically constructed class names like `bg-green-${intensity}` will be stripped. Wind intensity is computed at runtime, not build time. | Python-computed `style=` attributes in Jinja2 templates |
| `openmeteo-requests` SDK | Adds a third-party dependency for a pattern `requests` already covers. The SDK wraps the same REST API. Zero benefit, extra dependency surface area. | Raw `requests.get()` matching existing `fetch_route_weather()` pattern |
| Celery / RQ background jobs | Vercel serverless functions are stateless and terminate after response — no persistent worker process is possible. | Fetch-on-demand + persist-to-DB pattern: first request fetches and stores, subsequent requests read from DB |
| SQLAlchemy ORM | Project uses raw psycopg2 SQL throughout — mixing in an ORM would create two query patterns in the same codebase. | Raw SQL in `models.py` following existing `_execute()` helper pattern |
| `numpy` for wind math | `numpy` is in dev dependencies for evals only. Importing it in production code inflates Vercel function bundle size significantly. All wind math needs only `math.sin()`, `math.cos()`, `math.radians()`. | Python stdlib `math` module |
| Real-time WebSocket wind updates | PROJECT.md explicitly marks "real-time wind updates during rides" as out of scope. Adds frontend complexity with no randonneuring planning value. | Static wind forecast fetched at page load time |

---

## Stack Patterns by Variant

**If stop has no RWGPS track point coverage (route_data unavailable):**
- Skip wind cell rendering; show "—" placeholder
- Do not block the rest of the ride plan table from rendering
- Log a warning; do not raise an exception

**If Open-Meteo archive returns no data for a date (pre-1940 or future):**
- Show "No data" in wind column
- This shouldn't occur for 2025/2026 rides but handle gracefully

**If forecast wind is requested outside the 16-day Open-Meteo forecast window:**
- Fall through to archive API fetch (treat as historical)
- Log the fallback; surface it to the user if relevant

**If ride_wind_data already exists in DB (historical, re-fetched):**
- Return existing data; do not re-fetch archive API
- Provide an admin route or parameter to force refresh if needed

---

## Installation

No new packages needed for production. The existing `requirements.txt` is sufficient.

If upgrading Flask-Caching (optional, deferred):

```bash
# Update requirements.txt pin only
pip install Flask-Caching==2.3.1
```

For development/testing:

```bash
# Already in requirements-dev.txt — no changes needed
pip install -r requirements-dev.txt
```

---

## Version Compatibility

| Package | Version | Compatibility Notes |
|---------|---------|-------------------|
| Flask 3.0.0 | Flask-Caching 2.1.0 | Compatible — Flask-Caching 2.x targets Flask 2.x+ |
| psycopg2-binary 2.9.9 | PostgreSQL JSONB | `psycopg2.extras.Json()` works for all psycopg2 2.x versions |
| requests 2.31.0 | Open-Meteo archive API | No SDK dependency — raw HTTP, no version concern |
| Jinja2 3.1.2 | Flask 3.0.0 | Bundled together; no conflict |

---

## Sources

- [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api) — endpoint URL, wind variables, batch format, date range (HIGH confidence, official docs)
- [Open-Meteo Pricing](https://open-meteo.com/en/pricing) — 10,000 req/day free tier limit (MEDIUM confidence, via WebSearch verified against GitHub issue #438 corroborating the limit)
- [Flask-Caching on PyPI](https://pypi.org/project/Flask-Caching/) — latest version 2.3.1 as of 2025-02-23 (HIGH confidence, official PyPI)
- [psycopg2.extras docs](https://www.psycopg.org/docs/extras.html) — Json() adapter pattern (HIGH confidence, official docs)
- [Tailwind CSS dynamic styles](https://tailwindcss.com/docs/adding-custom-styles) — static class purging limitation confirmed (HIGH confidence, official docs via WebSearch)
- [Vercel serverless function constraints](https://vercel.com/docs/limits) — stateless invocations, no persistent in-memory state (HIGH confidence, official Vercel docs)
- Codebase analysis — `services/weather.py`, `cache.py`, `db.py`, `models.py`, `requirements.txt`, `tailwind.config.js` reviewed directly (HIGH confidence)

---

*Stack research for: Wind forecast integration — Team Asha Randonneuring*
*Researched: 2026-03-23*
