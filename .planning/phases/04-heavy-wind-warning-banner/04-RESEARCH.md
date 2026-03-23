# Phase 4: Heavy Wind Warning Banner - Research

**Researched:** 2026-03-23
**Domain:** Flask route extension + service function + Jinja2 template banner
**Confidence:** HIGH — based on direct codebase inspection of Phases 1-3 output

## Summary

Phase 4 adds a "Heavy Winds" warning banner to the existing upcoming brevets page
(`/riders/<season_name>/upcoming`). The full wind pipeline — coordinate interpolation,
Open-Meteo API call, headwind classification — was proven end-to-end in Phase 3. Phase 4
reuses that pipeline wholesale; the only new work is:

1. A `detect_heavy_wind(stop_wind)` pure function in `services/weather.py` that evaluates
   the per-stop wind list from `fetch_stop_wind()` and returns a summary dict (or None) if
   thresholds are exceeded.
2. A loop in the `upcoming_brevets()` route handler (`routes/riders.py`) that runs
   `fetch_stop_wind()` + `detect_heavy_wind()` for each event in the next 28 days that has
   a linked ride plan with a RWGPS route.
3. A conditional banner block at the top of `templates/upcoming_brevets.html` that renders
   when the template receives `wind_warnings` — a list of warning dicts (one per affected
   ride).

The thresholds are already defined as named constants (`HEAVY_WIND_MAX_KMH = 30`,
`HEAVY_WIND_AVG_HEADWIND_KMH = 15`) in `services/weather.py` from Phase 1 (WIND-10). No
new constants, no new DB tables, no schema changes.

**Primary recommendation:** Add `detect_heavy_wind()` to `services/weather.py`, extend
`upcoming_brevets()` to build a `wind_warnings` list, and add a conditional banner block
immediately after the `{% block content %}` opening in `upcoming_brevets.html`.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| WARN-01 | User sees "Heavy Winds" warning banner at top of upcoming brevets page when winds are significant | Template conditional block in `upcoming_brevets.html`; `wind_warnings` list passed from route |
| WARN-02 | Wind warnings only calculated for rides in the next 28 days with linked ride plans | Route handler filters: `event['date'] <= today + 28 days` and `event['plan_slug'] is not None` (both already available on event dicts) |
| WARN-03 | Warning triggers when max wind speed > 30 km/h OR average headwind > 15 km/h | `detect_heavy_wind()` function using existing `HEAVY_WIND_MAX_KMH` and `HEAVY_WIND_AVG_HEADWIND_KMH` constants |
| WARN-04 | Warning shows ride name, date, and wind description (e.g., "Strong headwinds expected — avg 18 km/h headwind, gusts to 35 km/h") | `detect_heavy_wind()` returns a dict with `ride_name`, `ride_date`, `description` keys; banner renders these |
</phase_requirements>

## Standard Stack

### Core — Already in Place (No New Dependencies)

| Component | Version/Location | Purpose |
|-----------|-----------------|---------|
| `services/weather.py` | Already exists, Phase 1-3 built | Add `detect_heavy_wind()` here |
| `fetch_stop_wind()` | `services/weather.py` | Reuse as-is; already returns per-stop wind list |
| `HEAVY_WIND_MAX_KMH = 30` | `services/weather.py` line 16 | Threshold constant — import, never redefine |
| `HEAVY_WIND_AVG_HEADWIND_KMH = 15` | `services/weather.py` line 17 | Threshold constant — import, never redefine |
| `fetch_route()` | `services/rwgps.py` | Fetch track points for a RWGPS route ID |
| `get_ride_plan_stops()` | `models.py` | Fetch stops with `distance_miles` for a plan |
| `upcoming_brevets()` | `routes/riders.py` lines 383-481 | Route to extend |
| `upcoming_brevets.html` | `templates/upcoming_brevets.html` | Template to extend |
| Flask-Caching `cache` | `cache.py` | Pass to `fetch_stop_wind()` for 1-hour TTL |

**No new pip packages required.** No schema changes.

## Architecture Patterns

### Recommended File Changes

```
services/
└── weather.py          # Add detect_heavy_wind() — new pure function

routes/
└── riders.py           # Extend upcoming_brevets() — build wind_warnings list

templates/
└── upcoming_brevets.html  # Add conditional banner block at top of {% block content %}
```

### Pattern 1: detect_heavy_wind() — Pure Function

**What:** Takes the return value of `fetch_stop_wind()` (a list of per-stop wind dicts, or
None), evaluates against thresholds, and returns a summary dict if heavy wind is detected or
None if not.

**Input:** `stop_wind` — the exact return type of `fetch_stop_wind()`:
```python
# Each element (or None for stops without coordinates):
{'wind_speed_kmh': float, 'wind_type': str, 'style': dict, 'label': str}
```
The function also needs `headwind_kmh` values. Looking at `fetch_stop_wind()` output, it
does NOT include `headwind_kmh` directly — only `wind_type`, `wind_speed_kmh`, `style`, and
`label`. **This is an important gap:** to compute `avg_headwind_kmh`, `detect_heavy_wind()`
needs headwind values.

**Two options to address this:**
1. Augment `fetch_stop_wind()` output to include `headwind_kmh` per stop (the cleaner
   approach — it already computes `hw` internally at line 406 but doesn't return it).
2. Pass `headwind_kmh` separately. Option 1 is better: add `headwind_kmh` to the returned
   dict in `fetch_stop_wind()` alongside the existing keys.

**When to use:** Called in `upcoming_brevets()` for each qualifying event after calling
`fetch_stop_wind()`.

**Signature:**
```python
def detect_heavy_wind(stop_wind):
    """Evaluate per-stop wind data against heavy wind thresholds.

    stop_wind: return value of fetch_stop_wind() — list of dicts or None
    Each dict must include 'wind_speed_kmh' and 'headwind_kmh' keys.

    Returns dict if heavy wind detected:
        {
            'max_wind_kmh': float,
            'avg_headwind_kmh': float,
            'is_heavy': True,
            'description': str,  # e.g. "Strong headwinds — avg 18 km/h headwind, gusts to 35 km/h"
        }
    Returns None if no heavy wind or no data.
    """
    if not stop_wind:
        return None

    valid = [s for s in stop_wind if s is not None]
    if not valid:
        return None

    max_wind = max(s['wind_speed_kmh'] for s in valid)
    headwinds = [s['headwind_kmh'] for s in valid]
    avg_headwind = sum(headwinds) / len(headwinds)

    if max_wind > HEAVY_WIND_MAX_KMH or avg_headwind > HEAVY_WIND_AVG_HEADWIND_KMH:
        description = _build_wind_description(max_wind, avg_headwind)
        return {
            'max_wind_kmh': round(max_wind, 1),
            'avg_headwind_kmh': round(avg_headwind, 1),
            'is_heavy': True,
            'description': description,
        }
    return None
```

### Pattern 2: Route Handler Extension

**What:** Add a wind-warning loop inside `upcoming_brevets()` that iterates over events
within the next 28 days that have a linked ride plan, calls the wind pipeline, and builds a
`wind_warnings` list to pass to the template.

**Key implementation details from the existing route:**

- Events are already fetched via `get_upcoming_rusa_events()` — returns dicts with `date`,
  `plan_slug`, `plan_rwgps_url_team`, `route_name`, `distance_km`, `date_str`.
- The route already has `plans = get_all_ride_plans()` and `cache` imported from
  `cache.py`.
- The 28-day cutoff needs `from datetime import date, timedelta` — already imported at
  line 50.
- `get_ride_plan_stops(plan_id)` requires `plan_id`, not `plan_slug`. The route already
  builds `plan_slug_to_id` dict at line 437 — reuse this.
- `weather_route_id` for each event comes from `plan_rwgps_url_team` (preferred) or the
  event's own RWGPS URL — same logic as `ride_plan_detail()` at line 1211.
- Wrap each wind fetch in `try/except` — if any event fails, skip it silently and continue.
- The warning loop must run AFTER `_match_plans_to_events()` populates `plan_slug` on
  events (already at line 405).

**Start time for forecast:** Each event has a `start_time` field on the `ride` row.
`get_all_upcoming_events()` SELECTs `ri.*` so `start_time` is available on each event dict.
Fall back to `'07:00'` if absent (same pattern as `ride_plan_detail()` line 1203).

**Cache considerations:** `fetch_stop_wind()` accepts `cache` — pass it. The cache key
includes `plan_slug` + date+hour, so repeated page loads won't re-fetch. Brevets page is
also cached with `@cache.cached(timeout=CACHE_TIMEOUT)` — however, the `upcoming_brevets`
route does NOT use `@cache.cached` (unlike `upcoming()` in `main.py`). This is correct: the
brevets page is user-session-aware (login state, custom plans) so it cannot be globally
cached. Wind fetch results are cached inside `fetch_stop_wind()` itself.

**Pseudocode for the wind loop:**
```python
cutoff = date.today() + timedelta(days=28)
wind_warnings = []
plan_slug_to_id = {plan['slug']: plan['id'] for plan in plans}

for event in rusa_events:
    event_date = event.get('date')
    if not event_date or event_date > cutoff:
        continue
    plan_slug = event.get('plan_slug')
    if not plan_slug:
        continue

    plan_id = plan_slug_to_id.get(plan_slug)
    if not plan_id:
        continue

    # Resolve weather RWGPS route ID (prefer team route)
    weather_rwgps_url = event.get('plan_rwgps_url_team') or event.get('rwgps_url')
    if not weather_rwgps_url:
        continue
    weather_route_id = _extract_rwgps_route_id(weather_rwgps_url)
    if not weather_route_id:
        continue

    try:
        plan_stops = get_ride_plan_stops(plan_id)
        route_data = fetch_route(weather_route_id)
        track_points = route_data.get('track_points') or []
        stop_wind = fetch_stop_wind(
            stops=plan_stops,
            track_points=track_points,
            plan_slug=plan_slug,
            start_time_str=str(event.get('start_time') or '07:00'),
            cache=cache,
        )
        warning = detect_heavy_wind(stop_wind)
        if warning:
            warning['ride_name'] = event.get('route_name') or event.get('name', '')
            warning['ride_date'] = event.get('date_str', str(event_date))
            wind_warnings.append(warning)
    except Exception:
        current_app.logger.exception("Wind warning check failed for event %s", event.get('id'))
        continue
```

### Pattern 3: Template Banner

**What:** A conditional block at the top of `{% block content %}` in
`upcoming_brevets.html`, immediately before the hero section (or immediately after it,
before the filter section). The banner uses inline styles consistent with existing UI
(warning yellow/orange color scheme).

**Placement:** After the `<div class="hero">` block and before `<div class="container section">`. This makes the banner prominent without disrupting the filter controls.

**Template example:**
```html
{% if wind_warnings %}
<div style="background:linear-gradient(135deg,#fef3c7,#fde68a);border:2px solid #f59e0b;
            border-radius:12px;padding:16px 20px;margin:0 auto 24px;max-width:1200px;
            box-shadow:0 4px 12px rgba(245,158,11,0.2);">
  <div style="font-weight:800;color:#92400e;font-size:1.05rem;margin-bottom:10px;">
    &#9888;&#65039; Heavy Winds Forecast
  </div>
  {% for w in wind_warnings %}
  <div style="margin-bottom:8px;color:#78350f;font-size:0.9rem;">
    <strong>{{ w.ride_name }}</strong> &mdash; {{ w.ride_date }}:
    {{ w.description }}
  </div>
  {% endfor %}
</div>
{% endif %}
```

**Graceful degradation:** When `wind_warnings` is empty or absent (falsy), the banner block
renders nothing. The page must work identically with `wind_warnings=[]` or
`wind_warnings` not in context.

### Anti-Patterns to Avoid

- **Running wind fetch for all events regardless of date:** Only fetch for events within 28
  days. Open-Meteo forecast API covers 16 days max — events beyond 28 days have no forecast
  data.
- **Running wind fetch for events without plan_slug:** Events without a linked ride plan have
  no stops, so interpolation fails. Skip them explicitly before any API call.
- **Putting detect_heavy_wind() logic inline in the route:** Wind classification belongs in
  `services/weather.py`. Routes are thin orchestrators.
- **Using `@cache.cached` on the outer route for wind results:** The brevets route is
  user-session-aware and already intentionally uncached. Wind results are cached inside
  `fetch_stop_wind()`.
- **Dynamic Tailwind classes for banner colors:** Use inline styles as the banner color is
  fixed (warning yellow) and does not need dynamic intensity.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead |
|---------|-------------|-------------|
| Per-stop wind fetch | Custom API loop | `fetch_stop_wind()` — already built in Phase 3 |
| Threshold constants | Hardcoded literals | `HEAVY_WIND_MAX_KMH`, `HEAVY_WIND_AVG_HEADWIND_KMH` from `services/weather.py` |
| Stop coordinate resolution | Custom interpolation | `get_stop_coordinates()` called inside `fetch_stop_wind()` — already handled |
| RWGPS route ID extraction | Custom URL parsing | `_extract_rwgps_route_id()` in `routes/riders.py` — already exists |
| Cache key management | Manual key construction | `fetch_stop_wind(cache=cache)` handles it |

## Common Pitfalls

### Pitfall 1: fetch_stop_wind() Does Not Return headwind_kmh

**What goes wrong:** `detect_heavy_wind()` needs `headwind_kmh` per stop to compute average
headwind. But the current `fetch_stop_wind()` return dict (lines 411-416) includes only
`wind_speed_kmh`, `wind_type`, `style`, `label` — not `headwind_kmh`.

**Why it happens:** Phase 3 only needed display data (color, label) not raw component
values.

**How to avoid:** Add `'headwind_kmh': round(float(hw), 1)` to the result dict in
`fetch_stop_wind()` (at the `result.append({...})` block, line 411). This is a
backward-compatible addition — existing code only reads the keys it uses.

**Warning signs:** `KeyError: 'headwind_kmh'` in `detect_heavy_wind()`.

### Pitfall 2: Date Comparison Type Mismatch

**What goes wrong:** `event['date']` from `get_all_upcoming_events()` is a Python `date`
object (from psycopg2). Comparing with `date.today() + timedelta(days=28)` works only if
both sides are `date` objects. If `event['date']` is a string (e.g., from mock data), `>`
comparison will raise `TypeError`.

**Why it happens:** psycopg2 returns `date` objects, but the event dict in `events_with_defaults`
preserves the original type (not converted to string — `date_str` is the string alias).

**How to avoid:** Compare `event['date']` directly (it is a `date` object from DB). Test
with mock data that uses actual `date` objects too.

**Warning signs:** `TypeError: '>' not supported between instances of 'str' and 'datetime.date'`

### Pitfall 3: plan_slug_to_id Built Twice

**What goes wrong:** The route already builds `plan_slug_to_id` at line 437, but it's
inside the `if user_id:` block. Wind warning loop needs it unconditionally.

**Why it happens:** The existing use case (user's custom plans) only needs the map when a
user is logged in.

**How to avoid:** Build `plan_slug_to_id` unconditionally, before the `if user_id:` block,
right after `plans = get_all_ride_plans()` at line 404.

**Warning signs:** `NameError: name 'plan_slug_to_id' is not defined` when no user is
logged in.

### Pitfall 4: Vercel Serverless Cold-Start Latency

**What goes wrong:** On first load (cache miss), the brevets page now does N additional
RWGPS calls + N Open-Meteo calls (one pair per event in next 28 days). A typical season
has 5-15 events in a 28-day window — that's potentially 10-30 additional API calls per page
load on cache miss.

**Why it happens:** Vercel serverless functions have limited execution time budgets and
each API call adds ~0.5-2s latency.

**How to avoid:**
- Limit to events within 28 days (already required).
- Open-Meteo batch API accepts multiple lat/lng arrays in a single call — each
  `fetch_stop_wind()` for a route already batches all stops on that route. So it's N
  route-level calls, not N*stops calls.
- In practice, most Vercel loads will be cache hits (1h TTL). Cold-start only when cache
  expires or server restarts.
- The existing `CACHE_TIMEOUT` for the page itself doesn't apply (brevets is not globally
  cached), but `fetch_stop_wind()` caches by `wind:{plan_slug}:{date}{hour}`.

**Warning signs:** Slow page loads on first hit; Vercel function timeouts (default 10s).

### Pitfall 5: Missing start_time on Event Dict

**What goes wrong:** `start_time_str` passed to `fetch_stop_wind()` must be an `"HH:MM"`
string. The event dict has `start_time` from `ri.*` in `get_all_upcoming_events()`, but it
may be `None` for some events.

**Why it happens:** `start_time` is not a required field in the `ride` table.

**How to avoid:** Use `str(event.get('start_time') or '07:00')`. Note that if `start_time`
is a Python `time` object (psycopg2 behavior), `str()` converts it correctly to `"HH:MM:SS"`
— you need `[:5]` to trim to `"HH:MM"`. Inspect actual value from DB or use
`plan.get('start_time', '07:00')` pattern from `ride_plan_detail()` line 1203 as reference.

**Warning signs:** `fetch_stop_wind()` receives `"HH:MM:SS"` format — the `hour_str =
start_time_str[:2]` slice handles this correctly, so it's not a bug but note it for
consistency.

## Code Examples

### Adding headwind_kmh to fetch_stop_wind() return dict

```python
# Source: services/weather.py — extend existing result.append() at line 411
result.append({
    'wind_speed_kmh': round(float(wind_speed), 1),
    'headwind_kmh': round(float(hw), 1),   # ADD THIS
    'wind_type': wind_type,
    'style': style,
    'label': wind_label(hw),
})
```

### detect_heavy_wind() function

```python
# Source: services/weather.py — new function after fetch_stop_wind()
def detect_heavy_wind(stop_wind):
    """Evaluate per-stop wind list against heavy wind thresholds.

    stop_wind: return value of fetch_stop_wind() — list of dicts or None.
    Each non-None dict must include 'wind_speed_kmh' and 'headwind_kmh' keys.

    Returns dict if heavy wind detected, else None:
        {'max_wind_kmh': float, 'avg_headwind_kmh': float,
         'is_heavy': True, 'description': str}
    """
    if not stop_wind:
        return None
    valid = [s for s in stop_wind if s is not None]
    if not valid:
        return None

    max_wind = max(s['wind_speed_kmh'] for s in valid)
    headwind_values = [s['headwind_kmh'] for s in valid]
    avg_headwind = sum(headwind_values) / len(headwind_values)

    if max_wind > HEAVY_WIND_MAX_KMH or avg_headwind > HEAVY_WIND_AVG_HEADWIND_KMH:
        return {
            'max_wind_kmh': round(max_wind, 1),
            'avg_headwind_kmh': round(avg_headwind, 1),
            'is_heavy': True,
            'description': (
                f"Strong headwinds expected \u2014 "
                f"avg {round(avg_headwind, 1)} km/h headwind, "
                f"gusts to {round(max_wind, 1)} km/h"
            ),
        }
    return None
```

### Wind warning loop in upcoming_brevets() route

```python
# Source: routes/riders.py — add after _match_plans_to_events() call
from services.weather import fetch_stop_wind, detect_heavy_wind

# Build plan_slug -> plan_id map (move outside if user_id block)
plan_slug_to_id = {plan['slug']: plan['id'] for plan in plans}

# Compute wind warnings for rides in next 28 days with ride plans
cutoff = date.today() + timedelta(days=28)
wind_warnings = []
for event in rusa_events:
    event_date = event.get('date')
    if not event_date or event_date > cutoff:
        continue
    plan_slug = event.get('plan_slug')
    if not plan_slug:
        continue
    plan_id = plan_slug_to_id.get(plan_slug)
    if not plan_id:
        continue
    weather_rwgps_url = event.get('plan_rwgps_url_team') or event.get('rwgps_url')
    if not weather_rwgps_url:
        continue
    weather_route_id = _extract_rwgps_route_id(weather_rwgps_url)
    if not weather_route_id:
        continue
    try:
        plan_stops = get_ride_plan_stops(plan_id)
        route_data = fetch_route(weather_route_id)
        track_points = route_data.get('track_points') or []
        stop_wind = fetch_stop_wind(
            stops=plan_stops,
            track_points=track_points,
            plan_slug=plan_slug,
            start_time_str=str(event.get('start_time') or '07:00'),
            cache=cache,
        )
        warning = detect_heavy_wind(stop_wind)
        if warning:
            warning['ride_name'] = event.get('route_name') or event.get('name', '')
            warning['ride_date'] = event.get('date_str', str(event_date))
            wind_warnings.append(warning)
    except Exception:
        current_app.logger.exception(
            "Wind warning check failed for event %s", event.get('id'))
```

### Template banner in upcoming_brevets.html

```html
{# Insert after <div class="hero"> block, before <div class="container section"> #}
{% if wind_warnings %}
<div style="background:linear-gradient(135deg,#fef3c7,#fde68a);
            border:2px solid #f59e0b;border-radius:12px;
            padding:16px 20px;margin:16px auto;max-width:1200px;
            box-shadow:0 4px 12px rgba(245,158,11,0.2);">
  <div style="font-weight:800;color:#92400e;font-size:1.05rem;margin-bottom:10px;">
    &#9888;&#65039; Heavy Winds Forecast
  </div>
  {% for w in wind_warnings %}
  <div style="margin-bottom:6px;color:#78350f;font-size:0.9rem;">
    <strong>{{ w.ride_name }}</strong> &mdash; {{ w.ride_date }}:
    {{ w.description }}
  </div>
  {% endfor %}
</div>
{% endif %}
```

## State of the Art

| Old Pattern | Phase 4 Pattern | Notes |
|-------------|----------------|-------|
| No wind data on brevets page | Wind warnings for next 28 days | Phase 3 proved the full pipeline; Phase 4 reuses it |
| Threshold check inline in route | `detect_heavy_wind()` pure function in service | Keeps routes thin; enables unit testing without Flask context |

## Open Questions

1. **headwind_kmh key not in current fetch_stop_wind() output**
   - What we know: The function computes `hw` internally but only includes it in `wind_label()` and `classify_wind()`; it does not return `headwind_kmh`.
   - What's unclear: Is there any existing code that breaks if we add `headwind_kmh` to the returned dict?
   - Recommendation: Add it — it's a purely additive change. No existing test reads or rejects unexpected keys. Verify by grepping for callers of `fetch_stop_wind()` — currently only `ride_plan_detail()` at line 1362 consumes it, and the template only accesses `stop_wind[i].style` and `stop_wind[i].label`.

2. **start_time field type from psycopg2**
   - What we know: `get_all_upcoming_events()` SELECTs `ri.*` — if `start_time` is a `TIME` column in PostgreSQL, psycopg2 returns a `datetime.time` object.
   - What's unclear: Whether `fetch_stop_wind()` handles `"HH:MM:SS"` format (from `str(time_obj)`) vs `"HH:MM"`.
   - Recommendation: In `fetch_stop_wind()`, `hour_str = start_time_str[:2]` is already safe — it takes the first 2 chars regardless. But verify by checking the `ride` table `start_time` column type in the schema.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (pytest.ini present at project root) |
| Config file | `pytest.ini` |
| Quick run command | `python3 -m pytest tests/test_weather.py -x -q` |
| Full suite command | `python3 -m pytest tests/ -x -q` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| WARN-01 | Banner renders when wind_warnings is non-empty | unit (template logic via service) | `pytest tests/test_weather.py::TestDetectHeavyWind -x -q` | Wave 0 |
| WARN-02 | Only events within 28 days with plan_slug are evaluated | unit | `pytest tests/test_weather.py::TestDetectHeavyWind -x -q` | Wave 0 |
| WARN-03 | max_wind > 30 OR avg_headwind > 15 triggers warning | unit | `pytest tests/test_weather.py::TestDetectHeavyWind::test_triggers_on_max_wind` | Wave 0 |
| WARN-03 | Below thresholds returns None | unit | `pytest tests/test_weather.py::TestDetectHeavyWind::test_no_warning_below_thresholds` | Wave 0 |
| WARN-04 | Warning dict contains ride_name, ride_date, description | unit | `pytest tests/test_weather.py::TestDetectHeavyWind::test_description_format` | Wave 0 |

### Sampling Rate

- **Per task commit:** `python3 -m pytest tests/test_weather.py -x -q`
- **Per wave merge:** `python3 -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_weather.py::TestDetectHeavyWind` — covers WARN-01, WARN-02, WARN-03, WARN-04 (add to existing test file)

*(Existing `tests/test_weather.py` covers the weather service; `TestDetectHeavyWind` class is a new addition to it. No new test file needed.)*

## Sources

### Primary (HIGH confidence)

- Direct codebase inspection: `services/weather.py` — confirmed `fetch_stop_wind()` return shape, existing threshold constants, function signatures
- Direct codebase inspection: `routes/riders.py` lines 383-481 — confirmed `upcoming_brevets()` current structure, available data (`rusa_events`, `plans`, `plan_slug_to_id`, `cache`, `_extract_rwgps_route_id`)
- Direct codebase inspection: `templates/upcoming_brevets.html` — confirmed template structure, `{% block content %}` placement, existing CSS variable conventions
- Direct codebase inspection: `models.py` lines 566-607 — confirmed `get_all_upcoming_events()` returns `plan_slug`, `plan_rwgps_url_team`, `date`, `start_time` on event dicts
- Direct codebase inspection: `.planning/research/ARCHITECTURE.md` — confirmed Phase 4 design documented in original architecture research

### Secondary (MEDIUM confidence)

- `.planning/REQUIREMENTS.md` — threshold values (WARN-03) confirmed match `HEAVY_WIND_MAX_KMH`/`HEAVY_WIND_AVG_HEADWIND_KMH` constants

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all components directly inspected in codebase
- Architecture: HIGH — follows established Phase 3 patterns exactly; no new patterns introduced
- Pitfalls: HIGH — identified via direct code inspection (missing headwind_kmh key confirmed by reading fetch_stop_wind return dict)

**Research date:** 2026-03-23
**Valid until:** 2026-04-22 (stable stack; only risk is if Phase 3 output changes before Phase 4 executes)
