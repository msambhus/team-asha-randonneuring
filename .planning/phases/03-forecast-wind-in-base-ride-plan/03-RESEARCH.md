# Phase 3: Forecast Wind in Base Ride Plan - Research

**Researched:** 2026-03-23
**Domain:** Open-Meteo batch forecast API, Flask-Caching, Jinja2 template integration, ride plan route handler
**Confidence:** HIGH

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| WIND-06 | System fetches wind forecast for interpolated stop coordinates via Open-Meteo batch API with 1-hour cache | `fetch_route_weather()` and `get_cached_route_weather()` already exist in `services/weather.py` and handle exactly this. The new function `fetch_stop_wind()` (or reuse of existing) needs to accept stop coordinates from `get_stop_coordinates()`, call the batch API, and compute per-stop wind data using `headwind_component()`, `crosswind_component()`, `classify_wind()`, and `wind_cell_style()`. Cache key: `wind:{route_slug}:{start_hour}`. |
| BPLN-01 | User sees wind column in base ride plan detail page showing wind speed at each stop | `ride_plan_detail()` route in `routes/riders.py` must call `fetch_stop_wind()`, pass `stop_wind` list to template. Template adds `<th>Wind</th>` header and per-row `<td>` cell. |
| BPLN-02 | Wind cells have green background for tailwind, red for headwind, blue for crosswind | `wind_cell_style()` already returns `background: rgba(r,g,b,opacity)` correctly keyed to wind type. Template applies this as `style="background:{{ w.style.background }}"`. |
| BPLN-03 | Wind cell background color opacity scales with wind speed (light 0-5, medium 5-15, strong 15+ km/h) | `wind_cell_style()` already returns `opacity=0.15/0.35/0.65` by speed band. No new logic needed — consume what Phase 1 built. |
| BPLN-04 | Wind cell font size scales with wind speed (small for light, medium for moderate, large for strong) | `wind_cell_style()` already returns `font_size='0.75rem'/'0.875rem'/'1.0rem'`. Template applies as `style="font-size:{{ w.style.font_size }}"`. |
| BPLN-05 | Wind column only renders when wind data is available (graceful degradation) | Template wraps `<th>Wind</th>` and all wind `<td>` cells in `{% if stop_wind %}` guards. When `fetch_stop_wind()` fails (RWGPS unavailable, API error), route passes `stop_wind=None` and the column is absent. |
| BPLN-06 | Wind legend section explains green=tailwind, red=headwind, blue=crosswind color coding | New `<div>` block below the table, also guarded by `{% if stop_wind %}`. Three colored squares with labels. |
</phase_requirements>

## Summary

Phase 3 wires the Phase 1 wind math and Phase 2 coordinate interpolation into the base ride plan detail page. The infrastructure is already 90% built — `fetch_route_weather()`, `get_cached_route_weather()`, `get_stop_coordinates()`, `headwind_component()`, `crosswind_component()`, `classify_wind()`, and `wind_cell_style()` all exist and are unit-tested. What remains is:

1. A new `fetch_stop_wind(stops, track_points, plan, cache)` service function that chains `get_stop_coordinates()` → `fetch_route_weather()` (via cache) → per-stop wind computation → returns a list of wind data dicts with style applied.
2. A call to this function inside `ride_plan_detail()` in `routes/riders.py`, passing the result as `stop_wind` to the template.
3. Template changes in `ride_plan_detail.html`: add a `Wind` header column, per-row wind cell with inline styles, and a legend block below the table. All guarded by `{% if stop_wind %}`.

The hardest design question is the cache key. The existing `get_cached_route_weather()` uses `weather:{route_slug}:{start_hour_str}`. Phase 3 needs a parallel cache for stop-level wind. Use `wind:{plan_slug}:{start_hour}` with 1-hour TTL (matching the existing 3600-second pattern). The start hour is derived from `plan['start_time']` and today's date (forecast use case: rider checks wind for an upcoming event).

**Primary recommendation:** Add `fetch_stop_wind()` to `services/weather.py`. Call it in `ride_plan_detail()` with try/except so errors produce `stop_wind=None` (graceful degradation). Use inline styles throughout — the project decision log explicitly records "Inline styles for wind cell colors — Tailwind JIT static purging makes dynamic classes impossible."

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `services/weather.py` | existing | All wind math + Open-Meteo fetch + cache | Established module; all Phase 1/2 functions already here |
| `routes/riders.py` | existing | `ride_plan_detail()` route handler | The only place to add the wind fetch call |
| `templates/ride_plan_detail.html` | existing | Base plan UI | Target for Wind column and legend additions |
| `flask_caching` (`cache` from `cache.py`) | existing | 1-hour TTL for Open-Meteo responses | Already initialized; `cache.get()`/`cache.set()` with `timeout=3600` |
| `services/rwgps.py` `fetch_route()` | existing | Retrieve RWGPS track points for interpolation | `weather_route_id` already extracted in `ride_plan_detail()` |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pytest` | existing | Unit tests | All test execution |
| `unittest.mock.patch` | stdlib | Mock `requests.get` and cache | All weather service tests |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Inline styles in template | Tailwind dynamic classes | Tailwind JIT purges dynamic classes at build time — project decision already locked: use inline styles |
| Server-side wind fetch in route handler | JavaScript fetch on page load | Server-side is simpler, avoids CORS, consistent with rest of template approach (no async JS data fetching pattern exists in this app) |
| Single `fetch_stop_wind()` in weather.py | Inline logic in route handler | Service function is testable in isolation; route handlers should stay thin |

**Installation:** No new dependencies.

## Architecture Patterns

### Recommended Project Structure
No new files. All additions go into existing files:

```
services/
└── weather.py             # Add fetch_stop_wind() function

routes/
└── riders.py              # Add fetch_stop_wind() call in ride_plan_detail()

templates/
└── ride_plan_detail.html  # Add Wind column header, per-row cell, legend block

tests/
└── test_weather.py        # Add TestFetchStopWind class
```

### Pattern 1: New Service Function `fetch_stop_wind()`
**What:** Pure orchestration function — calls `get_stop_coordinates()`, fetches weather via cache, computes per-stop wind data.
**When to use:** Called once per `ride_plan_detail()` request when `weather_route_id` is available.
**Example:**
```python
# Source: derived from existing get_cached_route_weather() in services/weather.py
def fetch_stop_wind(stops, track_points, plan_slug, start_time_str, cache=None):
    """Return per-stop wind data list for display in the base ride plan table.

    stops: list of stop dicts with 'distance_miles' key
    track_points: list of RWGPS track dicts (y=lat, x=lng, d=distance_m)
    plan_slug: str — used as part of cache key
    start_time_str: str — HH:MM format, used for estimated arrival times
    cache: Flask-Caching cache object (passed for testability)

    Returns list of dicts: {
        'wind_speed_kmh': float,
        'wind_type': str,        # 'headwind'|'tailwind'|'crosswind'
        'style': dict,           # from wind_cell_style()
        'label': str,            # from wind_label()
    } in same order as stops. Returns None on any error.
    """
    if not track_points:
        return None

    # Step 1: interpolate stop coordinates
    coords = get_stop_coordinates(stops, track_points)
    valid_coords = [c for c in coords if c is not None]
    if not valid_coords:
        return None

    # Step 2: fetch forecast (cache-first, 1-hour TTL)
    start_hour = datetime.now().strftime('%Y%m%d') + start_time_str[:2]
    cache_key = f"wind:{plan_slug}:{start_hour}"
    if cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    weather_data = fetch_route_weather(valid_coords)
    if not weather_data:
        return None

    # Step 3: compute per-stop wind
    # Use bearing between consecutive stop coordinates
    result = []
    for i, coord in enumerate(coords):
        if coord is None:
            result.append(None)
            continue
        valid_i = sum(1 for c in coords[:i+1] if c is not None) - 1
        if valid_i >= len(weather_data):
            result.append(None)
            continue
        forecast = weather_data[valid_i]
        hourly = forecast.get('hourly', {})
        # Use hour index 0 as default; for Phase 3 forecast, this is acceptable
        # (Phase 3 goal is "color-coded wind column", not precise per-stop arrival time)
        wind_speed = _safe_get(hourly, 'wind_speed_10m', 0, 0.0)
        wind_dir = _safe_get(hourly, 'wind_direction_10m', 0, 0)

        # Bearing from this stop to next (or last bearing if final stop)
        bearing = 0.0
        if i + 1 < len(coords) and coords[i + 1] is not None:
            bearing = calculate_bearing(
                coord['lat'], coord['lng'],
                coords[i + 1]['lat'], coords[i + 1]['lng']
            )
        elif i > 0 and coords[i - 1] is not None:
            bearing = calculate_bearing(
                coords[i - 1]['lat'], coords[i - 1]['lng'],
                coord['lat'], coord['lng']
            )

        hw = headwind_component(wind_speed, wind_dir, bearing)
        cw = crosswind_component(wind_speed, wind_dir, bearing)
        wind_type = classify_wind(hw, cw)
        style = wind_cell_style(wind_speed, wind_type)
        result.append({
            'wind_speed_kmh': round(wind_speed, 1),
            'wind_type': wind_type,
            'style': style,
            'label': wind_label(hw),
        })

    if cache:
        cache.set(cache_key, result, timeout=3600)
    return result
```

**Note on arrival time precision:** Phase 3 success criteria does not require accurate per-stop arrival time matching (that level of detail is for the existing `format_weather_response()` which serves the chat assistant). For the wind column, using the forecast hour closest to the plan's start time plus an estimated offset is sufficient. Consider using `get_hour_index()` with a rough arrival estimate based on `stop['distance_miles'] / avg_speed_mph` if desired, but hour 0 of the daily forecast will be acceptable for an MVP column.

### Pattern 2: Route Handler Integration
**What:** In `ride_plan_detail()`, fetch RWGPS track points and call `fetch_stop_wind()` in a try/except block.
**When to use:** Whenever `weather_route_id` is available.
**Example:**
```python
# In routes/riders.py — ride_plan_detail(), after stops list is built, before render_template()
from services.weather import fetch_stop_wind
from services.rwgps import fetch_route

stop_wind = None
if weather_route_id:
    try:
        route_data = fetch_route(weather_route_id)
        track_points = route_data.get('track_points') or []
        stop_wind = fetch_stop_wind(
            stops=stops,
            track_points=track_points,
            plan_slug=plan['slug'],
            start_time_str=plan.get('start_time', '06:00'),
            cache=cache,
        )
    except Exception:
        logger.exception("Wind fetch failed for plan %s", slug)
        stop_wind = None

# Add stop_wind to render_template() call
return render_template('ride_plan_detail.html',
    ...
    stop_wind=stop_wind,
    ...
)
```

### Pattern 3: Template Wind Column (Inline Styles)
**What:** Conditional column added to the existing `plan-table`. All guarded by `{% if stop_wind %}`.
**When to use:** Only renders when `stop_wind` is a non-None list.
**Example (header and one row cell):**
```html
<!-- In <thead><tr>: add after existing columns -->
{% if stop_wind %}
<th style="text-align:center;">Wind</th>
{% endif %}

<!-- In <tbody>{% for s in stops %}<tr>: add after existing cells -->
{% if stop_wind %}
{% set w = stop_wind[loop.index0] %}
<td style="text-align:center;padding:4px 8px;">
  {% if w %}
  <span style="
    display:inline-block;
    padding:2px 6px;
    border-radius:4px;
    background:{{ w.style.background }};
    color:{{ w.style.color }};
    font-size:{{ w.style.font_size }};
    font-weight:600;
    white-space:nowrap;">
    {{ w.wind_speed_kmh }} km/h
  </span>
  {% else %}
  &mdash;
  {% endif %}
</td>
{% endif %}

<!-- In <tfoot><tr>: add an empty cell to maintain column count -->
{% if stop_wind %}<td></td>{% endif %}
```

### Pattern 4: Wind Legend Block
**What:** A small legend div below the table explaining the color coding.
**When to use:** Only renders when `stop_wind` is not None.
**Example:**
```html
{% if stop_wind %}
<div style="margin-top:12px;display:flex;gap:16px;align-items:center;flex-wrap:wrap;font-size:0.75rem;color:var(--text-light);">
  <span style="font-weight:600;color:var(--text);">Wind legend:</span>
  <span>
    <span style="display:inline-block;width:12px;height:12px;border-radius:2px;background:#16A34A;vertical-align:middle;margin-right:3px;"></span>
    Tailwind
  </span>
  <span>
    <span style="display:inline-block;width:12px;height:12px;border-radius:2px;background:#DC2626;vertical-align:middle;margin-right:3px;"></span>
    Headwind
  </span>
  <span>
    <span style="display:inline-block;width:12px;height:12px;border-radius:2px;background:#2563EB;vertical-align:middle;margin-right:3px;"></span>
    Crosswind
  </span>
</div>
{% endif %}
```

### Anti-Patterns to Avoid
- **Dynamic Tailwind classes:** Never write `class="bg-green-500"` with a Python variable interpolated in the color name. Tailwind JIT purges classes not present as complete strings at build time. Always use `style="background:{{ ... }}"`.
- **Raising in the route handler:** `fetch_stop_wind()` errors must be caught at the call site. A single uncaught exception would break the entire ride plan page. The wind column is a progressive enhancement — the page must render without it.
- **Fetching RWGPS track in the service function:** `fetch_stop_wind()` should accept pre-fetched `track_points`, not a `route_id`. This keeps the function testable without app context and avoids a hidden network call inside the service.
- **Building `stop_wind` for zero-distance start stops:** The first stop (start) has `distance_miles = 0`. `get_stop_coordinates()` clamps it to the first track point. This is correct and produces a valid coordinate — no special case needed.
- **Mismatched list lengths:** `stop_wind` must be the same length as `stops` (including `None` entries for stops where wind couldn't be computed). The template uses `loop.index0` to index `stop_wind` by position.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Wind speed → background color | Custom color interpolation | `wind_cell_style()` (already exists, tested) | Phase 1 built exactly this; reuse completely |
| Coordinate interpolation | New geo math | `get_stop_coordinates()` (already exists, tested) | Phase 2 built exactly this; reuse completely |
| Open-Meteo batch API call | New HTTP client | `fetch_route_weather()` (already exists, tested) | Single-location normalization and multi-location already handled |
| 1-hour cache | Manual dict/TTL | `cache.get()`/`cache.set()` with `timeout=3600` | Flask-Caching already initialized; existing pattern in `get_cached_route_weather()` |
| Bearing between two stops | Spherical trig | `calculate_bearing()` (already exists, tested) | Phase 1 foundation |
| Headwind/crosswind decomposition | Custom vector math | `headwind_component()` + `crosswind_component()` (already exist, tested) | Phase 1 foundation |

**Key insight:** Phases 1 and 2 deliberately built all the math primitives before any UI work. Phase 3 is almost entirely assembly — connecting existing pieces in the route handler and template.

## Common Pitfalls

### Pitfall 1: RWGPS Fetch Failure Breaks the Ride Plan Page
**What goes wrong:** `fetch_route(weather_route_id)` raises an exception (network timeout, invalid route ID, API key missing). If not caught, the entire `ride_plan_detail()` view returns a 500 error.
**Why it happens:** `fetch_route()` raises on any non-200 response (see `services/rwgps.py` lines 132-143). RWGPS is an external API with variable availability.
**How to avoid:** Wrap the entire wind fetch block in `try/except Exception` and set `stop_wind = None` on failure. Log the exception with `logger.exception()` for debugging. The page renders normally without the wind column.
**Warning signs:** 500 errors in production when RWGPS is down; wind column missing silently in staging.

### Pitfall 2: `stop_wind` Length Mismatch with `stops`
**What goes wrong:** Template uses `stop_wind[loop.index0]` but `stop_wind` has fewer entries than `stops`. Jinja2 raises `IndexError`.
**Why it happens:** `fetch_stop_wind()` returns a partial list if some coordinates are `None` or if wind data is missing for some stops.
**How to avoid:** `fetch_stop_wind()` must always return a list of exactly `len(stops)` entries, using `None` for any stop that couldn't be resolved. The template should check `{% if w %}` before rendering the cell content.
**Warning signs:** `IndexError: list index out of range` in Jinja2 template rendering.

### Pitfall 3: Cache Key Collision Between Route Weather and Stop Wind
**What goes wrong:** The existing `get_cached_route_weather()` uses key `weather:{route_slug}:{start_hour_str}`. If Phase 3 reuses the same key format or collides with it, stale weather data is returned for the wrong purpose.
**Why it happens:** Two different data shapes (route-level weather segments vs. per-stop wind) stored under similar keys.
**How to avoid:** Use distinct cache key prefix `wind:{plan_slug}:{start_hour}` — different from `weather:{route_slug}:{start_hour_str}`. The data shape (list of per-stop dicts vs. list of Open-Meteo forecast blobs) is different and must not share a key.
**Warning signs:** Template rendering errors when `stop_wind` entries have unexpected structure.

### Pitfall 4: Open-Meteo Returns Dict (Single Location) Not List
**What goes wrong:** `fetch_route_weather()` normalizes single-location responses to `[data]`. But if called with exactly one stop coordinate, the outer list has one element. Code that iterates `weather_data[i]` without checking still works — but only if the normalization step ran.
**Why it happens:** Open-Meteo returns a plain dict when given one lat/lng, a list when given multiple. `fetch_route_weather()` already handles this correctly. The pitfall is bypassing `fetch_route_weather()` and calling `requests.get()` directly.
**How to avoid:** Always go through `fetch_route_weather()` — never call the Open-Meteo URL directly in Phase 3 code.
**Warning signs:** `TypeError: 'dict' object is not subscriptable` when iterating weather data.

### Pitfall 5: Arrival Time Not Computed Per Stop
**What goes wrong:** All stops show wind for the same forecast hour (hour 0 = midnight or the first available forecast hour), rather than the hour when the rider is expected to arrive at that stop.
**Why it happens:** `fetch_stop_wind()` uses a fixed hour index instead of using `get_hour_index()` with an estimated arrival time per stop.
**How to avoid:** Phase 3 can acceptably use approximate arrival times derived from `stop['distance_miles']` divided by an average speed. The stops already have `arrival_time_min` computed in the route handler. Pass this to `fetch_stop_wind()` or compute a `start_dt` from `plan['start_time']` and today's date, then use `get_hour_index()` per stop.
**Warning signs:** All wind cells show identical values regardless of how far along the route the stop is.

### Pitfall 6: `plan-table` Column Count Mismatch in Footer
**What goes wrong:** Adding a `<th>Wind</th>` header without adding a matching `<td></td>` in the `<tfoot>` row causes the footer row to have a different column count than the header. Browsers compensate inconsistently.
**Why it happens:** The `plan-table` has an existing `<tfoot>` row with explicit `colspan` and individual cells. Adding a column to header/body without matching it in `<tfoot>` misaligns the table.
**How to avoid:** Add `{% if stop_wind %}<td></td>{% endif %}` to the `<tfoot>` row alongside the existing conditional for `plan.cutoff_hours`.
**Warning signs:** Table footer visually misaligned when wind column is shown.

## Code Examples

Verified patterns from the codebase:

### Existing Cache Pattern (1-hour TTL)
```python
# Source: services/weather.py — get_cached_route_weather(), lines 283-300
def get_cached_route_weather(route_slug, start_hour_str, sample_points, cache=None):
    cache_key = f"weather:{route_slug}:{start_hour_str}"
    if cache is not None:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    data = fetch_route_weather(sample_points)
    if cache is not None:
        cache.set(cache_key, data, timeout=3600)
    return data
```
Phase 3 follows this exact pattern with key `wind:{plan_slug}:{start_hour}`.

### Existing Route Handler Error Handling (for RWGPS)
```python
# Source: routes/riders.py — (general pattern for external API calls)
# External calls go in try/except; page renders without the optional data on failure
if weather_route_id:
    try:
        route_data = fetch_route(weather_route_id)
        # ... process
    except Exception:
        logger.exception("failed")
        # fall back to no data
```

### `fetch_route()` Return Structure (RWGPS)
```python
# Source: services/rwgps.py — fetch_route(), line 145-148
data = resp.json()
route = data.get('route', data) if isinstance(data, dict) else data
# route['track_points'] is the list of {y, x, d, e} dicts
track_points = route.get('track_points') or []
```

### Wind Cell Style Output (already Phase 1 tested)
```python
# Source: services/weather.py — wind_cell_style(), lines 107-123
# Returns: {'color': '#DC2626', 'background': 'rgba(220,38,38,0.35)', 'font_size': '0.875rem'}
style = wind_cell_style(10, 'headwind')
```

### Stops Structure in ride_plan_detail() (routes/riders.py)
```python
# Source: routes/riders.py lines 1218-1278
# Each stop dict has: distance_miles, stop_type, stop_name, arrival_time_min, ...
# Relevant fields for wind: distance_miles (float), arrival_time_min (int minutes)
```

### Conditional Column Pattern (Jinja2, existing usage)
```html
<!-- Source: templates/ride_plan_detail.html line 1470 -->
{% if plan.cutoff_hours %}<th style="text-align:right;">Time Bank</th>{% endif %}
<!-- Phase 3 follows this exact guard pattern for Wind column -->
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| No wind in ride plan table | Wind column with inline color-coded cells | Phase 3 | Riders see directional wind at each control point |
| Weather accessed via external button link only | Inline server-side wind data in the control table | Phase 3 | Wind visible without leaving the page |
| Tailwind CSS dynamic classes (impossible) | Inline `style=` attributes with rgba colors | Phase 1 decision | Correct rendering with Flask/Jinja2 — no build step needed |

**Deprecated/outdated:**
- None: Phase 3 adds new capability without replacing any existing feature.

## Open Questions

1. **Should `fetch_stop_wind()` use arrival-time-accurate forecast hours or a fixed start hour?**
   - What we know: `get_hour_index()` exists and takes a `datetime`. The route handler already computes `arrival_time_min` per stop. The plan has a `start_time` field (HH:MM string).
   - What's unclear: Whether the added complexity of per-stop arrival time estimation is worth it for Phase 3 (which is about visual color coding, not precise numeric forecasts).
   - Recommendation: Implement arrival-time-aware fetching — use `plan['start_time']` + today's date as `start_dt`, add `stop['arrival_time_min']` as a timedelta offset, call `get_hour_index()`. This reuses existing helpers and produces more accurate colors without much extra complexity.

2. **Should the wind column appear in both "cards" and "table" views, or only in the table?**
   - What we know: `ride_plan_detail.html` has two view modes: `cards-view` (timeline cards) and `table-view` (the `plan-table`). The success criteria says "base ride plan page shows a Wind column alongside existing stop columns" — the word "column" suggests table view.
   - What's unclear: Whether adding wind to the card view is in scope.
   - Recommendation: Phase 3 adds the wind column to the **table view only**. The cards view is more complex (inline cards, break chip merging) and is not required by the success criteria. Phase 5 (Custom Plan) can revisit if needed.

3. **Is `weather_route_id` always available for plans with RWGPS routes?**
   - What we know: `weather_route_id = _extract_rwgps_route_id(plan.get('rwgps_url_team')) if plan.get('rwgps_url_team') else rwgps_route_id`. Both `rwgps_url_team` and `rwgps_url` may be `None` for plans not yet linked to a route.
   - What's unclear: What fraction of plans lack RWGPS routes.
   - Recommendation: The `{% if stop_wind %}` guard handles this — if `weather_route_id` is `None`, skip the fetch and pass `stop_wind=None`. The success criterion "wind column is absent when wind data is unavailable" covers this case.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing) |
| Config file | `pytest.ini` (project root) |
| Quick run command | `python3 -m pytest tests/test_weather.py -x -q` |
| Full suite command | `python3 -m pytest tests/ -x -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| WIND-06 | `fetch_stop_wind()` returns list of per-stop wind dicts with correct structure | unit | `python3 -m pytest tests/test_weather.py::TestFetchStopWind -x` | Wave 0 |
| WIND-06 | `fetch_stop_wind()` returns cached result on second call (no new API call) | unit | `python3 -m pytest tests/test_weather.py::TestFetchStopWind::test_cache_hit -x` | Wave 0 |
| WIND-06 | `fetch_stop_wind()` returns None when track_points is empty | unit | `python3 -m pytest tests/test_weather.py::TestFetchStopWind::test_empty_track_returns_none -x` | Wave 0 |
| WIND-06 | `fetch_stop_wind()` returns None when Open-Meteo API raises | unit | `python3 -m pytest tests/test_weather.py::TestFetchStopWind::test_api_error_returns_none -x` | Wave 0 |
| BPLN-01 | `stop_wind` is passed to template and wind column header renders | integration | manual / route-level test | Wave 0 |
| BPLN-02 | Wind cell background contains correct rgba color for wind type | unit | `python3 -m pytest tests/test_weather.py::TestWindCellStyle -x` | ✅ exists |
| BPLN-03 | Wind cell background opacity varies by speed band | unit | `python3 -m pytest tests/test_weather.py::TestWindCellStyle -x` | ✅ exists |
| BPLN-04 | Wind cell font size varies by speed band | unit | `python3 -m pytest tests/test_weather.py::TestWindCellStyle -x` | ✅ exists |
| BPLN-05 | Template renders without wind column when `stop_wind=None` | integration | manual smoke test / screenshot | manual only |
| BPLN-06 | Wind legend block appears when `stop_wind` is not None | integration | manual smoke test | manual only |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/test_weather.py -x -q`
- **Per wave merge:** `python3 -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_weather.py::TestFetchStopWind` — covers WIND-06 (class does not yet exist; add to existing file)

*(No new files needed — BPLN-02/03/04 already covered by `TestWindCellStyle` from Phase 1)*

## Sources

### Primary (HIGH confidence)
- `services/weather.py` (project codebase) — `fetch_route_weather()`, `get_cached_route_weather()`, `get_stop_coordinates()`, `wind_cell_style()`, `headwind_component()`, `crosswind_component()`, `classify_wind()`, `calculate_bearing()`, `wind_label()`, `_safe_get()`, `get_hour_index()` — all existing, tested functions that Phase 3 reuses
- `routes/riders.py` (project codebase) — `ride_plan_detail()` route handler structure, existing `render_template()` call, `weather_route_id` extraction, `stops` list shape
- `templates/ride_plan_detail.html` (project codebase) — `plan-table` structure (lines 1458–1559), existing conditional column pattern (`{% if plan.cutoff_hours %}`), inline style conventions
- `cache.py` (project codebase) — `cache` object initialization, `timeout=3600` pattern
- `.planning/STATE.md` — locked decisions: "Inline styles for wind cell colors — Tailwind JIT static purging makes dynamic classes impossible", "No new DB tables for forecast data — cache in Flask-Caching (1-hour TTL)"
- `.planning/REQUIREMENTS.md` — BPLN-01 through BPLN-06 specs

### Secondary (MEDIUM confidence)
- `tests/test_weather.py` (project codebase) — confirms `TestWindCellStyle` already covers BPLN-02/03/04; confirms mock pattern for Open-Meteo (`patch('services.weather.requests.get')`)
- `services/rwgps.py` (project codebase) — `fetch_route()` return structure, `track_points` field name

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new libraries; all needed functions already exist and are tested
- Architecture: HIGH — route handler structure, template patterns, and cache strategy all directly observable from codebase
- Pitfalls: HIGH — error handling patterns, column count issues, and cache key design all identified from direct code inspection

**Research date:** 2026-03-23
**Valid until:** 30 days (stable — depends only on project code conventions and Open-Meteo API shape, neither of which changes rapidly)
