---
phase: 8
slug: personality-extraction
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-17
---

# Phase 8 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.3.0 |
| **Config file** | `pytest.ini` |
| **Quick run command** | `pytest tests/test_personality_extraction.py -x` |
| **Full suite command** | `pytest tests/ -x` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_personality_extraction.py -x`
- **After every plan wave:** Run `pytest tests/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 08-01-01 | 01 | 1 | EXTR-03 | unit | `pytest tests/test_personality_extraction.py::test_extraction_model_fields -x` | ❌ W0 | ⬜ pending |
| 08-01-02 | 01 | 1 | Schema | integration | `pytest tests/test_personality_extraction.py::test_migration_012_columns -x` | ❌ W0 | ⬜ pending |
| 08-01-03 | 01 | 1 | Schema | integration | `pytest tests/test_personality_extraction.py::test_trait_evidence_schema -x` | ❌ W0 | ⬜ pending |
| 08-01-04 | 01 | 1 | EXTR-05 | unit | `pytest tests/test_personality_extraction.py::test_group_by_sender_filters_noise -x` | ❌ W0 | ⬜ pending |
| 08-01-05 | 01 | 1 | EXTR-06 | unit | `pytest tests/test_personality_extraction.py::test_compute_confidence -x` | ❌ W0 | ⬜ pending |
| 08-01-06 | 01 | 1 | EXTR-01 | unit | `pytest tests/test_personality_extraction.py::test_extract_from_messages_returns_model -x` | ❌ W0 | ⬜ pending |
| 08-02-01 | 02 | 1 | EXTR-02 | unit | `pytest tests/test_personality_extraction.py::test_fetch_blog_text_from_url -x` | ❌ W0 | ⬜ pending |
| 08-02-02 | 02 | 1 | EXTR-02 | unit | `pytest tests/test_personality_extraction.py::test_extract_pdf_text -x` | ❌ W0 | ⬜ pending |
| 08-02-03 | 02 | 1 | EXTR-04 | integration | `pytest tests/test_personality_extraction.py::test_evidence_quotes_stored -x` | ❌ W0 | ⬜ pending |
| 08-03-01 | 03 | 2 | EXTR-07 | unit | `pytest tests/test_personality_extraction.py::test_merge_profiles_blog_wins -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_personality_extraction.py` — all unit and integration test stubs
- [ ] `migrations/012_personality_extraction_fields.sql` — add 3 missing columns + evidence table + fix extraction_source CHECK
- [ ] `migrations/apply_migration_012.py` — standalone apply script
- [ ] Install new libs: `pip install trafilatura==2.0.0 pdfplumber==0.11.9` + add to `requirements-dev.txt`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Extraction prompt quality | EXTR-01, EXTR-02 | LLM output quality is empirical | Run script on real WhatsApp data, review JSON output with admin |
| Venki PDF accessibility | EXTR-02 | Google Drive permissions vary | Download PDF manually, pass local path to script |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
