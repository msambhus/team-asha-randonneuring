---
phase: 12
slug: knowledge-base-expansion
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-18
---

# Phase 12 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | tests/conftest.py |
| **Quick run command** | `python3 -m pytest tests/test_embed_resources.py tests/test_knowledge_admin.py -x -q` |
| **Full suite command** | `python3 -m pytest tests/ -x -q` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/test_embed_resources.py tests/test_knowledge_admin.py -x -q`
- **After every plan wave:** Run `python3 -m pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 12-01-01 | 01 | 1 | KB-01 | unit | `python3 -m pytest tests/test_embed_resources.py::test_csv_url_parsing -x -q` | ❌ W0 | ⬜ pending |
| 12-01-02 | 01 | 1 | KB-02 | unit | `python3 -m pytest tests/test_embed_resources.py::test_content_extraction -x -q` | ❌ W0 | ⬜ pending |
| 12-01-03 | 01 | 1 | KB-03 | unit | `python3 -m pytest tests/test_embed_resources.py::test_source_prefix -x -q` | ❌ W0 | ⬜ pending |
| 12-01-04 | 01 | 1 | KB-03 | unit | `python3 -m pytest tests/test_embed_resources.py::test_deduplication -x -q` | ❌ W0 | ⬜ pending |
| 12-02-01 | 02 | 2 | KB-04 | unit | `python3 -m pytest tests/test_knowledge_admin.py::test_source_list -x -q` | ❌ W0 | ⬜ pending |
| 12-02-02 | 02 | 2 | KB-05 | unit | `python3 -m pytest tests/test_knowledge_admin.py::test_reembed -x -q` | ❌ W0 | ⬜ pending |
| 12-02-03 | 02 | 2 | KB-06 | unit | `python3 -m pytest tests/test_knowledge_admin.py::test_remove_source -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_embed_resources.py` — stubs for KB-01, KB-02, KB-03
- [ ] `tests/test_knowledge_admin.py` — stubs for KB-04, KB-05, KB-06

*Existing infrastructure covers test framework; only test files needed.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Admin page renders correctly with source list | KB-04 | Visual layout check | Navigate to /admin/knowledge, verify table shows sources with counts |
| Re-embed button triggers correct script flow | KB-05 | Requires live DB + OpenAI API | Click re-embed, verify chunk count updates |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
