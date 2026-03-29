---
phase: 08-personality-extraction
plan: 03
subsystem: extraction-scripts
tags: [python, trafilatura, pdfplumber, openai, gpt-4o, blog, pdf, merge, cli]

# Dependency graph
requires:
  - phase: 08-01
    provides: migration 012 (personality_profile 3 new columns + personality_trait_evidence table)
  - phase: 08-02
    provides: personality_helpers.py (PersonalityExtraction, store_extraction_results, store_evidence, compute_confidence, merge_profiles)

provides:
  - scripts/extract_personality_blog.py — full CLI script for blog URL + PDF personality extraction
  - scripts/merge_personality.py — CLI script to merge multi-source personality profiles

affects:
  - Phase 9 (chat integration): merged profiles available in personality_profile table for coach context assembly

# Tech tracking
tech-stack:
  added: [trafilatura, pdfplumber]
  patterns:
    - Blog extraction prompt: BLOG_EXTRACTION_PROMPT — adapted from EXTRACTION_SYSTEM_PROMPT for longer-form writing analysis
    - Placeholder module pattern: ImportError fallback creates ModuleType with stub callables so mock.patch works without library installed
    - Message-equivalent count: word_count // 50 used as proxy for blog confidence calculation
    - Evidence merge: DELETE merged evidence then INSERT copies from both whatsapp+blog sources with extraction_source='merged'
    - Google Drive URL warning: preemptive warning about potential auth issues before fetch attempt

key-files:
  created:
    - scripts/merge_personality.py
  modified:
    - scripts/extract_personality_blog.py (stub -> full implementation)
    - scripts/personality_helpers.py (merge_profiles already added in Plan 02)

key-decisions:
  - "Blog extraction uses a separate BLOG_EXTRACTION_PROMPT rather than EXTRACTION_SYSTEM_PROMPT — blog text is a single document, not a message list"
  - "Word count / 50 used as message-equivalent for confidence calculation — approximate but consistent with compute_confidence() thresholds"
  - "merge_personality.py always requires DATABASE_URL even for --dry-run — needs to read source profiles from DB to merge"
  - "Evidence merge copies quotes from both sources re-tagged as 'merged' rather than creating new evidence"

requirements-completed: [EXTR-02, EXTR-07]

# Metrics
duration: ~3min
completed: 2026-03-18
---

# Phase 8 Plan 03: Blog Extraction + Merge Script Summary

**Blog/PDF personality extraction via trafilatura + pdfplumber, multi-source merge with blog priority, CLI scripts with dry-run support**

## Performance

- **Duration:** ~3 min
- **Completed:** 2026-03-18
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- `scripts/extract_personality_blog.py` — 340 lines. Full CLI with --url (trafilatura), --pdf-path (pdfplumber), --rider-name, --profile-type, --dry-run. Blog-specific BLOG_EXTRACTION_PROMPT adapted for longer-form writing. Google Drive URL warning. Word count / 50 message-equivalent for confidence. Stores results with extraction_source='blog'.
- `scripts/merge_personality.py` — 211 lines. CLI with --rider-name, --profile-type, --dry-run. Reads whatsapp + blog rows from personality_profile, calls merge_profiles() from personality_helpers.py. Upserts merged row with extraction_source='merged'. Copies evidence from both sources. Prints detailed merge summary.
- `scripts/personality_helpers.py` — merge_profiles() function (already created in Plan 02): blog wins on enum fields, signature_phrases unioned and capped at 5, lower confidence, summed message counts.

## Task Commits

All code was already implemented in prior iterations (Plans 08-01 and 08-02). This plan documents the completed state.

## Files Created/Modified

- `scripts/extract_personality_blog.py` — full blog/PDF extraction CLI (was stub from Plan 02)
- `scripts/merge_personality.py` — multi-source merge CLI
- `scripts/personality_helpers.py` — merge_profiles() function

## Verification

- All 7 unit tests pass in test_personality_extraction.py (3 integration stubs skip as expected)
- Full suite: 152 passed, 26 skipped
- `python scripts/extract_personality_blog.py --help` shows all expected options
- `python scripts/merge_personality.py --help` shows all expected options
- merge_profiles() importable from scripts.personality_helpers

## Success Criteria Validation

- Blog extraction handles WordPress URLs (trafilatura.fetch_url + extract) and local PDFs (pdfplumber.open + extract_text)
- Google Drive URLs produce auth warning before fetch attempt
- Merge uses blog priority for all enum fields, unions signature_phrases capped at 5
- Merged confidence is the lower of the two source confidences
- Original whatsapp and blog rows preserved — merge creates new 'merged' row via ON CONFLICT upsert
- All 3 scripts (whatsapp, blog, merge) support --dry-run

---
*Phase: 08-personality-extraction*
*Completed: 2026-03-18*
