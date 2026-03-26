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
- [x] **Phase 5: WhatsApp Knowledge Base** - Import group chat exports, parse and filter cycling content, store in vector DB, integrate RAG into chatbot
- [ ] **Phase 6: Image Preview Cards** - Show product images and bike accessory photos inline in chatbot responses via OpenGraph extraction

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
- [x] 05-01-PLAN.md — WhatsApp parser, chunker, two-stage filter (rule-based + LLM), and formatter with TDD
- [x] 05-02-PLAN.md — pgvector schema, CLI import script with incremental append and two-stage filtering
- [x] 05-03-PLAN.md — RAG retrieval function, agent loop integration, system prompt update

### Phase 6: Image Preview Cards
**Goal**: When the chatbot mentions product URLs from allowlisted cycling/gear domains, image preview cards with product photos appear below the assistant message — extracted via server-side OpenGraph metadata fetching with SSRF defenses, rendered via safe DOM construction, with graceful degradation when previews are unavailable
**Depends on**: Phase 5
**Requirements**: IMG-01, IMG-02, IMG-03, IMG-04, IMG-05, IMG-06, IMG-07, IMG-08, IMG-09
**Success Criteria** (what must be TRUE):
  1. GET `/api/image-preview?url=<allowlisted_url>` returns JSON with `image_url`, `title`, `domain` — extracted from the page's OpenGraph metadata
  2. Non-allowlisted domains return 403; HTTP URLs return 403; unauthenticated requests return 401
  3. After an assistant message stream completes, HTTPS URLs in the response are detected and up to 3 image preview cards appear below the bubble
  4. Image cards show product photo, title, and domain — clicking opens the original URL in a new tab
  5. Failed previews degrade gracefully: no card shown, existing text link remains
  6. All image card DOM construction uses safe methods (createElement, textContent) — no innerHTML with API response data
**Plans**: 2 plans

Plans:
- [ ] 06-01-PLAN.md — Backend image preview service (TDD): fetch_og_image(), domain allowlist, /api/image-preview endpoint with SSRF defenses and caching
- [ ] 06-02-PLAN.md — Frontend image card rendering: URL extraction, DOM card builder, CSS styles, finishStream() integration, human-verify checkpoint

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Secure Foundation | 3/3 | Code complete | 2026-03-15 |
| 2. Core Chat Experience | 3/3 | Code complete | 2026-03-15 |
| 3. Agentic Tool-Calling Pipeline | 3/3 | Code complete | 2026-03-15 |
| 4. Braintrust Evals + Observability | 2/2 | Code complete | 2026-03-15 |
| 5. WhatsApp Knowledge Base | 3/3 | Code complete | 2026-03-16 |
| 6. Image Preview Cards | 2/2 | Code complete | 2026-03-17 |
| 7. RWGPS Route Intelligence | 0/2 | Planned | — |
| 8. Weather/Wind Forecasting | 0/2 | Planned | — |
| 9. WhatsApp Knowledge Priority | 0/1 | Planned | — |
| 10. Multi-Rider Strava Analysis | 1/2 | In progress | — |

### Phase 7: RWGPS Route Intelligence
**Goal:** When the user asks about a route, the chatbot resolves the ride name to a RWGPS route ID, checks for a cached ride plan first, and if none exists, fetches live route data from the RWGPS API -- providing elevation profile, distance, control points, and key segments grounded in real route data, not generic advice
**Depends on:** Phase 6
**Requirements**: RWGPS-01, RWGPS-02, RWGPS-03, RWGPS-04, RWGPS-05, RWGPS-06, RWGPS-07
**Success Criteria** (what must be TRUE):
  1. Asking "Tell me about the Cascade 400" with no cached ride plan triggers a live RWGPS API fetch and returns elevation, distance, control stops, and key segment data
  2. Asking about a route that HAS a cached ride plan returns the cached data without calling the RWGPS API
  3. RWGPS API errors (404, 401, 429, timeout) produce user-friendly messages, not crashes
  4. RWGPS responses are cached in-memory for 5 minutes to avoid duplicate API calls within a chat session
  5. The intent classification prompt describes route_discussion as capable of live RWGPS data access
**Plans**: 2 plans

Plans:
- [ ] 07-01-PLAN.md — Route data functions (TDD): get_ride_rwgps_url SQL query, summarize_route_for_chat(), fetch_and_summarize_route() with caching and error handling
- [ ] 07-02-PLAN.md — Agent loop wiring: extend route_discussion branch with live RWGPS fallback, update intent classification prompt

### Phase 8: Weather and wind forecasting for routes — use RandoPlan-style data to answer about headwinds, tailwinds, temperature, and conditions along a route

**Goal:** When a user asks about weather conditions for a specific route, the chatbot fetches route geometry from RWGPS, samples coordinates along the route, makes a single batched Open-Meteo API call for hourly forecasts, computes headwind/tailwind components from bearing math, and presents a structured segment-by-segment weather summary with arrival-time-adjusted forecasts
**Depends on:** Phase 7
**Requirements**: WTHR-01, WTHR-02, WTHR-03, WTHR-04, WTHR-05, WTHR-06, WTHR-07, WTHR-08, WTHR-09, WTHR-10
**Success Criteria** (what must be TRUE):
  1. Asking "What's the weather for the Cascade 400?" triggers a `weather_query` intent and returns a segment-by-segment forecast with temperature, wind, and precipitation for each section of the route
  2. Wind analysis includes headwind/tailwind assessment per segment using bearing math and meteorological wind direction convention
  3. Forecasts are time-adjusted: the weather at km 300 uses the estimated arrival time (T+24h for a 400km ride), not current-hour weather
  4. Open-Meteo is called with a single batched multi-coordinate request (not one call per point)
  5. Weather results are cached for 1 hour using Flask-Caching SimpleCache
  6. If Open-Meteo is unavailable or the route has no RWGPS track data, the chatbot responds with a clear explanation instead of crashing
**Plans**: 2 plans

Plans:
- [ ] 08-01-PLAN.md — Weather service module (TDD): route sampling, bearing math, headwind computation, Open-Meteo batch fetch, caching, response formatting
- [ ] 08-02-PLAN.md — Intent classification + agent loop integration: weather_query intent, execute_route_weather tool, RWGPS wiring

### Phase 9: Prioritize WhatsApp community knowledge in chatbot responses — attribute insights to the group, then compare and contrast with web search results

**Goal:** When both community knowledge (RAG) and web search results are available, the chatbot always presents community knowledge FIRST with explicit attribution ("Team member Venki mentioned..."), then compares/contrasts with web sources -- with clear source separation, contradiction framing, and named attribution throughout
**Requirements**: WA-PRI-01, WA-PRI-02, WA-PRI-03, WA-PRI-04, WA-PRI-05, WA-PRI-06, WA-PRI-07, WA-PRI-08
**Depends on:** Phase 8
**Plans:** 1 plan

Plans:
- [ ] 09-01-PLAN.md — Strengthen RAG injection instruction, add web-with-community instruction variant, update CHAT_SYSTEM_PROMPT with community-first priority, bump max_tokens for web_search

### Phase 10: Multi-rider Strava ride analysis — show all riders per ride, move plan toggle to admin

**Goal:** The Strava ride analysis page expands from single-rider to multi-rider -- a new page at `/ride/<ride_id>/all-strava` shows every FINISHED rider's cached analysis for a ride event with summary table and per-rider accordion, honoring privacy flags and using only cached data (no live Strava API calls). The base/custom plan toggle in ride_plan_detail.html becomes admin-only.
**Requirements**: MULTI-01, MULTI-02, MULTI-03, MULTI-04
**Depends on:** Phase 9
**Success Criteria** (what must be TRUE):
  1. GET `/ride/<ride_id>/all-strava` returns a page showing all FINISHED riders for that ride with their Strava analysis summaries
  2. Riders with `strava_data_private = True` are shown as "Analysis Private" -- no Strava data exposed
  3. Riders without cached `strava_ride_analysis` are shown as "Not Yet Analyzed" with a link to their individual page -- no live Strava API calls triggered
  4. Riders with cached analysis display comparison data (plan vs actual stops, summary metrics)
  5. The "Base Plan" toggle in `ride_plan_detail.html` is only visible to admin users; "View My Custom Plan" remains visible to all users with a custom plan
  6. The ride detail page links to the multi-rider analysis view
**Plans:** 2 plans

Plans:
- [x] 10-01-PLAN.md — Backend model function (get_finished_riders_for_ride), route handler (/ride/<ride_id>/all-strava), and tests
- [ ] 10-02-PLAN.md — Multi-rider template (summary table + per-rider accordion), admin-gate plan toggle, navigation links, human-verify checkpoint
