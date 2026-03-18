---
phase: 11
slug: braintrust-evals
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-18
---

# Phase 11 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.3.0 |
| **Config file** | pytest.ini |
| **Quick run command** | `pytest tests/test_braintrust_integration.py -x -q` |
| **Full suite command** | `pytest tests/ -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_braintrust_integration.py -x -q`
- **After every plan wave:** Run `pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 11-01-01 | 01 | 1 | EVAL2-01 | unit | `pytest tests/test_braintrust_integration.py::test_eval2_scope_enforcement -x` | ❌ W0 | ⬜ pending |
| 11-01-02 | 01 | 1 | EVAL2-02 | unit | `pytest tests/test_braintrust_integration.py::test_eval2_topic_blocking -x` | ❌ W0 | ⬜ pending |
| 11-01-03 | 01 | 1 | EVAL2-03 | unit | `pytest tests/test_braintrust_integration.py::test_eval2_medical_deflection -x` | ❌ W0 | ⬜ pending |
| 11-01-04 | 01 | 1 | EVAL2-04 | unit | `pytest tests/test_braintrust_integration.py::test_eval2_persona_consistency -x` | ❌ W0 | ⬜ pending |
| 11-01-05 | 01 | 1 | EVAL2-05 | unit | `pytest tests/test_braintrust_integration.py::test_eval2_llm_scorer_used -x` | ❌ W0 | ⬜ pending |
| 11-01-06 | 01 | 1 | EVAL2-06 | unit | `pytest tests/test_braintrust_integration.py::test_eval2_version_stamp -x` | ❌ W0 | ⬜ pending |
| 11-02-01 | 02 | 1 | EVAL2-01-04 | integration | `pytest tests/test_braintrust_integration.py -x -q -k eval2` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_braintrust_integration.py` — add EVAL2 test stubs for all 6 requirements
- [ ] Seed rows in `coaching_guardrail` — at least one active row per `rule_type` if DB is empty

*Existing test infrastructure covers framework install.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Braintrust UI shows experiments with version stamps | EVAL2-06 | Requires live Braintrust dashboard | Run eval, check experiment_name and metadata in Braintrust UI |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
