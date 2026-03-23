---
phase: 03-forecast-wind-in-base-ride-plan
verified: 2026-03-23T17:00:00Z
status: human_needed
score: 10/10 must-haves verified
human_verification:
  - test: "Navigate to a ride plan that has weather_route_id set. Switch to Table view."
    expected: "A Wind column appears between Avg Speed and Time Bank (or Diff. if no cutoff). Each row shows a colored badge — green for tailwind, red for headwind, blue for crosswind — with wind speed in km/h. Lighter winds have a more transparent badge and smaller text; stronger winds have a more opaque badge and larger text. A wind legend with three colored squares appears below the table."
    why_human: "Opacity and font-size scaling (BPLN-03, BPLN-04) require visual inspection of rendered HTML in a browser. Correctness of Open-Meteo data and badge appearance cannot be verified from source code alone."
  - test: "Navigate to a ride plan that has no RWGPS route linked (weather_route_id is None or absent)."
    expected: "No Wind column appears. No empty column, no error. The page renders normally and all other columns remain correctly aligned."
    why_human: "Graceful degradation (BPLN-05) is confirmed by code inspection but the absence-of-column layout is a visual correctness check that requires rendering."
  - test: "Load a ride plan with an RWGPS route twice within one hour."
    expected: "Second load is visibly fast (no Open-Meteo API delay). Only one API call was made. Can confirm via Flask dev server logs — only one 'GET' to open-meteo.com appears."
    why_human: "Cache hit behaviour (WIND-06) requires runtime verification against a live Flask-Caching instance and real network logs."
---

# Phase 3: Forecast Wind in Base Ride Plan — Verification Report

**Phase Goal:** Riders viewing a base ride plan control sheet see a color-coded wind column at every stop, fetched from Open-Meteo via a single batched API call
**Verified:** 2026-03-23T17:00:00Z
**Status:** human_needed — all automated checks pass; three items require browser verification
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | fetch_stop_wind() returns a list of per-stop wind dicts with wind_speed_kmh, wind_type, style, and label keys | VERIFIED | `TestFetchStopWind.test_returns_wind_data_for_each_stop` passes; function at weather.py:305 returns exactly those keys |
| 2 | fetch_stop_wind() returns cached result on second call without triggering a new API request | VERIFIED | `TestFetchStopWind.test_cache_hit` passes; cache.set(cache_key, result, timeout=3600) at weather.py:420 |
| 3 | fetch_stop_wind() returns None when track_points is empty or when the API call fails | VERIFIED | `test_empty_track_returns_none` and `test_api_error_returns_none` pass; early return at weather.py:319 and try/except at 339-343 |
| 4 | Result list length always equals stops list length, with None entries for unresolvable stops | VERIFIED | `test_result_length_matches_stops` and `test_none_coordinate_produces_none_entry` pass |
| 5 | Rider viewing a base ride plan with an RWGPS route sees a Wind column with color-coded wind speed at each stop | VERIFIED (needs human) | Template has `{% if stop_wind %}<th>Wind</th>{% endif %}` and per-row badge cell at lines 1470/1517-1528; route passes stop_wind at line 1389 |
| 6 | Wind cells show green for tailwind, red for headwind, blue for crosswind with opacity varying by speed | VERIFIED (needs human) | wind_cell_style() at weather.py:107-123 returns rgba with opacity 0.15/0.35/0.65 by speed band; template renders w.style.background inline |
| 7 | Wind cell text is smaller for light winds and larger for strong winds | VERIFIED (needs human) | wind_cell_style() returns font_size 0.75rem / 0.875rem / 1.0rem; template renders w.style.font_size inline |
| 8 | A ride plan without a linked RWGPS route shows no Wind column and no error | VERIFIED (needs human) | stop_wind=None when weather_route_id is falsy (riders.py:1357-1371); all template wind markup gated on `{% if stop_wind %}` |
| 9 | A wind legend below the table explains the green/red/blue color coding | VERIFIED (needs human) | Legend block at template lines 1574-1590 with correct hex values #16A34A, #DC2626, #2563EB; gated on `{% if stop_wind %}` |
| 10 | Viewing the same ride plan twice within an hour does not trigger a second Open-Meteo API call | VERIFIED (needs human) | Cache key wind:{plan_slug}:{YYYYMMDD}{HH} checked before fetch_route_weather call; cache.set with timeout=3600 after result |

**Score:** 10/10 truths verified (3 require human browser confirmation)

---

## Required Artifacts

### Plan 03-01 Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `services/weather.py` | fetch_stop_wind() orchestration function | VERIFIED | `def fetch_stop_wind` at line 305; 118 lines of substantive implementation; calls get_stop_coordinates, fetch_route_weather, classify_wind, wind_cell_style, wind_label, calculate_bearing, get_hour_index |
| `tests/test_weather.py` | TestFetchStopWind test class | VERIFIED | `class TestFetchStopWind` at line 752; 8 tests, all passing |

### Plan 03-02 Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `routes/riders.py` | fetch_stop_wind() call in ride_plan_detail() with try/except | VERIFIED | Import at line 47; wind fetch block lines 1356-1371; try/except catches all exceptions; passes stop_wind=stop_wind at line 1389 |
| `templates/ride_plan_detail.html` | Wind column in plan-table and wind legend block | VERIFIED | thead th at line 1470; tbody td at lines 1517-1528 with loop.index0 indexing; tfoot td at line 1567; legend at lines 1574-1590 |

---

## Key Link Verification

### Plan 03-01 Key Links

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| services/weather.py:fetch_stop_wind | services/weather.py:get_stop_coordinates | function call | WIRED | `coords = get_stop_coordinates(stops, track_points)` at line 323 |
| services/weather.py:fetch_stop_wind | services/weather.py:fetch_route_weather | function call | WIRED | `weather_data = fetch_route_weather(valid_coords)` at line 340 |
| services/weather.py:fetch_stop_wind | services/weather.py:classify_wind | function call | WIRED | `wind_type = classify_wind(hw, cw)` at line 408 |

### Plan 03-02 Key Links

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| routes/riders.py:ride_plan_detail | services/weather.py:fetch_stop_wind | function call with try/except | WIRED | `stop_wind = fetch_stop_wind(stops=stops, ...)` at lines 1362-1368; wrapped in try/except at 1359-1371 |
| routes/riders.py:ride_plan_detail | templates/ride_plan_detail.html | render_template stop_wind parameter | WIRED | `stop_wind=stop_wind` at line 1389 in render_template() call |
| templates/ride_plan_detail.html | stop_wind list | Jinja2 loop.index0 indexing | WIRED | `{% set w = stop_wind[loop.index0] %}` at line 1518 |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| WIND-06 | 03-01-PLAN.md | System fetches wind forecast for interpolated stop coordinates via Open-Meteo batch API with 1-hour cache | SATISFIED | fetch_stop_wind() calls fetch_route_weather(valid_coords) (batched); cache TTL=3600; 8 tests pass |
| BPLN-01 | 03-02-PLAN.md | User sees wind column in base ride plan detail page showing wind speed at each stop | SATISFIED (human) | Wind column header and per-row cells present in template; route passes stop_wind to render_template |
| BPLN-02 | 03-02-PLAN.md | Wind cells have green background for tailwind, red for headwind, blue for crosswind | SATISFIED (human) | wind_cell_style() returns correct hex/rgba by wind_type; template renders w.style.background inline |
| BPLN-03 | 03-02-PLAN.md | Wind cell background color opacity scales with wind speed (light 0-5, medium 5-15, strong 15+ km/h) | SATISFIED (human) | Opacity 0.15/0.35/0.65 for <5/<15/15+ in wind_cell_style(); rendered via w.style.background |
| BPLN-04 | 03-02-PLAN.md | Wind cell font size scales with wind speed (small for light, medium for moderate, large for strong) | SATISFIED (human) | Font size 0.75rem/0.875rem/1.0rem for <5/<15/15+ in wind_cell_style(); rendered via w.style.font_size |
| BPLN-05 | 03-02-PLAN.md | Wind column only renders when wind data is available (graceful degradation) | SATISFIED (human) | stop_wind=None when weather_route_id absent; all template wind blocks gated on `{% if stop_wind %}` |
| BPLN-06 | 03-02-PLAN.md | Wind legend section explains green=tailwind, red=headwind, blue=crosswind color coding | SATISFIED (human) | Legend div at template lines 1574-1590 with correct hex values; gated on `{% if stop_wind %}` |

No orphaned requirements: REQUIREMENTS.md traceability table maps WIND-06 and BPLN-01 through BPLN-06 to Phase 3, all claimed by the two plans in this phase directory.

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| routes/riders.py | 710 | Comment "show placeholder so advice button appears" | Info | Unrelated to this phase; in Strava section; no impact |

No blockers or warnings in phase-3 files. No TODO/FIXME/print() in `services/weather.py`, `routes/riders.py` (wind section), or `templates/ride_plan_detail.html` (wind blocks).

---

## Human Verification Required

### 1. Wind column renders with correct colors and scaling

**Test:** Start Flask dev server (`python3 app.py`). Navigate to a ride plan that has an RWGPS route linked (`weather_route_id` is set in the DB). Switch to Table view.
**Expected:** A "Wind" column appears between "Avg Speed" and "Time Bank" (or "Diff." if no cutoff). Each stop row shows a colored badge with wind speed in km/h. Green = tailwind, red = headwind, blue = crosswind. Light-wind badges are more transparent and smaller text (0.75rem); strong-wind badges are more opaque and larger text (1.0rem). A wind legend with three colored squares appears below the table.
**Why human:** Opacity and font-size rendering must be confirmed visually in a browser. The correct Open-Meteo data fetch from a live route requires a real API call that cannot be asserted from source alone.

### 2. Graceful degradation for plans without RWGPS routes

**Test:** Navigate to a ride plan where no RWGPS route is linked (no `weather_route_id`).
**Expected:** No Wind column appears. No empty column, no JavaScript errors, no server error. All other columns remain correctly aligned with matching tfoot totals row.
**Why human:** Column-count alignment in the rendered table (thead/tbody/tfoot) must be visually confirmed — a mismatch would be invisible in source but obvious in the browser.

### 3. Cache prevents duplicate Open-Meteo API calls

**Test:** Load a ride plan with an RWGPS route. Reload within one hour. Watch the Flask dev server terminal output.
**Expected:** Only one HTTP request to open-meteo.com appears in the logs for both page loads. The second load feels faster.
**Why human:** Flask-Caching behaviour requires a live running server with a real cache backend; the test suite mocks the cache object.

---

## Gaps Summary

No gaps found. All automated checks pass:
- `TestFetchStopWind`: 8/8 tests passing
- Full test suite: 271 passed, 6 skipped, 0 failed
- All 4 commits verified in git history (79e86aa, 2591df8, fd639fd, 413bc28)
- All key links wired: get_stop_coordinates, fetch_route_weather, classify_wind called inside fetch_stop_wind; fetch_stop_wind called in ride_plan_detail with try/except; stop_wind passed to render_template and consumed by template with loop.index0 indexing
- All 7 requirement IDs (WIND-06, BPLN-01 through BPLN-06) have implementation evidence
- No TODO, FIXME, placeholder, or print() anti-patterns in phase files
- wind_cell_style() confirms opacity scaling (BPLN-03) and font-size scaling (BPLN-04) are substantively implemented

Three items flagged for human verification are confirmations of correctness (visual rendering, live cache), not gaps. The phase goal is structurally achieved.

---

_Verified: 2026-03-23T17:00:00Z_
_Verifier: Claude (gsd-verifier)_
