---
phase: 08-personality-extraction
plan: 01
subsystem: database
tags: [postgres, sql, migration, pydantic, pytest, trafilatura, pdfplumber]

# Dependency graph
requires:
  - phase: 07-data-foundation
    provides: personality_profile table with initial schema and upsert_personality_profile() model function

provides:
  - migrations/012_personality_extraction_fields.sql — adds 3 extraction columns, fixes constraints, creates evidence table
  - migrations/apply_migration_012.py — standalone apply script following project pattern
  - tests/test_personality_extraction.py — 10 RED test stubs defining expected extraction behavior
  - requirements-dev.txt updated with trafilatura==2.0.0 and pdfplumber==0.11.9
  - models.py upsert_personality_profile() ON CONFLICT updated for new 3-column unique

affects:
  - 08-02 (whatsapp extraction): needs migration applied, test scaffolds, and PersonalityExtraction model
  - 08-03 (blog extraction + merge): needs evidence table, pdfplumber, fetch_blog_text tests

# Tech tracking
tech-stack:
  added:
    - trafilatura==2.0.0 (HTML text extraction for blog posts)
    - pdfplumber==0.11.9 (PDF text extraction for ride reports)
  patterns:
    - Per-source personality rows: UNIQUE (rider_id, profile_type, extraction_source) allows separate whatsapp/blog/merged rows
    - Evidence table pattern: personality_trait_evidence stores 3-5 supporting quotes per trait for auditability
    - TDD RED scaffolds: test file written before implementation modules exist — ImportError is intentional

key-files:
  created:
    - migrations/012_personality_extraction_fields.sql
    - migrations/apply_migration_012.py
    - tests/test_personality_extraction.py
  modified:
    - requirements-dev.txt (added trafilatura, pdfplumber)
    - pytest.ini (registered 'integration' marker)
    - models.py (updated ON CONFLICT target in upsert_personality_profile)

key-decisions:
  - "UNIQUE constraint changed from (rider_id, profile_type) to (rider_id, profile_type, extraction_source) to allow separate per-source rows that merge script can read independently"
  - "extraction_source CHECK expanded to include 'merged' for the combined output of merge_profiles()"
  - "Integration tests stubbed with pytest.skip — require live DB after migration 012 applied"
  - "TDD RED approach: test file imported from scripts.personality_helpers and scripts.extract_personality_blog which don't exist yet — intentional RED state"

patterns-established:
  - "Migration idempotency: all ALTER TABLE use IF NOT EXISTS / IF EXISTS; CREATE TABLE uses IF NOT EXISTS"
  - "Per-source profile rows: extraction scripts write source-specific rows; merge script writes merged row with extraction_source='merged'"
  - "Test scaffolds precede implementation: write test behavior contract before writing production code"

requirements-completed: [EXTR-01, EXTR-03, EXTR-04, EXTR-05, EXTR-06]

# Metrics
duration: 3min
completed: 2026-03-17
---

# Phase 8 Plan 01: Schema Extension and Test Scaffolds Summary

**Migration 012 adds 3 extraction columns and evidence table to personality_profile; 10 RED test stubs define the full extraction contract for Plans 02 and 03**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-03-17T12:33:50Z
- **Completed:** 2026-03-17T12:37:13Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- Migration SQL adds `response_length_tendency`, `question_asking_behavior`, `domain_bias` columns; relaxes `extraction_source` CHECK to include `'merged'`; changes UNIQUE constraint to `(rider_id, profile_type, extraction_source)`; creates `personality_trait_evidence` table with two indexes
- Updated `upsert_personality_profile()` ON CONFLICT target in models.py from `(rider_id, profile_type)` to `(rider_id, profile_type, extraction_source)` to match the new constraint
- Test scaffolds define behavior contracts for all EXTR requirements: noise filtering, confidence thresholds, OpenAI mock, blog/WhatsApp merge logic, trafilatura/pdfplumber mocking, and 3 integration stubs for DB verification

## Task Commits

Each task was committed atomically:

1. **Task 1: Migration 012 and apply script** - `628efed` (feat)
2. **Task 2: Test scaffolds and dev dependencies** - `639063d` (test)

## Files Created/Modified

- `migrations/012_personality_extraction_fields.sql` — Idempotent SQL adding 3 columns, fixing constraints, creating evidence table
- `migrations/apply_migration_012.py` — Standalone apply script following apply_migration_011.py pattern
- `tests/test_personality_extraction.py` — 10 test functions (7 unit, 3 integration stubs)
- `requirements-dev.txt` — Added trafilatura==2.0.0 and pdfplumber==0.11.9
- `pytest.ini` — Registered 'integration' marker
- `models.py` — Updated ON CONFLICT target in upsert_personality_profile()

## Decisions Made

- Changed UNIQUE from `(rider_id, profile_type)` to `(rider_id, profile_type, extraction_source)`: extraction scripts need to write separate whatsapp and blog rows before the merge step reads both
- `extraction_source` CHECK expanded to `('whatsapp', 'blog', 'manual', 'merged')`: merge output needs its own source value to distinguish from raw extraction rows
- Integration tests use `pytest.skip` rather than blank stubs so they show as skipped (not erroring) until a live DB is available post-migration

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

**Migration 012 must be applied to production DB before Plans 02/03 can write extraction data.**

Run: `python3 migrations/apply_migration_012.py` with `DATABASE_URL` set.

After applying, integration tests can be run with: `pytest -m integration tests/test_personality_extraction.py`

## Next Phase Readiness

- Schema is ready: all columns, evidence table, and constraint changes in migration SQL
- Test contracts defined: Plans 02 and 03 know exactly what behavior to implement
- Plans 02 (WhatsApp extraction) and 03 (blog extraction + merge) can proceed in parallel after this migration is applied

---
*Phase: 08-personality-extraction*
*Completed: 2026-03-17*
