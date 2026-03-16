---
phase: 05-whatsapp-knowledge-base
plan: 02
subsystem: data-pipeline
tags: [pgvector, openai-embeddings, whatsapp, import-script, vector-db, incremental-append, supabase]

# Dependency graph
requires:
  - phase: 05-whatsapp-knowledge-base
    provides: "WhatsApp parser, chunker, two-stage filter, formatter (Plan 01)"
provides:
  - "whatsapp_chunk table with vector(1536) column and HNSW cosine similarity index"
  - "CLI import script (import_whatsapp.py) with two-stage filtering and incremental append"
  - "Idempotent re-import via UNIQUE constraint on (source, chunk_start, chunk_end)"
  - "Dev dependencies (pgvector, numpy, tqdm) isolated from production requirements"
affects: [05-03-PLAN, rag-retrieval, knowledge-base]

# Tech tracking
tech-stack:
  added: ["pgvector==0.4.2 (dev)", "numpy (dev)", "tqdm (dev)"]
  patterns: ["pgvector HNSW index for cosine similarity", "incremental append via MAX(chunk_end) per source", "two-stage filtering pipeline (rule-based + LLM)", "ON CONFLICT DO NOTHING for idempotent re-import", "dev-only dependencies in requirements-dev.txt"]

key-files:
  created:
    - schema/whatsapp_schema.sql
    - scripts/import_whatsapp.py
  modified:
    - requirements-dev.txt

key-decisions:
  - "HNSW index over IVFFlat -- works on empty tables, better recall at expected scale (~22k rows)"
  - "Dev-only dependencies (pgvector, numpy, tqdm) in requirements-dev.txt, not production requirements.txt (WA-10)"
  - "UNIQUE constraint on (source, chunk_start, chunk_end) for idempotent re-import (WA-06)"
  - "Incremental append via MAX(chunk_end) query per source -- re-imports only process new messages"
  - "Two-stage filter in import: rule-based first, then LLM classification for non-keyword chunks"
  - "6000-token safety cap (24000 chars) on chunk text before embedding API call"

patterns-established:
  - "Offline CLI scripts in scripts/ directory with argparse, DATABASE_URL from env/.env, progress reporting"
  - "Schema SQL files in schema/ directory with IF NOT EXISTS for idempotent application"
  - "execute_values batch insert with pgvector register_vector for numpy array embedding columns"

requirements-completed: [WA-04, WA-05, WA-06, WA-10]

# Metrics
duration: 4min
completed: 2026-03-16
---

# Phase 5 Plan 02: pgvector Schema and Import Script Summary

**pgvector schema with HNSW cosine index and CLI import script implementing parse-chunk-filter(two-stage)-embed-insert pipeline with incremental append and idempotent re-import**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-16T05:25:00Z
- **Completed:** 2026-03-16T05:29:00Z
- **Tasks:** 2 (1 auto + 1 checkpoint:human-verify)
- **Files created:** 3

## Accomplishments
- `schema/whatsapp_schema.sql` (32 lines) defines whatsapp_chunk table with vector(1536) embedding column, HNSW index (m=16, ef_construction=64), source index, and UNIQUE constraint for idempotent re-import
- `scripts/import_whatsapp.py` (384 lines) implements full CLI pipeline: parse WhatsApp export, incremental filter (only new messages after MAX(chunk_end)), chunk by 30-min windows, two-stage filtering (rule-based + LLM batch classification), embed with text-embedding-3-small, batch insert with execute_values
- Dry-run mode works without OPENAI_API_KEY or DB connection (with --skip-llm-filter), enabling safe testing of parse/chunk/filter stages
- Dev dependencies (pgvector, numpy, tqdm) added to requirements-dev.txt only; production requirements.txt unchanged (WA-10)

## Task Commits

Each task was committed atomically:

1. **Task 1: pgvector schema and import script with two-stage filtering and incremental append** - `517c6cf` (feat)
2. **Task 2: Verify schema migration and import script dry-run** - checkpoint:human-verify (approved, no commit needed)

## Files Created/Modified
- `schema/whatsapp_schema.sql` (32 lines) - pgvector table definition with vector(1536) column, HNSW index, source index, and UNIQUE constraint
- `scripts/import_whatsapp.py` (384 lines) - CLI import script with argparse, two-stage filtering, incremental append, batch embedding, and idempotent insert
- `requirements-dev.txt` - Added pgvector==0.4.2, numpy, tqdm as dev-only dependencies

## Decisions Made
- Chose HNSW index over IVFFlat because HNSW works on empty tables and offers better recall at the expected scale (~22k rows from WhatsApp exports)
- Dev-only dependencies isolated in requirements-dev.txt; production retrieval function in Plan 03 uses plain Python float lists with no numpy dependency
- UNIQUE constraint on (source, chunk_start, chunk_end) ensures idempotent re-import -- ON CONFLICT DO NOTHING skips duplicates
- Incremental append queries MAX(chunk_end) per source so re-imports only process messages newer than the last import
- Two-stage filter in import pipeline: Stage 1 (rule-based keywords) catches obvious cycling content; Stage 2 (LLM classification) recovers cycling-relevant chunks that lack keywords
- 6000-token safety cap (approximately 24000 characters) on chunk text before sending to embedding API

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
**External services require manual configuration:**
- pgvector extension must be enabled in Supabase Dashboard (Database -> Extensions -> vector -> Enable)
- Schema SQL must be run in Supabase SQL editor before import script can insert data
- OPENAI_API_KEY environment variable required for embedding generation (not needed for --dry-run --skip-llm-filter)

## Next Phase Readiness
- whatsapp_chunk table ready to receive embedded chunks via import script
- Plan 03 (RAG retrieval) depends on this table existing with HNSW index for cosine similarity search
- Import script can be run with `python scripts/import_whatsapp.py --source fresh_start --path /tmp/wa_explore/fresh_start/_chat.txt` after schema is applied

## Self-Check: PASSED

- Files: schema/whatsapp_schema.sql FOUND, scripts/import_whatsapp.py FOUND, requirements-dev.txt FOUND
- Commits: 517c6cf (Task 1) FOUND
- 05-02-SUMMARY.md exists at expected path

---
*Phase: 05-whatsapp-knowledge-base*
*Completed: 2026-03-16*
