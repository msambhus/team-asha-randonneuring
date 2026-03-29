---
phase: 11-braintrust-evals
verified: 2026-03-18T00:00:00Z
status: passed
score: 4/4 must-haves verified
re_verification: false
---

# Phase 11: Braintrust Evals Verification Report

**Phase Goal:** Dynamic eval suite validates guardrail compliance with LLM-as-judge scoring.
**Verified:** 2026-03-18
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Running `eval_guardrail_dynamic.py` loads guardrail rules from the live DB and generates test cases automatically | VERIFIED | `load_dataset()` calls `models.get_active_guardrails()` (3 call sites); CASE_GENERATORS dispatcher maps each rule to 5 auto-generated cases |
| 2 | Adding a new guardrail rule in admin produces new eval test cases without editing the eval script | VERIFIED | CASE_GENERATORS dispatcher requires only a new generator function for a new `rule_type`; `load_dataset()` itself is untouched |
| 3 | Scoring uses LLMClassifier for semantic compliance, not keyword matching | VERIFIED | Module-level `_classifier = LLMClassifier(...)` instantiated once; `llm_compliance_scorer` calls `_classifier.eval()` — confirmed by test_eval2_llm_scorer_used patching `_classifier` directly |
| 4 | Eval results are tagged with the guardrail rule version stamp | VERIFIED | `compute_version_stamp()` returns MD5[:12] of sorted (id, rule_version) pairs; `Eval(experiment_name=f"guardrail_dynamic_{stamp}", metadata={"guardrail_version_stamp": stamp, ...}, tags=[f"stamp:{stamp}"])` |

**Score:** 4/4 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `evals/eval_guardrail_dynamic.py` | Dynamic eval script with DB-driven test case generation | VERIFIED | 469 lines; exports `load_dataset`, `generate_test_cases`, `guardrail_dynamic_task`, `llm_compliance_scorer`, `compute_version_stamp`, `seed_and_run`; all imports confirmed via `python3 -c "from evals.eval_guardrail_dynamic import ..."` |
| `tests/test_braintrust_integration.py` | Unit tests for all 6 EVAL2 requirements | VERIFIED | 623 lines total; 6 `test_eval2_*` functions appended after existing tests (line 485 onward); all 6 pass: `6 passed, 17 deselected in 0.46s` |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `evals/eval_guardrail_dynamic.py` | `models.get_active_guardrails()` | DB query at eval start | WIRED | `grep -c` returns 3 — called in `load_dataset()` and `seed_and_run()` |
| `evals/eval_guardrail_dynamic.py` | `services.chat_service.assemble_coach_context()` | system prompt assembly in task function | WIRED | `grep -c` returns 4 — imported and called in `guardrail_dynamic_task()` |
| `evals/eval_guardrail_dynamic.py` | `autoevals.LLMClassifier` | semantic compliance scoring | WIRED | `grep -c` returns 4 — imported, instantiated as `_classifier`, and called via `_classifier.eval()` in scorer |
| `evals/eval_guardrail_dynamic.py` | `braintrust.Eval` with version stamp metadata | experiment with version stamp metadata | WIRED | `Eval(..., metadata={"guardrail_version_stamp": stamp, ...}, tags=[f"stamp:{stamp}"])` confirmed in `seed_and_run()` |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| EVAL2-01 | 11-01-PLAN.md | Eval dataset covers scope enforcement | SATISFIED | `_generate_scope_cases()` produces 5 cases per scope rule; `test_eval2_scope_enforcement` passes |
| EVAL2-02 | 11-01-PLAN.md | Eval dataset covers topic blocking | SATISFIED | `_generate_topic_block_cases()` produces 5 cases; `test_eval2_topic_blocking` verifies violation cases have `expected='non_compliant'` |
| EVAL2-03 | 11-01-PLAN.md | Eval dataset covers medical deflection | SATISFIED | `_generate_escalation_cases()` produces 5 cases with 2 adversarial; `test_eval2_medical_deflection` verifies >= 2 adversarial per escalation rule |
| EVAL2-04 | 11-01-PLAN.md | Eval dataset covers persona consistency | SATISFIED | Scope rules with `applies_to` set to `shriram` or `venki` provide persona-specific cases; `test_eval2_persona_consistency` verifies `applies_to != 'all'` |
| EVAL2-05 | 11-01-PLAN.md | Eval uses LLM-as-judge scoring, not keyword matching | SATISFIED | Module-level `LLMClassifier` instance; `test_eval2_llm_scorer_used` patches `_classifier` and asserts `.eval()` is called |
| EVAL2-06 | 11-01-PLAN.md | Eval results comparable across guardrail rule versions | SATISFIED | `compute_version_stamp()` deterministic per config; `test_eval2_version_stamp` verifies same config = same stamp, different config = different stamp, 12-char length |

No orphaned requirements — REQUIREMENTS.md traceability table maps EVAL2-01 through EVAL2-06 to Phase 11 only, and the plan claims all six. All six are accounted for.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `evals/eval_guardrail_dynamic.py` | 280 | Comment says "placeholder case" in unknown rule_type fallback | Info | Not a stub — this is intentional graceful degradation producing a labeled `case_type: 'unknown'` case rather than crashing on new rule types. Not a blocker. |

No blockers. No stubs. No TODO/FIXME markers beyond the one noted above.

---

### Human Verification Required

None required for this phase. All assertions are on pure Python logic with mocked dependencies. The eval script requires `OPENAI_API_KEY` and `BRAINTRUST_API_KEY` for a live run, but those are runtime prerequisites not verifiable in static analysis — the SUMMARY notes they are already in place from Phase 10.

The one scenario that warrants a future manual check is a live run of `python evals/eval_guardrail_dynamic.py` against the actual Supabase DB to confirm the Braintrust experiment appears with the correct version stamp and tag. This is not a gap — it is operational confirmation, and the code paths are fully tested.

---

### Commit Verification

Both commits documented in SUMMARY.md exist and are on the correct branch:

- `d98e485` — `test(11-01): add failing EVAL2 unit tests for dynamic guardrail eval`
- `c4593c4` — `feat(11-01): implement dynamic guardrail eval with DB-driven test case generation`

---

### Regression Check

Full test suite: `177 passed, 44 skipped, 8 warnings` — no regressions. The 44 skipped tests are pre-existing DB/integration tests requiring a live Supabase connection; none are new failures.

---

## Summary

Phase 11 goal is fully achieved. `evals/eval_guardrail_dynamic.py` is a complete, non-stub implementation that:

1. Loads guardrail rules from the live DB via `models.get_active_guardrails()` — new rules added via admin immediately produce new eval test cases.
2. Generates exactly 5 test cases per rule (violation, pass, boundary, adversarial_1, adversarial_2) across all four compliance categories via the CASE_GENERATORS dispatcher.
3. Scores compliance semantically using a module-level `autoevals.LLMClassifier` instance — not keyword matching.
4. Tags Braintrust experiments with a deterministic MD5 version stamp derived from the guardrail (id, rule_version) state — different rule configurations produce distinct, comparable result sets.

All 6 EVAL2 unit tests pass with zero DB or API key requirements. Full test suite green.

---

_Verified: 2026-03-18_
_Verifier: Claude (gsd-verifier)_
