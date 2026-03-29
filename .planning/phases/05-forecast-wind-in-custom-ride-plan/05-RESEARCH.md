# Phase 5: Forecast Wind in Custom Ride Plan - Research

**Researched:** 2026-03-23
**Domain:** Flask route handler + Jinja2 template — extending existing wind pipeline to custom ride plan views
**Confidence:** HIGH (all findings are from direct codebase inspection)

## Summary

Phase 5 is a narrow wiring task, not a new algorithm task. The wind computation pipeline (`fetch_stop_wind`) is already complete from Phase 3. The custom ride plan view (`custom_ride_plan_view`) already resolves `weather_route_id` using the same logic as the base plan view — but it never calls `fetch_stop_wind` and never passes `stop_wind` to the template. The template (`ride_plan_detail.html`) already renders wind cells when `stop_wind` is present. The gap is a missing 10-line block in the route handler.

The secondary requirement (WIND-09) is about normalizing archive API responses. The archive API endpoint does not exist yet in `services/weather.py`. However, the *forecast* API normalization is already in `fetch_route_weather` (wraps a single-dict response into a list). For Phase 5 specifically, WIND-09 manifests as: if a future archive API function is called with one stop, it may return a raw dict. The fix is ensuring any function that calls Open-Meteo applies the same `isinstance(data, dict)` normalization guard that `fetch_route_weather` uses. Since no archive API function exists yet, the WIND-09 work in Phase 5 is likely a unit test that validates the existing normalization guard, or — if a new `fetch_archive_wind` function is added here — ensuring it has the same guard.

The hidden-stop concern (success criterion 3) is already handled structurally: `get_merged_plan_stops` in `custom_plan_service.py` filters out hidden stops before returning the merged list. So `stops` passed to `fetch_stop_wind` will never contain a hidden stop. The `stop_wind` list returned by `fetch_stop_wind` is always the same length as `stops`. The template uses `loop.index0` to index into `stop_wind`, so alignment is automatic as long as the list is built from the same `stops` list. No special hidden-stop handling is needed in Phase 5.

**Primary recommendation:** Add the `fetch_stop_wind` call and `stop_wind` template variable to `custom_ride_plan_view` in `routes/riders.py`, following the pattern already established in the base plan view. Add unit tests for WIND-09 normalization behavior.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| WIND-09 | System normalizes single-location (dict) and multi-location (list) archive API responses identically to existing forecast normalization | `fetch_route_weather` already has the normalization guard (`isinstance(data, dict) -> [data]`). WIND-09 requires verifying this pattern also applies when a future archive endpoint is introduced. In Phase 5, this means a unit test that documents the normalization contract. |
| CPLN-01 | User sees wind columns in custom ride plan view with same color coding as base plan | `custom_ride_plan_view` already computes `weather_route_id` but never calls `fetch_stop_wind`. Adding the call + passing `stop_wind` to `render_template` is the only change needed. The template already renders wind cells conditionally on `stop_wind`. |
| CPLN-02 | Custom stop positions correctly interpolated on route (including rider-added stops and hidden stops) | `get_merged_plan_stops` already builds a flat list of visible stops (hidden stops excluded). `fetch_stop_wind` already interpolates any list of stops with `distance_miles`. Rider-added custom stops have `distance_miles` set. No new interpolation logic is needed. |
</phase_requirements>

## Standard Stack

### Core (no new dependencies required)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Flask | Existing | Route handler for `custom_ride_plan_view` | Project stack |
| `services.weather.fetch_stop_wind` | Phase 3 | Wind data pipeline | Already implemented, tested, cached |
| `services.rwgps.fetch_route` | Existing | Fetches RWGPS track points | Already used in base plan view |
| `cache` (Flask-Caching) | Existing | 1-hour wind cache, keyed `wind:{plan_slug}:{YYYYMMDD}{HH}` | Same cache object used in base plan view |
| `ride_plan_detail.html` | Existing | Shared template for base and custom views, already has wind column logic | Already used by `custom_ride_plan_view` via `is_custom_view=True` |

**Installation:** None required.

## Architecture Patterns

### How `custom_ride_plan_view` differs from `ride_plan_detail_view`

The base plan view (`ride_plan_detail_view`) includes this block (lines 1398-1413 in `routes/riders.py`):

```python
stop_wind = None
if weather_route_id:
    try:
        route_data = fetch_route(weather_route_id)
        track_points = route_data.get('track_points') or []
        stop_wind = fetch_stop_wind(
            stops=stops,
            track_points=track_points,
            plan_slug=plan['slug'],
            start_time_str=str(plan.get('start_time') or '07:00')[:5],
            cache=cache,
        )
    except Exception:
        current_app.logger.exception("Wind fetch failed for plan %s", slug)
        stop_wind = None
```

`custom_ride_plan_view` (lines 1506-1701) already computes `weather_route_id` using the same logic (line 1550), builds a `stops` list, but **does not** have this block, and **does not** pass `stop_wind` to `render_template`.

The fix: insert the same `stop_wind` block in `custom_ride_plan_view` immediately before the `render_template` call, and add `stop_wind=stop_wind` (or `stop_wind=None`) to the `render_template` kwargs.

### Pattern: Wind Block Placement in Route Handler

Insert after the `stops, use_timeline = _attach_break_metadata(stops)` call, before `rusa_events` loop, mirroring the base plan view.

```python
# Wind data for table view (same pattern as ride_plan_detail_view)
stop_wind = None
if weather_route_id:
    try:
        route_data = fetch_route(weather_route_id)
        track_points = route_data.get('track_points') or []
        stop_wind = fetch_stop_wind(
            stops=stops,
            track_points=track_points,
            plan_slug=plan['slug'],
            start_time_str=str(plan.get('start_time') or '07:00')[:5],
            cache=cache,
        )
    except Exception:
        current_app.logger.exception("Wind fetch failed for custom plan %s", slug)
        stop_wind = None
```

Then add to `render_template` call:
```python
stop_wind=stop_wind,
```

### Pattern: Template Wind Rendering (already complete)

The shared `ride_plan_detail.html` template already handles wind display:

- Line 1470: `{% if stop_wind %}<th style="text-align:center;">Wind</th>{% endif %}` — header column
- Lines 1517-1528: `{% if stop_wind %}{% set w = stop_wind[loop.index0] %}...{% endif %}` — per-row cell using `loop.index0` alignment
- Lines 1574-end: Wind legend rendered when `stop_wind` is truthy

The template renders nothing when `stop_wind=None`, ensuring graceful degradation.

### Pattern: Hidden Stop Exclusion (already handled)

`get_merged_plan_stops` in `custom_plan_service.py` (lines 53-55) already skips hidden stops:

```python
if override and override.get('is_hidden'):
    accumulated_time_from_removed += base_stop.get('segment_time_min') or 0
    continue
```

The `stops` list passed to `fetch_stop_wind` will never contain a hidden stop. The returned `stop_wind` list is the same length as `stops`. No template-level skipping of hidden stops is needed.

### Pattern: Custom Stop Interpolation (already handled)

Custom stops injected by the rider have `distance_miles` set (line 119-124 of `custom_plan_service.py`). The `get_stop_coordinates` function interpolates any stop with a `distance_miles` value. No special-casing of custom stops is needed.

### Pattern: WIND-09 Normalization Guard

The existing `fetch_route_weather` function already normalizes single-dict responses:

```python
# Source: services/weather.py lines 273-278
data = resp.json()
# Normalize: single-location returns dict, multi returns list
if isinstance(data, dict):
    return [data]
return data
```

Any future archive fetch function must apply the same guard. A unit test should document this contract.

### Anti-Patterns to Avoid

- **Duplicating the stops loop:** `custom_ride_plan_view` already builds a processed `stops` list with `arrival_time_min` and `distance_miles`. Do NOT pass `custom_stops_raw` directly to `fetch_stop_wind`; pass the fully processed `stops` list.
- **Using a different cache key:** The cache key format `wind:{plan_slug}:{YYYYMMDD}{HH}` is shared between base and custom plan views for the same route — they use the same `plan['slug']` and the same `start_time`. This is correct and desirable (cache hit from either view).
- **Passing `stop_wind` without the variable in `render_template`:** The template will raise `UndefinedError` in strict Jinja2 mode. Always pass `stop_wind=stop_wind` even if it is `None`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Per-stop wind computation | Custom wind calculation in `custom_ride_plan_view` | `fetch_stop_wind()` from `services/weather.py` | Already implements caching, bearing calculation, API batching, error handling |
| Track point interpolation | Custom interpolation in route handler | `get_stop_coordinates()` inside `fetch_stop_wind` | Already handles edge cases: clamping, zero-length segments, None coordinates |
| Template wind rendering | New template for custom plan wind | Existing `ride_plan_detail.html` with `stop_wind` variable | Template already conditionally renders wind column and legend |
| API response normalization | Inline `isinstance` check in route handler | Same guard pattern as `fetch_route_weather` | Tested, documented, consistent |

**Key insight:** Every piece of infrastructure needed for Phase 5 already exists. The work is connection, not construction.

## Common Pitfalls

### Pitfall 1: Passing wrong stops list to `fetch_stop_wind`
**What goes wrong:** Passing `custom_stops_raw` (unprocessed list from `get_merged_plan_stops`) instead of the processed `stops` list. `custom_stops_raw` may have Decimal types for `distance_miles` and no `arrival_time_min` key.
**Why it happens:** `get_merged_plan_stops` returns the raw merged list before the type-conversion loop in `custom_ride_plan_view`.
**How to avoid:** Call `fetch_stop_wind(stops=stops, ...)` where `stops` is the list built in the processing loop (after `_attach_break_metadata`).
**Warning signs:** `TypeError: unsupported operand type(s) for *: 'decimal.Decimal' and 'float'` inside `get_stop_coordinates`.

### Pitfall 2: Forgetting `stop_wind` in `render_template` kwargs
**What goes wrong:** Template raises `jinja2.exceptions.UndefinedError: 'stop_wind' is undefined` in strict mode, or silently renders with no wind column.
**Why it happens:** The base plan view passes `stop_wind=stop_wind`; easy to miss when copying the render_template pattern.
**How to avoid:** Compare the custom plan `render_template` kwargs against the base plan kwargs list. The base plan (lines 1415-1439) includes `stop_wind=stop_wind`.
**Warning signs:** Wind column absent in custom plan view with no error, or template error in testing.

### Pitfall 3: WIND-09 TypeError on single-location archive response
**What goes wrong:** A future `fetch_archive_wind` function returns a raw dict for one location. Code that expects a list will raise `TypeError: 'dict' object is not subscriptable` when it does `data[0]`.
**Why it happens:** Open-Meteo returns a bare JSON dict (not a list) when only one coordinate is requested. This is documented by the existing `test_wraps_single_location_response` test.
**How to avoid:** Any function calling Open-Meteo batch API must apply `if isinstance(data, dict): return [data]`.
**Warning signs:** Phase 6 tests failing when archive API is called for a route with one stop.

### Pitfall 4: Cache collision between base and custom plan wind
**What goes wrong:** Not a bug — this is actually correct behavior. Both views share the same cache key `wind:{plan_slug}:{YYYYMMDD}{HH}` because they use the same base stop list for the forecast query. Custom stops may produce slightly different results since the stop list differs.
**Why it happens:** `plan['slug']` is the same for both views.
**How to avoid:** Accept the shared cache as a minor approximation (custom stops are rare). If perfect per-stop accuracy for custom stops is required, a distinct cache key like `wind:custom:{custom_plan_id}:{YYYYMMDD}{HH}` can be introduced. For Phase 5, the base key is sufficient.
**Warning signs:** Custom rider-added stops showing wind data from the base plan's stop list — acceptable approximation for v1.

## Code Examples

### Complete wind block for `custom_ride_plan_view`

```python
# Source: Pattern from ride_plan_detail_view (routes/riders.py lines 1398-1413)
# Insert after _attach_break_metadata, before rusa_events loop

stop_wind = None
if weather_route_id:
    try:
        route_data = fetch_route(weather_route_id)
        track_points = route_data.get('track_points') or []
        stop_wind = fetch_stop_wind(
            stops=stops,
            track_points=track_points,
            plan_slug=plan['slug'],
            start_time_str=str(plan.get('start_time') or '07:00')[:5],
            cache=cache,
        )
    except Exception:
        current_app.logger.exception("Wind fetch failed for custom plan %s", slug)
        stop_wind = None
```

### WIND-09: Archive response normalization guard

```python
# Source: services/weather.py lines 273-278 (fetch_route_weather)
data = resp.json()
# Normalize: single-location returns dict, multi returns list
if isinstance(data, dict):
    return [data]
return data
```

Any future `fetch_archive_weather` function must apply the same pattern.

### Template: wind column rendering (existing — no changes needed)

```jinja2
{# Source: templates/ride_plan_detail.html lines 1517-1528 #}
{% if stop_wind %}
{% set w = stop_wind[loop.index0] %}
<td style="text-align:center;padding:4px 8px;">
  {% if w %}
  <span style="display:inline-block;padding:2px 6px;border-radius:4px;
    background:{{ w.style.background }};color:{{ w.style.color }};
    font-size:{{ w.style.font_size }};font-weight:600;white-space:nowrap;">
    {{ w.wind_speed_kmh }} km/h
  </span>
  {% else %}
  &mdash;
  {% endif %}
</td>
{% endif %}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| No wind in custom plan | Wind pipeline exists but not wired to custom plan view | Phase 3 delivered the pipeline | Phase 5 is purely a wiring task |
| Inline dynamic Tailwind classes for wind colors | Inline styles (e.g., `background:rgba(...)`) | Phase 3 decision | Tailwind JIT purging makes dynamic class generation impossible; inline styles are the correct approach |

**Decisions from STATE.md that apply to Phase 5:**
- "Inline styles for wind cell colors — Tailwind JIT static purging makes dynamic classes impossible"
- "`stop_wind` passed as `None` when `weather_route_id` absent — all wind markup gated on `{% if stop_wind %}` for graceful degradation"
- "`current_app.logger.exception` used in route handler (not `app.logger`) — consistent with Flask proxy pattern"

## Open Questions

1. **Cache key for custom stops with rider-added stops**
   - What we know: the current cache key is `wind:{plan_slug}:{YYYYMMDD}{HH}`, shared between base and custom views
   - What's unclear: if a rider adds a custom stop at a location not in the base plan, the cached result from the base plan view will be reused, and that custom stop's wind data will be missing (None entry)
   - Recommendation: For v1, accept this. The REQUIREMENTS.md success criterion says "Wind data is present for rider-added stops" — this may require a custom-plan-specific cache key. Evaluate during planning.

2. **`plan.get('start_time')` in custom plan view**
   - What we know: `custom_ride_plan_view` sets `plan['start_time'] = plan.get('start_time') or '06:00'` at line 1544
   - What's unclear: the `custom_plan_data` may have its own `start_time` override
   - Recommendation: Use `plan.get('start_time')` as-is (same as base plan view). The custom plan inherits the base plan's start time.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (existing, `pytest.ini` at project root) |
| Config file | `/pytest.ini` |
| Quick run command | `python3 -m pytest tests/test_weather.py -x -q` |
| Full suite command | `python3 -m pytest tests/ -x -q` |

### Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CPLN-01 | `custom_ride_plan_view` passes `stop_wind` to template | unit (route handler) | `python3 -m pytest tests/test_weather.py -x -q -k "custom"` | ❌ Wave 0 |
| CPLN-02 | Custom and rider-added stops are included in wind fetch; hidden stops are absent | unit | `python3 -m pytest tests/test_weather.py -x -q -k "custom_stops"` | ❌ Wave 0 |
| WIND-09 | Single-dict archive response is normalized to list before indexing | unit | `python3 -m pytest tests/test_weather.py -x -q -k "normalize"` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `python3 -m pytest tests/test_weather.py -x -q`
- **Per wave merge:** `python3 -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_weather.py` — new test class `TestCustomPlanWind` covering CPLN-01, CPLN-02, WIND-09 normalization
  - No new test file needed; extend existing `tests/test_weather.py`

*(Existing test infrastructure covers all other requirements; only new test class needed)*

## Sources

### Primary (HIGH confidence)

- Direct codebase inspection — `routes/riders.py` `custom_ride_plan_view` (lines 1506-1701)
- Direct codebase inspection — `routes/riders.py` `ride_plan_detail_view` wind block (lines 1398-1413)
- Direct codebase inspection — `services/weather.py` `fetch_stop_wind` (lines 305-423)
- Direct codebase inspection — `services/weather.py` `fetch_route_weather` normalization (lines 273-278)
- Direct codebase inspection — `services/custom_plan_service.py` `get_merged_plan_stops` hidden stop handling (lines 53-55)
- Direct codebase inspection — `templates/ride_plan_detail.html` wind column rendering (lines 1470, 1517-1528, 1574+)
- Direct codebase inspection — `.planning/STATE.md` decisions table (inline styles, stop_wind=None, current_app.logger)
- Direct codebase inspection — `.planning/REQUIREMENTS.md` WIND-09, CPLN-01, CPLN-02

### Secondary (MEDIUM confidence)

- None required — all findings are from direct source inspection

### Tertiary (LOW confidence)

- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries are already in use; no new dependencies
- Architecture: HIGH — pattern copied directly from working base plan view code
- Pitfalls: HIGH — derived from reading actual implementation code, not speculation

**Research date:** 2026-03-23
**Valid until:** 2026-06-23 (stable internal codebase; no external API changes required)
