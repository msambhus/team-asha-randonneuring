# Roadmap: Team Asha Randonneuring Chatbot

## Overview

This roadmap adds a cycling-domain AI coaching chatbot to the existing Flask/Vercel/PostgreSQL app. The work progresses in four phases: first establishing the secure streaming infrastructure (all pitfalls addressed before any UI), then building the user-facing widget with Strava personalization, then wiring up the agentic tool-calling pipeline for data-grounded answers, and finally integrating Braintrust evals and observability. Every phase depends on the one before it — the security constraints in Phase 1 are invariants inherited by all subsequent phases.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Secure Foundation** - Chat API, SSE streaming endpoint, DB schema, security controls, system prompt — all pitfalls addressed before any UI
- [x] **Phase 2: Core Chat Experience** - Floating widget, multi-turn conversations, Strava personalization, conversation list
- [x] **Phase 3: Agentic Tool-Calling Pipeline** - Intent classification, tool execution, agent loop, data-grounded responses
- [x] **Phase 4: Braintrust Evals + Observability** - Eval datasets, Braintrust integration, quality metrics dashboard
- [ ] **Phase 5: WhatsApp Knowledge Base** - Import group chat exports, parse and filter cycling content, store in vector DB, integrate RAG into chatbot

## Phase Details

### Phase 1: Secure Foundation
**Goal**: A tested, secure `/api/chat/stream` SSE endpoint exists that accepts messages, runs them through moderation, enforces token limits, persists history to PostgreSQL, and returns streamed completions — with no UI, but validated on Vercel Preview
**Depends on**: Nothing (first phase)
**Requirements**: INFRA-01, INFRA-02, INFRA-03, INFRA-04, INFRA-05, INFRA-06, SEC-01, SEC-02, SEC-03, SEC-04, SEC-05, SEC-06, SEC-07, SEC-08, SEC-09, KNOW-01, KNOW-02, KNOW-03, KNOW-04, KNOW-05
**Success Criteria** (what must be TRUE):
  1. A `curl` or Postman POST to `/api/chat/stream` with a valid session returns a streaming SSE response with `data:` lines — confirmed working on Vercel Preview, not just local
  2. An unauthenticated request to any `/api/chat/*` endpoint returns 401 — the debug-mode auth bypass does not apply
  3. A user message containing adversarial prompt injection content is blocked by the OpenAI Moderation API and returns a friendly error before the LLM is called
  4. The `conversation` and `chat_message` tables exist in Supabase with the required indexes; CRUD functions in `models.py` can create, read, and append messages using only parameterized queries
  5. The system prompt covers randonneuring rules (ACP/RUSA, brevet distances, cutoffs, SR/R-12, PBP), bike maintenance, nutrition, and cycling-only guardrails — an off-topic question ("who won the World Cup?") returns a polite cycling redirect
**Plans**: 3 plans

Plans:
- [x] 01-01-PLAN.md — DB schema, read-only role, pytest infrastructure, and models.py CRUD functions
- [x] 01-02-PLAN.md — SSE streaming endpoint with auth gating, moderation, token limits, and error handling
- [x] 01-03-PLAN.md — System prompt with randonneuring knowledge, SQL allowlist scaffold, and Vercel config

### Phase 2: Core Chat Experience
**Goal**: A floating chat widget accessible on every page of the app allows logged-in users to have multi-turn conversations with context drawn from their Strava data, persisted across sessions, with cross-user isolation enforced throughout
**Depends on**: Phase 1
**Requirements**: SEC-10, SEC-11, CHAT-01, CHAT-02, CHAT-03, CHAT-04, CHAT-05, CHAT-06, CHAT-07, KNOW-06, PERS-01, PERS-02, PERS-03
**Success Criteria** (what must be TRUE):
  1. The floating chat widget is visible on every page after login; opening and closing it on one page and navigating to another page restores the same open/closed state via `sessionStorage`
  2. A Strava-connected user asking "How ready am I for my next brevet?" receives a response that references their actual fitness score and upcoming brevet — not generic advice
  3. A user without Strava connected receives a useful general cycling/randonneuring response rather than an error or empty personalization section
  4. A user can open the conversation list, see previous sessions titled and timestamped, and click one to continue that conversation with its prior context loaded
  5. User A cannot retrieve User B's conversations or Strava data — all context queries are scoped by `WHERE user_id = authenticated_user_id`; a user with `strava_data_private = True` has no Strava context injected
**Plans**: 3 plans

Plans:
- [x] 02-01-PLAN.md — Chat widget (Jinja partial + inline JS) with SSE client, open/close state, error display
- [x] 02-02-PLAN.md — Strava context assembly, cross-user isolation, privacy flag enforcement
- [x] 02-03-PLAN.md — Conversation list endpoint and UI, new conversation creation

### Phase 3: Agentic Tool-Calling Pipeline
**Goal**: The chatbot detects when a user is asking a data-seeking question, executes the appropriate pre-written SQL query via the tool registry, and synthesizes a response that cites specific numbers from the result — without ever executing free-form SQL
**Depends on**: Phase 2
**Requirements**: AGENT-01, AGENT-02, AGENT-03, AGENT-04, AGENT-05, AGENT-06, AGENT-07, AGENT-08, AGENT-09, AGENT-10
**Success Criteria** (what must be TRUE):
  1. Asking "What is my current fitness score?" triggers an `AGENT-intent: data_query` classification and returns an answer that quotes the actual score from the DB, not a hedged estimate
  2. Asking "Tell me about the Cascade 400 route" triggers a `route_discussion` intent and the `get_ride_plan` tool returns control stop details, distances, and elevation for that ride
  3. Asking an off-topic question ("What's the best pizza in Seattle?") is classified as `off_topic` and no DB queries are executed — the agent loop exits after intent classification
  4. The agent loop never exceeds 5 iterations or 3 DB queries per message; tool results are capped at 50 rows; a query that runs longer than 5 seconds is aborted
  5. Every response in `chat_message` records `prompt_tokens` and `completion_tokens` from `response.usage` — token consumption is visible per message in the DB
**Plans**: 3 plans

Plans:
- [x] 03-01-PLAN.md — Intent classification with Pydantic IntentResult model and classify_intent() via chat.completions.parse()
- [x] 03-02-PLAN.md — Tool registry: populate ALLOWED_QUERIES with 7 named queries, add SET LOCAL timeout enforcement
- [x] 03-03-PLAN.md — Agent loop with iteration/query guards, tool result injection, data citation, process_message() wiring

### Phase 4: Braintrust Evals + Observability
**Goal**: The chatbot's quality is measurable — intent classification accuracy, data grounding correctness, and guardrail effectiveness are tracked via Braintrust eval datasets, with every production conversation emitting trace spans to the Team Asha workspace
**Depends on**: Phase 3
**Requirements**: EVAL-01, EVAL-02, EVAL-03, EVAL-04, EVAL-05, EVAL-06
**Success Criteria** (what must be TRUE):
  1. The Braintrust Team Asha project is linked and a baseline eval run completes without error — results are visible at `https://www.braintrust.dev/app/setup/Team%20Asha`
  2. The intent classification eval dataset contains at least 20 labeled messages covering all 5 intent types; running the eval produces an accuracy score
  3. The data grounding eval dataset contains at least 10 question/answer pairs with known correct DB values; the eval flags responses that do not cite the expected numbers
  4. The guardrail eval dataset contains at least 10 known off-topic bypass patterns; every pattern is classified as `off_topic` and produces no DB tool calls
  5. Every production chat message stores `span_id` and `trace_id` in `chat_message.metadata` — a specific conversation can be looked up by span in the Braintrust dashboard
**Plans**: 2 plans

Plans:
- [x] 04-01-PLAN.md — Braintrust SDK install, production span logging in chat_service.py, span_id/trace_id in chat_message metadata
- [x] 04-02-PLAN.md — Eval datasets (intent classification, data grounding, guardrail) with custom scorers and baseline eval scripts

### Phase 5: WhatsApp Knowledge Base
**Goal**: The chatbot answers cycling questions with grounded community knowledge from real Team Asha WhatsApp group discussions — parsed, filtered (rules + LLM), embedded, stored in pgvector, and retrieved via RAG at query time
**Depends on**: Phase 4
**Requirements**: WA-01, WA-02, WA-03, WA-04, WA-05, WA-06, WA-07, WA-08, WA-09, WA-10
**Success Criteria** (what must be TRUE):
  1. WhatsApp export files are parsed correctly, handling U+202F timestamps and multi-line messages
  2. Two-stage filtering (rule-based + LLM classification) retains cycling-relevant content and discards noise
  3. Filtered chunks are embedded with text-embedding-3-small and stored in pgvector with HNSW index
  4. Re-importing only processes new messages after the last imported timestamp (incremental append)
  5. The chatbot retrieves relevant community knowledge for non-off-topic questions and attributes it naturally
  6. RAG failure degrades gracefully — chatbot continues working without community knowledge
**Plans**: 3 plans

Plans:
- [ ] 05-01-PLAN.md — WhatsApp parser, chunker, two-stage filter (rule-based + LLM), and formatter with TDD
- [ ] 05-02-PLAN.md — pgvector schema, CLI import script with incremental append and two-stage filtering
- [ ] 05-03-PLAN.md — RAG retrieval function, agent loop integration, system prompt update

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Secure Foundation | 3/3 | Code complete | 2026-03-15 |
| 2. Core Chat Experience | 3/3 | Code complete | 2026-03-15 |
| 3. Agentic Tool-Calling Pipeline | 3/3 | Code complete | 2026-03-15 |
| 4. Braintrust Evals + Observability | 2/2 | Code complete | 2026-03-15 |
| 5. WhatsApp Knowledge Base | 0/3 | Planning | — |
