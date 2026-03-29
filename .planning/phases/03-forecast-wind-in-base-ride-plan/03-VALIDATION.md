---
phase: 3
slug: forecast-wind-in-base-ride-plan
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-23
---

# Phase 3 — Validation Strategy

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
| 03-01-01 | 01 | 1 | WIND-06 | unit | `python3 -m pytest tests/test_weather.py::TestFetchStopWind -x` | ❌ W0 | ⬜ pending |
| 03-01-02 | 01 | 1 | WIND-06 | unit | `python3 -m pytest tests/test_weather.py::TestFetchStopWind::test_cache_hit -x` | ❌ W0 | ⬜ pending |
| 03-01-03 | 01 | 1 | WIND-06 | unit | `python3 -m pytest tests/test_weather.py::TestFetchStopWind::test_empty_track_returns_none -x` | ❌ W0 | ⬜ pending |
| 03-01-04 | 01 | 1 | WIND-06 | unit | `python3 -m pytest tests/test_weather.py::TestFetchStopWind::test_api_error_returns_none -x` | ❌ W0 | ⬜ pending |
| 03-02-01 | 02 | 2 | BPLN-01 | integration | manual route-level test | N/A | ⬜ pending |
| 03-02-02 | 02 | 2 | BPLN-02 | unit | `python3 -m pytest tests/test_weather.py::TestWindCellStyle -x` | ✅ exists | ⬜ pending |
| 03-02-03 | 02 | 2 | BPLN-03 | unit | `python3 -m pytest tests/test_weather.py::TestWindCellStyle -x` | ✅ exists | ⬜ pending |
| 03-02-04 | 02 | 2 | BPLN-04 | unit | `python3 -m pytest tests/test_weather.py::TestWindCellStyle -x` | ✅ exists | ⬜ pending |
| 03-02-05 | 02 | 2 | BPLN-05 | integration | manual smoke test | N/A | ⬜ pending |
| 03-02-06 | 02 | 2 | BPLN-06 | integration | manual smoke test | N/A | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_weather.py::TestFetchStopWind` — stubs for WIND-06 (add to existing test file)

*BPLN-02/03/04 already covered by `TestWindCellStyle` from Phase 1.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Wind column renders in ride plan page with stop data | BPLN-01 | Template rendering with real Flask context | Load ride plan detail for a route with RWGPS data, verify Wind column header and per-stop cells appear |
| Wind column absent when no wind data available | BPLN-05 | Template conditional guard | Load ride plan detail for a route without RWGPS route, verify no empty Wind column |
| Wind legend block appears below table | BPLN-06 | Visual layout verification | Load ride plan detail with wind data, verify legend shows green/red/blue squares with labels |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
