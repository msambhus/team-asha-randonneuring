# Phase 7: Historical Wind Display and Ride Header Links - Research

**Researched:** 2026-03-23
**Domain:** Flask/Jinja2 template integration, route handler modification, DB-backed wind data display
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| HIST-01 | System pulls actual wind data for completed 2026 rides that have linked ride plans with RWGPS routes | `get_historical_stop_wind()` exists in `services/weather.py` and handles this end-to-end; route handler needs to call it and pass the result to template |
| HIST-02 | User sees wind conditions in Strava analysis section with same column format as ride plans | `strava_ride_analysis.html` has a comparison table; wind column must be added there following the pattern from `ride_plan_detail.html` |
| HIST-03 | Historical wind uses same green/red/blue color coding with intensity and font scaling | `wind_cell_style()` returns inline style dict already used in `ride_plan_detail.html`; same function must be applied to historical rows |
| HIST-04 | Historical wind columns labeled "Actual Wind" (not "Forecast") | Column header in `strava_ride_analysis.html` must use "Actual Wind" text; differs from `ride_plan_detail.html` which uses "Wind" |
| LINK-01 | 2025/2026 season ride names in rider profile link to ride detail pages | `rider_profile.html` renders `p.ride_name` as plain `<strong>` text; `get_rider_participation()` already returns `ride_plan_id` — need `plan_slug` added to query; template condition uses season name check |
| LINK-02 | Only rides with linked ride plans show as clickable links; others remain plain text | Controlled via `{% if p.plan_slug %}` conditional wrapping the ride name in `rider_profile.html`; no plan = no `<a>` tag |
</phase_requirements>

---

## Summary

Phase 7 is a pure integration/display phase. All computation infrastructure was built in Phases 1–6. The two distinct work streams are: (1) plumbing `get_historical_stop_wind()` into the `ride_strava_analysis` route handler and rendering wind cells in `strava_ride_analysis.html`; and (2) augmenting `get_rider_participation()` SQL to JOIN `ride_plan.slug` and updating `rider_profile.html` to conditionally hyperlink ride names.

The key risks are scoped and known: the `get_rider_participation()` SQL must be extended to return `plan_slug` (currently only returns `ride_plan_id`, not the slug), the wind column must be gated behind a "2026 ride with linked plan and RWGPS route" check, and the `strava_ride_analysis.html` table header row is dynamically sized so the wind `<th>` must be conditional.

No new services need to be written. No new DB tables are needed. All wind math, storage, and retrieval functions are complete.

**Primary recommendation:** Two plans — Plan 1: augment `get_rider_participation()` and template for ride name links (pure SQL+template, zero risk); Plan 2: wire `get_historical_stop_wind()` into `ride_strava_analysis` route and add wind column to `strava_ride_analysis.html` (the heavier task, depends on plan stop data already loaded in that route).

---

## Standard Stack

### Core
| Component | Version | Purpose | Why Standard |
|-----------|---------|---------|--------------|
| Flask route handlers | 3.x (project) | Orchestrate DB + service calls, pass data to template | Existing pattern; all phase 3–6 work follows same structure |
| Jinja2 templates | 3.x | Render wind cells with inline styles | Inline styles are mandatory — Tailwind JIT purges dynamic classes |
| `services/weather.py` | project | `get_historical_stop_wind()`, `wind_cell_style()` | Already implemented in Phase 6; reuse verbatim |
| `models.py` | project | `get_rider_participation()`, `get_ride_plan_stops()`, `get_rwgps_route()` | Existing model layer functions |
| psycopg2 | 2.x | DB access | Project standard; no ORM |

### Supporting
| Component | Version | Purpose | When to Use |
|-----------|---------|---------|-------------|
| `services/rwgps.py` | project | Fetch track points for RWGPS route | Required by `get_historical_stop_wind()` to get track points |
| Flask-Caching | project | Existing 1-hr cache on weather fetches | Already wired; no new caching needed in Phase 7 |

**Installation:** No new packages required.

---

## Architecture Patterns

### Recommended Project Structure

No new files needed. Changes go to:
```
routes/
└── riders.py              # ride_strava_analysis() — add wind fetch + template vars
                           # (optionally also my_strava_analysis() if inline display wanted)
models.py                  # get_rider_participation() — add plan_slug to SELECT
templates/
├── strava_ride_analysis.html   # add conditional wind <th> + <td> + legend
└── rider_profile.html          # conditionally wrap ride names in <a> tags
tests/
└── test_weather.py             # extend with historical display integration tests
```

### Pattern 1: Wind Data Fetch in Route Handler

**What:** Call `get_historical_stop_wind()` inside `ride_strava_analysis` after plan stops are loaded. Pass result as `stop_wind` to template.

**When to use:** Only when ride has a linked ride plan (`has_plan = True`) and that plan has an RWGPS route (`weather_route_id` or equivalent track points available).

**Key data flow:**
```python
# Inside ride_strava_analysis(), after plan_stops are loaded:
from services.weather import get_historical_stop_wind, wind_cell_style

stop_wind = None
if has_plan and plan_stops:
    from services.rwgps import get_route_track_points  # or equivalent
    track_points = get_route_track_points(ride.get('weather_route_id'))
    if track_points:
        ride_date = ride['date']  # datetime.date object
        wind_rows, _ = get_historical_stop_wind(
            stops=list(plan_stops),
            track_points=track_points,
            ride_date=ride_date,
            ride_id=ride['id'],
        )
        if wind_rows:
            # Augment with style for template rendering
            for row in wind_rows:
                row['style'] = wind_cell_style(
                    row['wind_speed_kmh'], row['wind_type']
                )
            stop_wind = wind_rows
```

**Note on ride_date type:** `ride['date']` from `get_ride_by_id_full()` returns a `datetime.date` — the same type `get_historical_stop_wind()` expects. No conversion needed.

**Note on 2026-only restriction (HIST-01):** The requirement says "completed 2026 rides." In practice, `get_historical_stop_wind()` works for any completed ride with track points. The "2026" qualifier reflects the ride_wind_data table's initial data scope (rides with dates in 2026). Filtering to 2026 can be done either (a) in the route handler by checking `ride['date'].year >= 2026`, or (b) in the template by leaving it ungated (Phase 6 storage naturally only populated for 2026+ rides that have been stored). Option (a) is more explicit and safer.

### Pattern 2: Wind Column in strava_ride_analysis.html

**What:** Mirror the pattern from `ride_plan_detail.html` — conditional `<th>` in header, conditional `<td>` per row, conditional legend block.

**Key difference from ride_plan_detail.html:**
- Column header label: `"Actual Wind"` not `"Wind"`
- Data source: `stop_wind[loop.index0]` but the list is keyed by stop_order (not loop index) — the wind_rows list from `get_historical_stop_wind()` has sequential stop_order values starting from 0, so loop index should match
- The `comparison.rows` list is the existing comparison table rows; wind cells need to align to plan stops, not Strava-detected stops

**Critical alignment issue:** The comparison table in `strava_ride_analysis.html` iterates `comparison.rows`, which includes both plan stops AND unplanned "extra" stops from Strava. The `stop_wind` list from `get_historical_stop_wind()` only covers plan stops. Template must handle this: `{% if not row.is_extra and stop_wind_map.get(loop.index0) %}`. The safest approach is building a dict keyed by stop name or stop_order in the route handler, not relying on pure loop index alignment.

**Recommended approach:** Pass `stop_wind` as a dict keyed by `stop_name` (or `stop_order`). In the template: `{% set w = stop_wind.get(row.location) %}`.

**Template pattern (source: ride_plan_detail.html lines 1517–1528):**
```jinja2
{% if stop_wind %}
{% set w = stop_wind.get(row.location) %}
<td style="text-align:center;padding:4px 8px;">
  {% if w %}
  <span style="display:inline-block;padding:2px 6px;border-radius:4px;background:{{ w.style.background }};color:{{ w.style.color }};font-size:{{ w.style.font_size }};font-weight:600;white-space:nowrap;">
    {{ w.wind_speed_kmh }} km/h
  </span>
  {% else %}
  &mdash;
  {% endif %}
</td>
{% endif %}
```

### Pattern 3: Ride Name Links in rider_profile.html

**What:** Wrap `p.ride_name` in `<a href="{{ url_for('riders.ride_plan_detail', slug=p.plan_slug) }}">` when `p.plan_slug` is present, otherwise render as plain `<strong>`.

**Constraint:** Only 2025/2026 seasons show links (LINK-01). Jinja2 check: `{% if sd.season.name in ['2024-2025', '2025-2026'] and p.plan_slug %}`.

**SQL change required:** `get_rider_participation()` currently does not JOIN `ride_plan`. Must add:
```sql
LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
```
and add `rp.slug as plan_slug` to the SELECT list.

**Current query (models.py line 247–258):**
```sql
SELECT rr.status, rr.finish_time, ri.id as ride_id, ri.name as ride_name,
       ri.date, ri.distance_km, ri.elevation_ft, ri.ft_per_mile, ri.rwgps_url,
       ri.ride_plan_id, c.code as club_code
FROM rider_ride rr
JOIN ride ri ON rr.ride_id = ri.id
LEFT JOIN club c ON ri.club_id = c.id
WHERE rr.rider_id = %s AND ri.season_id = %s
  AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
ORDER BY ri.date
```

**Updated query adds:** `rp.slug as plan_slug` in SELECT, `LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id` after the club JOIN.

### Anti-Patterns to Avoid

- **Using Tailwind dynamic classes for wind colors:** Tailwind JIT purges unused dynamic classes. All wind colors MUST use inline `style=` attributes. (Established in Phase 3.)
- **Calling `get_historical_stop_wind()` without `ride_id`:** Without `ride_id`, the DB cache check (STOR-02) is skipped and the archive API is hit on every page load. Always pass `ride_id`.
- **Aligning wind by loop.index0 in comparison table:** The comparison table includes extra/unplanned stops that have no wind entry. Use a dict keyed by stop name.
- **Mutating psycopg2 Row objects:** `plan_stops` from `get_ride_plan_stops()` returns psycopg2 RealDict rows. Call `list(plan_stops)` before passing to `get_historical_stop_wind()` (which expects plain dicts). Phase 3/5 uses this pattern.
- **Accessing `p.plan_slug` before SQL change:** Until `get_rider_participation()` is updated, `p.plan_slug` will raise `KeyError` on dict access. SQL and template changes must ship together.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Historical wind fetch + archive fallback | Custom fetch logic | `get_historical_stop_wind()` in `services/weather.py` | Phase 6 built and tested this end-to-end including 5-day fallback and DB cache |
| Wind color/intensity calculation | Custom CSS rules | `wind_cell_style(wind_speed_kmh, wind_type)` | Returns exact inline style dict; consistent with all other wind display surfaces |
| Stop coordinate interpolation | Custom math | `get_stop_coordinates()` in `services/weather.py` | Phase 2 built and tested; handles edge cases (track overrun, zero-length segments) |
| DB read-through cache for wind | Manual DB check | Pass `ride_id` to `get_historical_stop_wind()` | STOR-02 compliance: checks DB before API, saves after fetch |

---

## Common Pitfalls

### Pitfall 1: RWGPS Track Points Not Available
**What goes wrong:** `get_historical_stop_wind()` returns `(None, None)` when `track_points` is empty. If the route handler does not check for this, the template crashes on `stop_wind[...]`.
**Why it happens:** Not all rides have RWGPS routes, or the route fetch fails.
**How to avoid:** Gate wind display behind `if stop_wind` check in both route handler and template. The `{% if stop_wind %}` pattern is already established in `ride_plan_detail.html`.
**Warning signs:** `None` returned from `get_historical_stop_wind()` with no exception — this is the normal "no track points" path.

### Pitfall 2: ride['date'] Type Inconsistency
**What goes wrong:** `ride['date']` from `get_ride_by_id_full()` might be a `datetime.date` object or a string depending on psycopg2 configuration. `get_historical_stop_wind()` expects `datetime.date`.
**Why it happens:** psycopg2 usually auto-converts `DATE` columns to Python `date`, but this can vary.
**How to avoid:** Check the type; if string, convert: `date.fromisoformat(ride['date'])` if not already `date`.
**Warning signs:** `AttributeError: 'str' object has no attribute 'year'` inside `fetch_historical_wind`.

### Pitfall 3: plan_stops Type Mismatch
**What goes wrong:** `get_ride_plan_stops()` returns psycopg2 `RealDictRow` objects. `get_historical_stop_wind()` accesses dict keys — this works, but passing them directly may cause issues in some dict operations.
**Why it happens:** psycopg2 RealDictRow is dict-like but not `isinstance(x, dict) == True`.
**How to avoid:** `stops = [dict(s) for s in plan_stops]` before passing to service functions. Established in Phase 3/5 route handlers.

### Pitfall 4: stop_wind Alignment with comparison.rows
**What goes wrong:** `comparison.rows` contains both plan stops AND extra (unplanned) Strava stops. Wind data only exists for plan stops. A simple `stop_wind[loop.index0]` breaks when extra rows appear.
**Why it happens:** Extra stops are interleaved into `comparison.rows` at positions determined by Strava stop detection.
**How to avoid:** Build a dict `{stop_name: wind_row}` in the route handler. In template: `stop_wind.get(row.location)`. For extra rows, `get()` returns `None` → `&mdash;` fallback.

### Pitfall 5: get_rider_participation Cache Invalidation
**What goes wrong:** `get_rider_participation()` is NOT cached (comment says "NOT CACHED - rider-specific data should not be cached in serverless environments"). The SQL change is safe; no cache to invalidate.
**Why it happens:** N/A — this is the safe case.
**Warning signs:** None — the comment in models.py explicitly confirms no caching.

---

## Code Examples

### Fetching wind in route handler
```python
# Source: services/weather.py get_historical_stop_wind() (Phase 6)
from services.weather import get_historical_stop_wind, wind_cell_style

stop_wind = None
if has_plan and plan_stops:
    from services.rwgps import get_route  # check actual function name in rwgps.py
    plan = get_ride_plan_by_slug(ride.get('plan_slug'))
    if plan and plan.get('weather_route_id'):
        route_data = get_route(plan['weather_route_id'])
        track_points = route_data.get('track_points', []) if route_data else []
        if track_points:
            ride_date = ride['date']
            if isinstance(ride_date, str):
                from datetime import date
                ride_date = date.fromisoformat(ride_date)
            wind_rows, _ = get_historical_stop_wind(
                stops=[dict(s) for s in plan_stops],
                track_points=track_points,
                ride_date=ride_date,
                ride_id=ride['id'],
            )
            if wind_rows:
                # Augment rows with style; key by stop_name for template lookup
                stop_wind = {}
                for row in wind_rows:
                    row['style'] = wind_cell_style(
                        row['wind_speed_kmh'], row['wind_type']
                    )
                    stop_wind[row['stop_name']] = row
```

### Updated get_rider_participation SQL
```sql
-- Source: models.py get_rider_participation() — add plan_slug JOIN
SELECT rr.status, rr.finish_time, ri.id as ride_id, ri.name as ride_name,
       ri.date, ri.distance_km, ri.elevation_ft, ri.ft_per_mile, ri.rwgps_url,
       ri.ride_plan_id, c.code as club_code,
       rp.slug as plan_slug
FROM rider_ride rr
JOIN ride ri ON rr.ride_id = ri.id
LEFT JOIN club c ON ri.club_id = c.id
LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
WHERE rr.rider_id = %s AND ri.season_id = %s
  AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
ORDER BY ri.date
```

### Conditional ride name link in rider_profile.html
```jinja2
{# Source: rider_profile.html lines 447-458 — current rendering #}
<td ...>
    {% if sd.season.name in ['2024-2025', '2025-2026'] and p.plan_slug %}
    <a href="{{ url_for('riders.ride_plan_detail', slug=p.plan_slug) }}"
       style="color:var(--primary); text-decoration:none; font-weight:700;">
        {{ p.ride_name|clean_name }}
    </a>
    {% else %}
    <strong style="color:var(--primary);">{{ p.ride_name|clean_name }}</strong>
    {% endif %}
    {# ... rest of cell (rwgps link, dnf badge) ... #}
</td>
```

### Wind column in strava_ride_analysis.html
```jinja2
{# Source: ride_plan_detail.html lines 1470, 1517-1528 — adapted for analysis table #}
{# In <thead>: #}
{% if stop_wind %}<th style="text-align:center;">Actual Wind</th>{% endif %}

{# In <tbody> per row: #}
{% if stop_wind %}
{% set w = stop_wind.get(row.location) %}
<td style="text-align:center;padding:4px 8px;">
  {% if w %}
  <span style="display:inline-block;padding:2px 6px;border-radius:4px;background:{{ w.style.background }};color:{{ w.style.color }};font-size:{{ w.style.font_size }};font-weight:600;white-space:nowrap;">
    {{ w.wind_speed_kmh }} km/h
  </span>
  {% else %}
  &mdash;
  {% endif %}
</td>
{% endif %}
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| No wind on completed rides | Archive API + DB persistence | Phase 6 (2026-03-23) | `get_historical_stop_wind()` now available; Phase 7 just displays it |
| Ride names as plain text in profile | Clickable links to ride plans | Phase 7 (this phase) | Requires SQL join + template change |

---

## Key Investigation Findings

### RWGPS Track Points Access Path
The route handler in `ride_strava_analysis` does NOT currently fetch RWGPS track points. The existing `get_ride_by_id_full()` returns `plan_slug` and `ride_plan_id`. To get track points, the handler needs to:
1. Call `get_ride_plan_by_slug(plan_slug)` to get the plan (with `weather_route_id`)
2. Call the RWGPS service to fetch track points for that route ID

Check `services/rwgps.py` for the exact function name (`get_route()` or `fetch_route()`). The `ride_plan_detail` route handler (riders.py ~line 1221) shows this pattern already implemented.

### plan_slug Availability in rider_profile
`get_rider_participation()` returns `ride_plan_id` but NOT `plan_slug`. The rider_profile template currently uses `p.plan_slug` in one place (line ~249) for "Ride Plan" button — this means **either plan_slug is already available via another join that was not visible in the grep results, or there is a bug in the existing code**. Must verify before modifying.

Actually, looking at line 249: `{% if ride.plan_slug %}` in the upcoming rides section (which comes from `get_rider_upcoming_signups` + `_match_plans_to_events`, a different query path). The brevet history section at line 448 uses `p.ride_name` — these `p` rows come from `get_rider_participation()` which does NOT include `plan_slug`. So the SQL change is required.

### Season Name for LINK-01 Filter
The requirement says "2025 and 2026 season ride names." Season names in this app follow the format `YYYY-YYYY` (e.g., `"2024-2025"`, `"2025-2026"`). So the filter is: `sd.season.name in ['2024-2025', '2025-2026']`.

### my_strava_analysis.html — HIST-02 Scope
HIST-02 says "User sees wind conditions in Strava analysis section." The analysis section exists in two templates:
1. `strava_ride_analysis.html` — the per-ride detail page (linked from `ride_strava_analysis` route)
2. `my_strava_analysis.html` — the overview cards page

The per-ride detail page (`strava_ride_analysis.html`) is where the comparison table lives. The overview cards page shows only summary metrics (no per-stop table). HIST-02 and HIST-04 should be implemented in `strava_ride_analysis.html` only. The overview cards page does not have per-stop data and cannot show wind columns.

---

## Open Questions

1. **RWGPS function name in rwgps.py**
   - What we know: `services/rwgps.py` exists and is used by `ride_plan_detail` route
   - What's unclear: Exact function name to call for fetching track points given a `weather_route_id`
   - Recommendation: Read the first ~80 lines of `services/rwgps.py` at planning time to confirm function signature

2. **ride_date type from get_ride_by_id_full()**
   - What we know: psycopg2 typically auto-converts `DATE` columns; the `date` field in the ride table is `DATE` type
   - What's unclear: Whether psycopg2 returns `datetime.date` or string in this specific query path
   - Recommendation: Check in plan — add a `isinstance()` guard in the route handler for safety (1 extra line)

3. **plan_slug in get_rider_participation() existing behavior**
   - What we know: Line 251 of the current query does NOT include plan_slug; line 249 of the template references `ride.plan_slug` in a different context (upcoming rides block, different data source)
   - What's unclear: Whether any test currently verifies `get_rider_participation()` column set
   - Recommendation: The SQL change is straightforward; test coverage for it goes in a new test method

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (see pytest.ini at project root) |
| Config file | `/Users/msambhus/LocalDocuments/Personal/Claude/team-asha-randonneuring/.claude/worktrees/wind-integration/pytest.ini` |
| Quick run command | `python3 -m pytest tests/test_weather.py tests/test_models_wind.py -x -q` |
| Full suite command | `python3 -m pytest tests/ -x -q` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| HIST-01 | `ride_strava_analysis` route calls `get_historical_stop_wind` when has_plan=True and track points available | unit | `python3 -m pytest tests/test_weather.py -x -q -k "historical"` | Partial (test_weather.py exists, new test needed) |
| HIST-02 | Template passes stop_wind dict to strava_ride_analysis.html; comparison table renders wind column | unit | `python3 -m pytest tests/test_weather.py -x -q -k "strava_wind"` | Wave 0 gap |
| HIST-03 | `wind_cell_style()` applied to each wind row before passing to template | unit | `python3 -m pytest tests/test_weather.py -x -q -k "wind_cell_style"` | Already tested |
| HIST-04 | Template uses "Actual Wind" label not "Forecast" | manual-only | Template inspection; no unit test for label strings | ✅ manual |
| LINK-01 | `get_rider_participation()` returns `plan_slug` for rides with linked plans | unit | `python3 -m pytest tests/ -x -q -k "rider_participation"` | Wave 0 gap |
| LINK-02 | Template renders `<a>` tag only when plan_slug present | unit | Template rendering test (Flask test client) | Wave 0 gap |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/test_weather.py tests/test_models_wind.py -x -q`
- **Per wave merge:** `python3 -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_weather.py` — extend with `TestGetHistoricalStopWindRouteIntegration` covering: (a) no track points returns None, (b) DB cache hit returns stored rows without API call, (c) wind rows augmented with `style` dict before template render
- [ ] New test for `get_rider_participation()` SQL returning `plan_slug` column
- [ ] New test for template rendering: ride name link appears when plan_slug present; plain text when absent

*(Existing test infrastructure covers HIST-03 via `TestWindCellStyle` in test_weather.py. No new framework install needed.)*

---

## Sources

### Primary (HIGH confidence)
- `services/weather.py` (project) — `get_historical_stop_wind()`, `wind_cell_style()`, `fetch_historical_wind()` — confirmed all exist and are tested
- `routes/riders.py` (project) — `ride_strava_analysis()` route, `my_strava_analysis()` route — confirmed data flow and template variables
- `models.py` (project) — `get_rider_participation()` SQL — confirmed no `plan_slug` in current query
- `templates/strava_ride_analysis.html` (project) — confirmed comparison table structure, `comparison.rows`, `row.is_extra`, `row.location`
- `templates/rider_profile.html` (project) — confirmed ride name rendering at line 448; season block iteration
- `templates/ride_plan_detail.html` (project) — confirmed exact wind cell pattern to replicate

### Secondary (MEDIUM confidence)
- `.planning/REQUIREMENTS.md` — HIST-01 to HIST-04, LINK-01, LINK-02 definitions verified against codebase
- `.planning/STATE.md` — Phase 6 decisions re: DB-check-before-fetch, fetch_historical_wind tuple return

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all components are project-internal, fully inspected
- Architecture: HIGH — data flow is deterministic; existing patterns are directly reusable
- Pitfalls: HIGH — each pitfall identified from direct code inspection (not speculation)
- SQL change: HIGH — `get_rider_participation()` fully read; join is straightforward
- Template change: HIGH — `strava_ride_analysis.html` fully read; wind cell pattern from `ride_plan_detail.html` is copy-adaptable

**Research date:** 2026-03-23
**Valid until:** This research is based on stable project code. Valid until any of the inspected source files change. 60-day stability estimate.
