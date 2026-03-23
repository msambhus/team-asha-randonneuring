---
phase: 1
slug: wind-math-foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-23
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing, no install needed) |
| **Config file** | none — run directly |
| **Quick run command** | `python3 -m pytest tests/test_weather.py -x -q` |
| **Full suite command** | `python3 -m pytest tests/ -x -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/test_weather.py -x -q`
- **After every plan wave:** Run `python3 -m pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | WIND-10 | unit | `python3 -m pytest tests/test_weather.py::TestWindConstants -x` | ❌ W0 | ⬜ pending |
| 01-01-02 | 01 | 1 | WIND-01 | unit | `python3 -m pytest tests/test_weather.py::TestCrosswindComponent -x` | ❌ W0 | ⬜ pending |
| 01-01-03 | 01 | 1 | WIND-02 | unit | `python3 -m pytest tests/test_weather.py::TestClassifyWind -x` | ❌ W0 | ⬜ pending |
| 01-01-04 | 01 | 1 | WIND-03, WIND-04 | unit | `python3 -m pytest tests/test_weather.py::TestWindCellStyle -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_weather.py` — add `TestCrosswindComponent`, `TestClassifyWind`, `TestWindCellStyle`, `TestWindConstants` test classes (file exists; classes do not)

*Existing test infrastructure covers framework and fixtures. Only new test classes are needed.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
