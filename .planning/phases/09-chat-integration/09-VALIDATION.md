---
phase: 9
slug: chat-integration
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-18
---

# Phase 9 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.3.x |
| **Config file** | pytest.ini |
| **Quick run command** | `python3 -m pytest tests/test_chat_integration.py -x -q` |
| **Full suite command** | `python3 -m pytest tests/ -x -q` |
| **Estimated runtime** | ~3 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/test_chat_integration.py -x -q`
- **After every plan wave:** Run `python3 -m pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 3 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 09-01-01 | 01 | 1 | COACH-02, COACH-03 | unit | `pytest tests/test_chat_integration.py::test_select_coach_routes_by_domain -x` | ❌ W0 | ⬜ pending |
| 09-01-02 | 01 | 1 | COACH-04 | unit | `pytest tests/test_chat_integration.py::test_fallback_coach -x` | ❌ W0 | ⬜ pending |
| 09-01-03 | 01 | 1 | COACH-05 | unit | `pytest tests/test_chat_integration.py::test_new_coach_no_code_change -x` | ❌ W0 | ⬜ pending |
| 09-02-01 | 02 | 1 | GUARD-07 | unit | `pytest tests/test_chat_integration.py::test_guardrails_injected_as_xml -x` | ❌ W0 | ⬜ pending |
| 09-02-02 | 02 | 1 | GUARD-07 | unit | `pytest tests/test_chat_integration.py::test_guardrail_db_change_no_redeploy -x` | ❌ W0 | ⬜ pending |
| 09-03-01 | 03 | 2 | GEAR-03 | unit | `pytest tests/test_chat_integration.py::test_gear_context_injected -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_chat_integration.py` — stubs for COACH-02 through COACH-05, GUARD-07, GEAR-03
- [ ] Shared fixtures for mocked DB rows (coach_assignment, coaching_guardrail, gear_preference)

*Existing pytest infrastructure covers framework needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live routing feels natural | COACH-02 | Subjective quality | Send "What tires for 600km?" and "How to train for PBP?" — verify different coach persona responds |
| Guardrail change propagates | GUARD-07 | Requires live DB mutation | Toggle a guardrail row in Supabase, send matching message, verify new behavior |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 3s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
