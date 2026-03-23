---
phase: 4
slug: heavy-wind-warning-banner
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-23
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | `pytest.ini` |
| **Quick run command** | `python3 -m pytest tests/test_weather.py -x -q` |
| **Full suite command** | `python3 -m pytest tests/ -x -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/test_weather.py -x -q`
- **After every plan wave:** Run `python3 -m pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | WARN-01, WARN-03 | unit | `pytest tests/test_weather.py::TestDetectHeavyWind -x -q` | ❌ W0 | ⬜ pending |
| 04-01-02 | 01 | 1 | WARN-04 | unit | `pytest tests/test_weather.py::TestDetectHeavyWind::test_description_format -x -q` | ❌ W0 | ⬜ pending |
| 04-02-01 | 02 | 1 | WARN-02 | unit | `pytest tests/test_weather.py -x -q` | ❌ W0 | ⬜ pending |
| 04-02-02 | 02 | 1 | WARN-01 | integration | `pytest tests/ -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_weather.py::TestDetectHeavyWind` — test class for detect_heavy_wind() covering WARN-01, WARN-03, WARN-04
- [ ] Test cases: triggers on max_wind > 30, triggers on avg_headwind > 15, no warning below thresholds, None/empty input returns None, description format

*Existing `tests/test_weather.py` covers the weather service; `TestDetectHeavyWind` class is a new addition.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Banner visual appearance on upcoming brevets page | WARN-01 | Visual layout in browser | Load `/riders/<season>/upcoming` with a brevet in next 28 days that has heavy winds; verify yellow banner with warning icon appears |
| Banner absent when no heavy winds | WARN-01 | Template rendering in browser | Load page when no events have heavy winds; confirm no banner element in DOM |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
