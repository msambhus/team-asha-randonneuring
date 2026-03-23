---
phase: 6
slug: historical-wind-archive-api-and-db-persistence
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-23
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (from requirements-dev.txt) |
| **Config file** | none — pytest discovers tests/ automatically |
| **Quick run command** | `python3 -m pytest tests/test_weather.py tests/test_models_wind.py -x -q` |
| **Full suite command** | `python3 -m pytest tests/ -x -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/test_weather.py tests/test_models_wind.py -x -q`
- **After every plan wave:** Run `python3 -m pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 06-01-01 | 01 | 1 | STOR-01 | migration | run `migrations/011_add_ride_wind_data.sql` | Wave 0 | pending |
| 06-01-02 | 01 | 1 | STOR-02 | unit | `python3 -m pytest tests/test_models_wind.py -k "save_and_get" -x` | Wave 0 | pending |
| 06-01-03 | 01 | 1 | STOR-02 | unit | `python3 -m pytest tests/test_models_wind.py -k "no_refetch" -x` | Wave 0 | pending |
| 06-01-04 | 01 | 1 | STOR-03 | unit | `python3 -m pytest tests/test_models_wind.py -k "data_source" -x` | Wave 0 | pending |
| 06-02-01 | 02 | 1 | WIND-07 | unit | `python3 -m pytest tests/test_weather.py -k "archive" -x` | Wave 0 | pending |
| 06-02-02 | 02 | 1 | WIND-08 | unit | `python3 -m pytest tests/test_weather.py -k "past_days" -x` | Wave 0 | pending |
| 06-02-03 | 02 | 1 | WIND-08 | unit | `python3 -m pytest tests/test_weather.py -k "lag_boundary" -x` | Wave 0 | pending |
| 06-02-04 | 02 | 1 | WIND-07 | unit | `python3 -m pytest tests/test_weather.py -k "archive_single" -x` | Wave 0 | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_models_wind.py` — new file covering `get_ride_wind_data()` and `save_ride_wind_data()` (mock `_execute()` / `get_db()`)
- [ ] `tests/test_weather.py` — extend with `TestFetchHistoricalWind`, `TestFetchArchiveWind`, `TestFetchForecastPastDays` classes

*Existing infrastructure covers test framework — only test stubs needed.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| ride_wind_data table creation | STOR-01 | DDL migration against real DB | Run `migrations/011_add_ride_wind_data.sql` against dev DB, verify columns with `\d ride_wind_data` |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
