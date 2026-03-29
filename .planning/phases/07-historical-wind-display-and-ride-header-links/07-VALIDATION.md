---
phase: 7
slug: historical-wind-display-and-ride-header-links
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-23
---

# Phase 7 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | `pytest.ini` |
| **Quick run command** | `python3 -m pytest tests/test_weather.py tests/test_models_wind.py -x -q` |
| **Full suite command** | `python3 -m pytest tests/ -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/test_weather.py tests/test_models_wind.py -x -q`
- **After every plan wave:** Run `python3 -m pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 07-01-01 | 01 | 1 | LINK-01 | unit | `python3 -m pytest tests/ -x -q -k "rider_participation"` | ❌ W0 | ⬜ pending |
| 07-01-02 | 01 | 1 | LINK-02 | unit | `python3 -m pytest tests/ -x -q -k "ride_name_link"` | ❌ W0 | ⬜ pending |
| 07-02-01 | 02 | 1 | HIST-01 | unit | `python3 -m pytest tests/test_weather.py -x -q -k "historical"` | ❌ W0 | ⬜ pending |
| 07-02-02 | 02 | 1 | HIST-02 | unit | `python3 -m pytest tests/test_weather.py -x -q -k "strava_wind"` | ❌ W0 | ⬜ pending |
| 07-02-03 | 02 | 1 | HIST-03 | unit | `python3 -m pytest tests/test_weather.py -x -q -k "wind_cell_style"` | ✅ | ⬜ pending |
| 07-02-04 | 02 | 1 | HIST-04 | manual | Template inspection | ✅ manual | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_models.py` — add test for `get_rider_participation()` returning `plan_slug` column
- [ ] `tests/test_weather.py` — extend with historical wind route integration tests
- [ ] Template rendering tests for ride name link (plan_slug present vs absent)

*Existing infrastructure covers HIST-03 via `TestWindCellStyle` in test_weather.py. No new framework install needed.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| "Actual Wind" column label (not "Forecast") | HIST-04 | Label is static text in template | Inspect `strava_ride_analysis.html` for "Actual Wind" string |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
