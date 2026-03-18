---
phase: 11-braintrust-evals
plan: 01
subsystem: testing
tags: [braintrust, autoevals, llmclassifier, guardrails, evals, openai]

# Dependency graph
requires:
  - phase: 09
    provides: assemble_coach_context(), get_active_guardrails(), DB-driven guardrail rules
  - phase: 10
    provides: coaching_guardrail admin, rule CRUD that eval now auto-detects

provides:
  - DB-driven eval script that auto-generates test cases from live guardrail rules
  - LLMClassifier-based semantic compliance scoring (not keyword matching)
  - Version stamp tagging for experiment reproducibility
  - 6 unit tests covering all EVAL2 requirements (no DB or API key needed)

affects:
  - phase-12
  - future eval scripts (pattern for DB-driven test case generation)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - DB-driven eval dataset: load_dataset() queries DB, dispatches to per-rule-type generators
    - Module-level LLMClassifier instance: instantiated ONCE at import, not per-call
    - Version stamp: sorted (id, rule_version) pairs -> JSON -> MD5[:12] for deterministic naming
    - 5-case-per-rule pattern: violation, pass, boundary, adversarial_1, adversarial_2
    - Patch _classifier directly in tests (not LLMClassifier class) — module-level instance workaround

key-files:
  created:
    - evals/eval_guardrail_dynamic.py
  modified:
    - tests/test_braintrust_integration.py

key-decisions:
  - "Patching _classifier (module-level instance) rather than LLMClassifier class — avoids re-import/reload complexity in tests"
  - "5 test cases per rule: violation/pass/boundary/adversarial_1/adversarial_2 — covers happy path, edge, and adversarial jailbreak attempts"
  - "CASE_GENERATORS dict dispatcher pattern — adding a new rule_type just needs a new generator function, no changes to load_dataset()"

patterns-established:
  - "CASE_GENERATORS dispatcher: dict mapping rule_type -> generator function, generator returns exactly 5 cases"
  - "metadata dict per case: rule_id, rule_version, rule_type, rule_value, applies_to, case_type — fully traceable to DB row"
  - "Unit tests mock models.get_active_guardrails() only — no Flask app context or real DB needed"

requirements-completed: [EVAL2-01, EVAL2-02, EVAL2-03, EVAL2-04, EVAL2-05, EVAL2-06]

# Metrics
duration: 3min
completed: 2026-03-18
---

# Phase 11 Plan 01: Braintrust Dynamic Guardrail Evals Summary

**DB-driven guardrail eval using autoevals.LLMClassifier with 5 auto-generated cases per rule and MD5 version stamps for experiment reproducibility**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-18T10:18:01Z
- **Completed:** 2026-03-18T10:20:53Z
- **Tasks:** 2 (TDD: RED + GREEN)
- **Files modified:** 2

## Accomplishments

- Created `evals/eval_guardrail_dynamic.py` with all 5 exported functions: `load_dataset`, `generate_test_cases`, `guardrail_dynamic_task`, `llm_compliance_scorer`, `compute_version_stamp`
- CASE_GENERATORS dispatcher handles 4 rule types (scope, topic_block, escalation, tone_limit), each producing 5 test cases covering violation/pass/boundary/adversarial patterns
- LLMClassifier scorer uses a single module-level instance for semantic compliance (not keyword matching), with chain-of-thought reasoning via `use_cot=True`
- Version stamp computed from sorted (id, rule_version) pairs + MD5[:12] for deterministic, reproducible experiment names
- All 6 EVAL2 unit tests pass with zero DB or API key requirements (pure mocks)

## Task Commits

1. **Task 1: Create EVAL2 unit tests (RED)** - `d98e485` (test)
2. **Task 2: Implement eval_guardrail_dynamic.py (GREEN)** - `c4593c4` (feat)

## Files Created/Modified

- `evals/eval_guardrail_dynamic.py` - Dynamic eval script: DB loading, 4-type case generators, LLM scorer, version stamping, Braintrust wiring
- `tests/test_braintrust_integration.py` - Added 6 EVAL2 test functions (appended after existing tests)

## Decisions Made

- **Patch `_classifier` directly in tests** (not `LLMClassifier` class): The module-level `_classifier` instance is created at import time, so patching the class after import has no effect. Patching `evals.eval_guardrail_dynamic._classifier` directly is the correct approach and is semantically clear.
- **5-case pattern per rule**: violation/pass/boundary/adversarial_1/adversarial_2 — covers happy path, near-miss, and jailbreak-style adversarial inputs that try to bypass the guardrail with social engineering prefixes.
- **CASE_GENERATORS dispatcher dict**: Adding a new rule_type only requires adding a new generator function — `load_dataset()` needs no changes. This keeps extensibility simple and discoverable.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test_eval2_llm_scorer_used mock strategy**
- **Found during:** Task 2 (GREEN verification)
- **Issue:** Plan specified mocking `LLMClassifier` class and reloading the module. But the module-level `_classifier` instance is created at import time, so patching the class after import does not affect the already-instantiated `_classifier` — the test called real autoevals code which failed with missing API key.
- **Fix:** Changed test to patch `evals.eval_guardrail_dynamic._classifier` directly (the instance), which correctly intercepts the `.eval()` call.
- **Files modified:** tests/test_braintrust_integration.py
- **Verification:** `pytest tests/test_braintrust_integration.py -k eval2` — all 6 pass
- **Committed in:** c4593c4 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - bug in test strategy)
**Impact on plan:** Minor fix to test mock approach. No scope creep. All success criteria met.

## Issues Encountered

None beyond the test mock strategy fix documented above.

## User Setup Required

None — no external service configuration required. The eval script requires `OPENAI_API_KEY` and `BRAINTRUST_API_KEY` at runtime, but these are already in place from Phase 10.

## Next Phase Readiness

- Phase 11 complete: dynamic guardrail eval is fully functional
- Phase 12 (knowledge expansion: embed script, knowledge admin page) can begin immediately
- Running `python evals/eval_guardrail_dynamic.py` will execute the full eval against the live DB, auto-discovering any new guardrail rules added via admin

---
*Phase: 11-braintrust-evals*
*Completed: 2026-03-18*

## Self-Check: PASSED

- `evals/eval_guardrail_dynamic.py` — FOUND
- `tests/test_braintrust_integration.py` — FOUND
- `.planning/phases/11-braintrust-evals/11-01-SUMMARY.md` — FOUND
- Commit `d98e485` (test: RED) — FOUND
- Commit `c4593c4` (feat: GREEN) — FOUND
