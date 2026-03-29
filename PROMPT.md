# Ralph Wiggum Loop — Personality-Driven Coaching (Milestone 2)

You are working on the Team Asha Randonneuring chatbot project autonomously.

## Current Focus: Phases 7-12

Phases 1-6 are code complete (Milestone 1). You are now executing Milestone 2 phases in order.

### Phase 7: Data Foundation
**Goal:** DB schema for personality_profile, gear_preference, coach_assignment, coaching_guardrail tables + model functions + seed data for Shriram and Venki.
**Requirements:** PROF-01 through PROF-05, GUARD-01, GUARD-06

### Phase 8: Personality Extraction
**Goal:** CLI scripts extract personality traits per person from WhatsApp exports and blog posts using GPT-4o + instructor.
**Requirements:** EXTR-01 through EXTR-07

### Phase 9: Chat Integration
**Goal:** DB-driven coach routing replaces hardcoded _BIKE_KEYWORDS, guardrails loaded from DB, gear context injected.
**Requirements:** GUARD-07, COACH-02 through COACH-05, GEAR-03

### Phase 10: Admin UI
**Goal:** Admin pages for personality traits, gear preferences, coach config, and guardrails — all via existing admin blueprint.
**Requirements:** ADMN-01 through ADMN-06, GEAR-01, GEAR-02, COACH-01, GUARD-02 through GUARD-05

### Phase 11: Braintrust Evals
**Goal:** Dynamic eval suite validates guardrail compliance with LLM-as-judge scoring.
**Requirements:** EVAL2-01 through EVAL2-06

### Phase 12: Knowledge Base Expansion
**Goal:** Crawl resources spreadsheet URLs, embed content into pgvector, admin source management.
**Requirements:** KB-01 through KB-06

## Your Job Each Iteration

1. **Read current state**: `cat .planning/STATE.md`
2. **Read CLAUDE.md**: `cat CLAUDE.md` (git workflow rules, conventions)
3. **Determine what to do next**:
   - Use `/gsd:progress` to check current position and get routed to the next action
   - If a phase needs planning: run `/gsd:plan-phase <N>`
   - If a phase is planned: run `/gsd:execute-phase <N>`
   - The GSD workflow handles plan creation, execution, verification, and state updates
4. **Execute one phase or plan per iteration**:
   - Follow existing patterns in the codebase
   - Write clean, secure code
   - For DB changes: create migration scripts in `migrations/`
   - For scripts: add to `scripts/` directory
   - For admin pages: extend `routes/admin.py` and `templates/admin/`
5. **Verify your work**:
   - Run `python3 -m pytest tests/ -x -q`
   - Check for syntax errors, import issues
6. **Commit your changes**:
   - Use branch `feature/personality-coaching-admin`
   - `git add` specific files (never `git add .`)
   - Write descriptive commit messages
7. **Log to activity.md**: Append what you did, what worked, any issues
8. **Exit cleanly** — the loop will restart you with fresh context

## Rules

- **One plan per iteration.** Complete one plan file fully, then exit.
- **Never push to remote.** Only commit locally. The human will create PRs.
- **Read before writing.** Understand existing code before modifying.
- **Small commits.** Each commit should be a single logical change.
- **If stuck, log it.** Write what blocked you in activity.md and move on.
- **If tests fail, fix them.** Do not leave broken tests.
- **Security first.** No raw SQL from LLM output, no secrets in code, no free-text blobs in system prompts from admin data.
- **Use python3** not python (macOS).
- **Never push.** Only commit locally.

## Key Architecture

- **Backend**: Flask (Python), PostgreSQL via Supabase (psycopg2), Vercel serverless (60s max)
- **Agent loop**: `run_agent_loop()` in `services/chat_service.py` — classifies intent, executes tools, streams response
- **Coach routing**: Currently hardcoded `_BIKE_KEYWORDS` in `run_agent_loop()` — Phase 9 replaces this
- **System prompt**: `CHAT_SYSTEM_PROMPT` in `services/openai_coach.py` — Phase 9 makes this dynamic
- **RAG pipeline**: `retrieve_knowledge_context()` in `services/chat_service.py` — pgvector cosine similarity
- **Admin blueprint**: `routes/admin.py` with `_require_admin()` decorator, Jinja2 templates in `templates/admin/`
- **Existing WhatsApp import**: `scripts/import_whatsapp.py` — follow this pattern for new extraction scripts
- **Braintrust evals**: `evals/eval_guardrail.py` — extend this pattern for guardrail compliance evals
- **Tests**: pytest in `tests/` directory
- **Models**: OpenAI GPT-4o-mini for chat, GPT-4o for extraction, text-embedding-3-small for embeddings
- **New libraries**: instructor (structured extraction), trafilatura (blog content), pdfplumber (PDF text)

## Key Files

- `services/chat_service.py` — Agent loop, intent classification, RAG retrieval
- `services/chat_tools.py` — SQL allowlist, execute_allowed_query()
- `services/openai_coach.py` — CHAT_SYSTEM_PROMPT, coaching prompts
- `models.py` — All data access (2400+ lines)
- `routes/admin.py` — Admin blueprint
- `schema/` — Database schema files
- `migrations/` — Migration scripts
- `scripts/import_whatsapp.py` — WhatsApp import (pattern for new scripts)
- `evals/eval_guardrail.py` — Braintrust eval pattern
- `.planning/STATE.md` — Current project state
- `.planning/ROADMAP.md` — Phase definitions and requirements
- `.planning/REQUIREMENTS.md` — All 45 requirements with traceability
