---
phase: 7
slug: data-foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-17
---

# Phase 7 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (configured in pytest.ini) |
| **Config file** | `pytest.ini` — `testpaths = tests`, `python_files = test_*.py` |
| **Quick run command** | `pytest tests/test_coaching_models.py -x` |
| **Full suite command** | `pytest tests/ -x` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_system_prompt.py -x`
- **After every plan wave:** Run `pytest tests/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 07-01-01 | 01 | 1 | PROF-01 | integration | `pytest tests/test_coaching_models.py::test_personality_profile_schema -x` | ❌ W0 | ⬜ pending |
| 07-01-02 | 01 | 1 | PROF-02 | integration | `pytest tests/test_coaching_models.py::test_coach_profile_fields -x` | ❌ W0 | ⬜ pending |
| 07-01-03 | 01 | 1 | PROF-03 | integration | `pytest tests/test_coaching_models.py::test_rider_profile_fields -x` | ❌ W0 | ⬜ pending |
| 07-01-04 | 01 | 1 | PROF-04 | integration | `pytest tests/test_coaching_models.py::test_extraction_metadata -x` | ❌ W0 | ⬜ pending |
| 07-01-05 | 01 | 1 | PROF-05 | integration | `pytest tests/test_coaching_models.py::test_profile_audit_columns -x` | ❌ W0 | ⬜ pending |
| 07-01-06 | 01 | 1 | GUARD-01 | integration | `pytest tests/test_coaching_models.py::test_guardrail_schema -x` | ❌ W0 | ⬜ pending |
| 07-01-07 | 01 | 1 | GUARD-06 | integration | `pytest tests/test_coaching_models.py::test_guardrail_version_increment -x` | ❌ W0 | ⬜ pending |
| 07-02-01 | 02 | 1 | PROF-01 | unit | `pytest tests/test_coaching_models.py::test_crud_personality_profile -x` | ❌ W0 | ⬜ pending |
| 07-02-02 | 02 | 1 | GUARD-01 | unit | `pytest tests/test_coaching_models.py::test_crud_guardrail -x` | ❌ W0 | ⬜ pending |
| 07-03-01 | 03 | 2 | PROF-02 | integration | `pytest tests/test_coaching_models.py::test_seed_shriram_profile -x` | ❌ W0 | ⬜ pending |
| 07-03-02 | 03 | 2 | PROF-02 | integration | `pytest tests/test_coaching_models.py::test_seed_venki_profile -x` | ❌ W0 | ⬜ pending |
| regression | - | - | (preservation) | unit | `pytest tests/test_system_prompt.py -x` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_coaching_models.py` — stubs for all schema, CRUD, and seed data tests (covers PROF-01 through PROF-05, GUARD-01, GUARD-06)

*Wave 0 must create this file before plans 07-01 / 07-02 begin implementation.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Migration applies cleanly to live Supabase | PROF-01 | Requires live DB credentials | Run `python3 migrations/apply_migration_standalone.py` against Supabase |
| Seed data matches CHAT_SYSTEM_PROMPT personality descriptions | PROF-02 | Semantic comparison | Compare seed values to openai_coach.py lines 159-248 |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
