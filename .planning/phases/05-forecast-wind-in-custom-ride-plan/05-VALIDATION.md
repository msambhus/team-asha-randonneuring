---
phase: 5
slug: forecast-wind-in-custom-ride-plan
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-03-23
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing) |
| **Config file** | `pytest.ini` |
| **Quick run command** | `python3 -m pytest tests/test_weather.py -x -q` |
| **Full suite command** | `python3 -m pytest tests/ -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/test_weather.py -x -q`
- **After every plan wave:** Run `python3 -m pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | CPLN-01 | unit | `python3 -m pytest tests/test_weather.py -x -q -k "custom_plan_wind"` | ❌ W0 | ⬜ pending |
| 05-01-02 | 01 | 1 | CPLN-02 | unit | `python3 -m pytest tests/test_weather.py -x -q -k "custom_stops"` | ❌ W0 | ⬜ pending |
| 05-01-03 | 01 | 1 | WIND-09 | unit | `python3 -m pytest tests/test_weather.py -x -q -k "normalize"` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_weather.py` — add `TestCustomPlanWind` class covering CPLN-01, CPLN-02
- [ ] Existing test infrastructure covers WIND-09 normalization

*Existing infrastructure covers all other phase requirements.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Visual wind column colors match base plan | CPLN-01 | CSS/style rendering | Compare custom plan wind column appearance with base plan side-by-side |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 15s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
