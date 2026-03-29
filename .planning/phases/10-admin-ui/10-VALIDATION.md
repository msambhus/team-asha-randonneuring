---
phase: 10
slug: admin-ui
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-18
---

# Phase 10 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing, no new install) |
| **Config file** | `pytest.ini` at project root |
| **Quick run command** | `pytest tests/test_coaching_models.py -x -q` |
| **Full suite command** | `pytest tests/ -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_coaching_models.py -x -q`
- **After every plan wave:** Run `pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 10-01-01 | 01 | 1 | ADMN-01 | unit | `pytest tests/test_admin_personality.py::TestCompleteness -x` | Wave 0 | pending |
| 10-01-02 | 01 | 1 | ADMN-02 | integration | `pytest tests/test_coaching_models.py::TestCrudPersonalityProfile -x` | exists | pending |
| 10-01-03 | 01 | 1 | ADMN-03 | integration | `pytest tests/test_admin_personality.py::TestTraitEvidence -x` | Wave 0 | pending |
| 10-01-04 | 01 | 1 | ADMN-04 | unit | `pytest tests/test_admin_personality.py::TestConfidenceBadge -x` | Wave 0 | pending |
| 10-01-05 | 01 | 1 | ADMN-05 | unit/smoke | `pytest tests/test_admin_personality.py::TestReExtractDisplay -x` | Wave 0 | pending |
| 10-02-01 | 02 | 1 | ADMN-06 | integration | `pytest tests/test_admin_gear.py -x` | Wave 0 | pending |
| 10-02-02 | 02 | 1 | GEAR-01 | integration | `pytest tests/test_coaching_models.py::TestCrudGearPreference -x` | exists | pending |
| 10-02-03 | 02 | 1 | GEAR-02 | integration | `pytest tests/test_coaching_models.py::TestCrudGearPreference -x` | exists | pending |
| 10-03-01 | 03 | 1 | COACH-01 | smoke | `pytest tests/test_admin_coaches.py -x` | Wave 0 | pending |
| 10-03-02 | 03 | 1 | GUARD-02 | integration | `pytest tests/test_coaching_models.py::TestCrudGuardrail -x` | exists | pending |
| 10-03-03 | 03 | 1 | GUARD-03 | integration | `pytest tests/test_coaching_models.py::TestCrudGuardrail -x` | exists | pending |
| 10-03-04 | 03 | 1 | GUARD-04 | integration | `pytest tests/test_coaching_models.py::TestCrudGuardrail -x` | exists | pending |
| 10-03-05 | 03 | 1 | GUARD-05 | integration | `pytest tests/test_coaching_models.py::TestCrudGuardrail -x` | exists | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_admin_personality.py` — stubs for ADMN-01, ADMN-03, ADMN-04, ADMN-05
- [ ] `tests/test_admin_gear.py` — stubs for ADMN-06
- [ ] `tests/test_admin_coaches.py` — stubs for COACH-01

*Existing infrastructure covers ADMN-02, GEAR-01, GEAR-02, GUARD-02 through GUARD-05.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Re-extraction CLI copy display | ADMN-05 | UI interaction pattern | Navigate to personality edit page, verify copyable CLI command is displayed |
| Guardrail soft-delete hides from active but shows in admin | GUARD-05 | Visual verification | Delete a guardrail, verify it disappears from chat pipeline but remains in admin with deleted indicator |

---

## Validation Sign-Off

- [ ] All tasks have automated verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
