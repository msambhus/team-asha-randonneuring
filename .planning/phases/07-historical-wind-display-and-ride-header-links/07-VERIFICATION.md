---
phase: 07-historical-wind-display-and-ride-header-links
verified: 2026-03-23T22:00:00Z
status: passed
score: 7/7 must-haves verified
gaps: []
human_verification:
  - test: "Visit a rider profile for a 2025-2026 season ride with a linked plan and click the ride name"
    expected: "Browser navigates to the ride plan detail page at /rider/plan/<slug>"
    why_human: "URL routing and page load behavior cannot be verified by grep"
  - test: "Visit the Strava analysis page for a completed 2026 ride with a linked RWGPS plan"
    expected: "Comparison table shows an 'Actual Wind' column with colored cells (green/red/blue) and a wind legend below the table"
    why_human: "Requires live DB data, real RWGPS API route, and Open-Meteo archive API response to exercise the full path"
  - test: "Visit the Strava analysis page for a completed ride with no linked plan"
    expected: "Page loads without error and no wind column appears"
    why_human: "Graceful degradation in the no-plan path needs visual confirmation"
---

# Phase 07: Historical Wind Display and Ride Header Links Verification Report

**Phase Goal:** Riders viewing their Strava analysis see "Actual Wind" columns for completed 2026 rides, and 2025/2026 season ride names link directly to ride detail pages.

**Verified:** 2026-03-23T22:00:00Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| #   | Truth | Status | Evidence |
| --- | ----- | ------ | -------- |
| 1 | 2025 and 2026 season ride names with linked ride plans appear as clickable links in rider profile | VERIFIED | `rider_profile.html` line 448: `{% if sd.season.name in ['2024-2025', '2025-2026'] and p.plan_slug %}` wraps `<a href="...ride_plan_detail...">` |
| 2 | Ride names without linked ride plans remain plain text (no broken links) | VERIFIED | `{% else %}<strong style="color:var(--primary);">` branch present at line 453 |
| 3 | Clicking a ride name link navigates to the ride plan detail page | HUMAN NEEDED | `url_for('riders.ride_plan_detail', slug=p.plan_slug)` used — routing correctness requires browser test |
| 4 | Strava analysis page for a completed 2026 ride with linked plan shows Actual Wind column | VERIFIED | `strava_ride_analysis.html` line 464: `{% if stop_wind %}<th style="text-align:center;">Actual Wind</th>{% endif %}` |
| 5 | Wind cells use green/red/blue color coding with intensity and font scaling | VERIFIED | Template line 580: `background:{{ w.style.background }};color:{{ w.style.color }};font-size:{{ w.style.font_size }}` — driven by `wind_cell_style()` return value |
| 6 | Column header reads "Actual Wind" not "Forecast" or "Wind" | VERIFIED | Template line 464: literal text `Actual Wind` confirmed |
| 7 | Rides without linked plan or RWGPS route show no wind column and no error | VERIFIED | `routes/riders.py` line 873-907: `stop_wind = None` default, gated by `has_plan and plan_stops and ride.get('date')`, `except Exception` catches all failures and sets `stop_wind = None` |

**Score:** 7/7 truths verified (3 also require human confirmation for live behavior)

---

### Required Artifacts

#### Plan 07-01 Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `models.py` | `get_rider_participation` returns `plan_slug` via `LEFT JOIN ride_plan` | VERIFIED | Line 251: `rp.slug as plan_slug`; Line 255: `LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id` |
| `templates/rider_profile.html` | Conditional `<a>` tag wrapping ride names when `plan_slug` present | VERIFIED | Lines 448-455: full conditional branch with `url_for('riders.ride_plan_detail', slug=p.plan_slug)` |
| `tests/test_models.py` | Tests verifying `plan_slug` column in `get_rider_participation` results | VERIFIED | `TestRiderParticipationPlanSlug` class with 5 tests (SQL content, key presence, slug value, None for unlinked) |

#### Plan 07-02 Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `routes/riders.py` | `ride_strava_analysis` route passes `stop_wind` dict to template | VERIFIED | Lines 872-915: full wind fetch block with `stop_wind=stop_wind` passed to `render_template` |
| `templates/strava_ride_analysis.html` | Conditional "Actual Wind" column in comparison table with wind cell styling | VERIFIED | Lines 464, 575-587, 592-600: header, per-row cells, and legend all present |
| `tests/test_weather.py` | Integration tests for historical wind in strava analysis route | VERIFIED | `TestStravaAnalysisWind` class with 4 tests at lines 1644-1831 |

---

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | -- | --- | ------ | ------- |
| `models.py` | `templates/rider_profile.html` | `plan_slug` column in participation query result | WIRED | Template accesses `p.plan_slug` at line 448; `get_rider_participation` returns it at line 251 |
| `routes/riders.py` | `services/weather.py` | `get_historical_stop_wind` call with `stops, track_points, ride_date, ride_id` | WIRED | Line 876: `from services.weather import get_historical_stop_wind, wind_cell_style`; called at line 890-895 |
| `routes/riders.py` | `templates/strava_ride_analysis.html` | `stop_wind` dict passed to `render_template` | WIRED | Line 915: `stop_wind=stop_wind` in the success-path `render_template` call; lines 822 and 857 pass `stop_wind=None` for no-match and error paths |
| `templates/strava_ride_analysis.html` | `stop_wind` dict | `stop_wind.get(row.location)` per-row dict lookup | WIRED | Line 578: `{% set w = stop_wind.get(row.location) %}` — matches `stop_name` key used when building the dict at line 902 |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ----------- | ----------- | ------ | -------- |
| HIST-01 | 07-02-PLAN.md | System pulls actual wind data for completed 2026 rides with linked plans and RWGPS routes | SATISFIED | `routes/riders.py` lines 873-907: guard on `has_plan + plan_stops + ride.get('date')`, fetches RWGPS route, calls `get_historical_stop_wind` |
| HIST-02 | 07-02-PLAN.md | User sees wind conditions in Strava analysis with same column format as ride plans | SATISFIED | `strava_ride_analysis.html` lines 575-587: identical cell span pattern to `ride_plan_detail.html` |
| HIST-03 | 07-02-PLAN.md | Historical wind uses same green/red/blue color coding with intensity and font scaling | SATISFIED | Template uses `w.style.background`, `w.style.color`, `w.style.font_size` from `wind_cell_style()` — same function used in all wind displays |
| HIST-04 | 07-02-PLAN.md | Historical wind columns labeled "Actual Wind" (not "Forecast") | SATISFIED | `strava_ride_analysis.html` line 464: literal text `Actual Wind` confirmed |
| LINK-01 | 07-01-PLAN.md | 2025/2026 season ride names in rider profile link to ride detail pages | SATISFIED | `rider_profile.html` line 448: `sd.season.name in ['2024-2025', '2025-2026']` — both seasons covered |
| LINK-02 | 07-01-PLAN.md | Only rides with linked ride plans show as clickable links; others remain plain text | SATISFIED | `rider_profile.html` line 448: `and p.plan_slug` guard; `{% else %}<strong>` branch for unlinked rides |

All 6 required IDs from PLAN frontmatter are accounted for. No orphaned requirements found: REQUIREMENTS.md traceability table maps exactly HIST-01, HIST-02, HIST-03, HIST-04, LINK-01, LINK-02 to Phase 7 and no additional Phase 7 IDs appear.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| — | — | None found | — | — |

Scanned `models.py`, `routes/riders.py`, `templates/rider_profile.html`, `templates/strava_ride_analysis.html` for TODO/FIXME, placeholder text, empty return values, and stub handlers. None found in phase 07 code paths. The `placeholders` variable appearances in `models.py` are legitimate parameterized SQL construction patterns, not code stubs.

---

### Test Suite

Full test suite run: **325 passed, 6 skipped** — no regressions.

Phase-specific tests:
- `TestRiderParticipationPlanSlug` (5 tests) — all pass, verify SQL content and return values
- `TestStravaAnalysisWind` (4 tests) — all pass, verify `stop_wind` dict building, `style` augmentation, and `None` paths for no-plan and no-RWGPS-route cases

Commit verification: all 6 documented commits (1aeb38d, ac0bb7a, 8684337, df8c318, c99f504, 4d26581) exist in the repository and match their described purposes.

---

### Human Verification Required

#### 1. Ride Name Link Navigation

**Test:** Log in as a rider with 2024-2025 or 2025-2026 season rides that have linked plans. Go to the rider profile page. Find a brevet in the history table with a clickable ride name.
**Expected:** Clicking the ride name navigates to `/rider/plan/<slug>` and the ride plan detail page loads correctly.
**Why human:** URL routing correctness and page load behavior cannot be verified by static analysis.

#### 2. Actual Wind Column in Strava Analysis

**Test:** Find a completed 2026 brevet with a linked ride plan that has a RWGPS route URL. Navigate to that ride's Strava analysis page (`/rider/<rusa_id>/ride/<ride_id>/strava-analysis`).
**Expected:** The comparison table shows an "Actual Wind" column as the rightmost header, each planned stop row shows a colored wind cell (green for tailwind, red for headwind, blue for crosswind), and a wind legend appears below the table.
**Why human:** Requires live DB data (ride with `ride_plan_id` set), a valid RWGPS route with track points, and Open-Meteo archive API availability for a date within the past ERA5 window. The full data pipeline cannot be exercised by mocked tests alone.

#### 3. Graceful Degradation — No Plan

**Test:** Navigate to the Strava analysis page for a ride that has no linked ride plan.
**Expected:** The page loads without error, the comparison table shows no "Actual Wind" column, and no wind legend appears.
**Why human:** Confirms the `stop_wind=None` path renders cleanly in the browser.

---

### Gaps Summary

No gaps. All must-haves from both plan frontmatter blocks are verified in the actual codebase. The implementation matches the plan exactly with no deviations.

---

_Verified: 2026-03-23T22:00:00Z_
_Verifier: Claude (gsd-verifier)_
