# Requirements: Team Asha Randonneuring Chatbot

**Defined:** 2026-03-14
**Core Value:** Personalized, data-grounded cycling coaching and randonneuring information — answering "Am I ready for my next brevet?" with actual training data, not generic advice.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Infrastructure

- [x] **INFRA-01**: DB schema — `conversation` and `chat_message` tables with proper indexes
- [x] **INFRA-02**: Chat API endpoint with SSE streaming (`/api/chat/stream`)
- [x] **INFRA-03**: Auth gating via `@api_login_required` (returns 401 JSON, no debug bypass) on all chat endpoints
- [x] **INFRA-04**: `maxDuration: 60` in `vercel.json` for chat routes
- [x] **INFRA-05**: Conversation CRUD functions in `models.py`
- [x] **INFRA-06**: Chat message CRUD functions in `models.py`

### Security

- [x] **SEC-01**: Read-only PostgreSQL role for chat queries
- [x] **SEC-02**: `ALLOWED_QUERIES` dict — LLM picks query type from enum, Python owns all SQL
- [x] **SEC-03**: `sqlparse` validation as secondary defense layer on any SQL
- [x] **SEC-04**: OpenAI Moderation API on all user input before processing
- [x] **SEC-05**: Prompt injection defense — user content in `role: user` only, DB data in delimited sections
- [x] **SEC-06**: `max_tokens` enforced on all completions (500-800)
- [x] **SEC-07**: Conversation history capped at last 10 turns per request
- [x] **SEC-08**: Specific OpenAI error handling (`RateLimitError`, `APITimeoutError`, `InternalServerError`)
- [x] **SEC-09**: Input validation — character limits and sanitization on chat input
- [x] **SEC-10**: Cross-user isolation — all queries filtered by `WHERE user_id = authenticated_user_id`
- [x] **SEC-11**: Respect `strava_data_private` flag in context assembly

### Chat Experience

- [x] **CHAT-01**: Floating chat widget accessible on every page
- [x] **CHAT-02**: Widget open/close state persisted via `sessionStorage`
- [x] **CHAT-03**: Multi-turn conversation — last 8 turns loaded from DB
- [x] **CHAT-04**: Streaming response rendering in widget (SSE client)
- [x] **CHAT-05**: User-visible error handling — friendly message on API failure
- [x] **CHAT-06**: Conversation list UI — view/continue previous conversations
- [x] **CHAT-07**: New conversation creation from widget

### Coaching & Knowledge

- [x] **KNOW-01**: System prompt with cycling/randonneuring guardrails
- [x] **KNOW-02**: Randonneuring knowledge — ACP/RUSA rules, brevet distances, cutoffs, SR/R-12, PBP
- [x] **KNOW-03**: Off-topic query handling — polite redirect with cycling topic suggestion
- [x] **KNOW-04**: Bike repair, maintenance, and gear guidance in system prompt
- [x] **KNOW-05**: Nutrition advice for long-distance cycling in system prompt
- [x] **KNOW-06**: Training plan suggestions based on upcoming rides and fitness

### Personalization

- [x] **PERS-01**: Strava context injection — fitness score + recent activities + upcoming brevets
- [x] **PERS-02**: Graceful fallback for non-Strava users (general knowledge mode)
- [x] **PERS-03**: Team Asha context — upcoming brevets, ride plans, routes, team stats

### Agentic Pipeline

- [x] **AGENT-01**: Intent classification via `chat.completions.parse()` with Pydantic model
- [x] **AGENT-02**: Intent enum: `data_query`, `coaching`, `knowledge`, `route_discussion`, `off_topic`
- [x] **AGENT-03**: Tool execution for `data_query` intents — maps query type enum to pre-written SQL
- [x] **AGENT-04**: Named tool coverage: `fitness_score`, `brevet_history`, `upcoming_rides`, `career_stats`, `recent_activities`
- [x] **AGENT-05**: `get_team_stats` tool — season stats, upcoming brevets (non-scoped)
- [x] **AGENT-06**: `get_ride_plan` tool — control stops, distances, elevation for specific ride
- [x] **AGENT-07**: Agent loop with `MAX_ITERATIONS=5` guard; max 3 DB queries per message
- [x] **AGENT-08**: Tool results capped at 50 rows; per-query 5s timeout
- [x] **AGENT-09**: Data citation — LLM references specific numbers from tool results
- [x] **AGENT-10**: Token logging (`prompt_tokens`, `completion_tokens`) in `chat_message` table

### Evals & Observability

- [x] **EVAL-01**: Braintrust project linked to Team Asha workspace
- [x] **EVAL-02**: Eval dataset for intent classification accuracy (golden labeled messages)
- [x] **EVAL-03**: Eval dataset for data grounding (questions with known correct DB values)
- [x] **EVAL-04**: Eval dataset for guardrail effectiveness (off-topic bypass patterns)
- [x] **EVAL-05**: Logging of `span_id`/`trace_id` from Braintrust in `chat_message.metadata`
- [x] **EVAL-06**: Conversation-level quality metrics visible in Braintrust dashboard

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Advanced Features

- **ADV-01**: Token budget dashboard — per-user daily/monthly usage
- **ADV-02**: Conversation summarization for long histories
- **ADV-03**: Per-message feedback (thumbs up/down) with review workflow
- **ADV-04**: Export conversation as markdown
- **ADV-05**: Context-aware page initialization — widget pre-populates based on current page
- **ADV-06**: Conversation search

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Free-form SQL generation | Critical security risk — LLM output IS the injection vector |
| Voice input/output | Text-only for v1; complexity not justified |
| Multi-user chat or forums | Completely different product |
| Garmin/Wahoo integration | Strava only for v1 |
| GPS tracking / live location | Use Strava/RideWithGPS |
| Medical advice | Always defer to healthcare professionals |
| Non-cycling topics | Strict guardrails |
| LangChain/LangGraph | Adds 50MB deps, slow cold starts, conflicts with psycopg2 layer |
| WebSocket-based chat | Incompatible with Vercel serverless |
| Proactive push notifications | Incompatible with Vercel serverless |
| Unlimited conversation history | Quadratic token cost growth |
| Assistants API | Polling incompatible with Vercel timeouts |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| INFRA-01 | Phase 1 | Code complete |
| INFRA-02 | Phase 1 | Code complete |
| INFRA-03 | Phase 1 | Code complete |
| INFRA-04 | Phase 1 | Code complete |
| INFRA-05 | Phase 1 | Code complete |
| INFRA-06 | Phase 1 | Code complete |
| SEC-01 | Phase 1 | Code complete |
| SEC-02 | Phase 1 | Code complete |
| SEC-03 | Phase 1 | Code complete |
| SEC-04 | Phase 1 | Code complete |
| SEC-05 | Phase 1 | Code complete |
| SEC-06 | Phase 1 | Code complete |
| SEC-07 | Phase 1 | Code complete |
| SEC-08 | Phase 1 | Code complete |
| SEC-09 | Phase 1 | Code complete |
| SEC-10 | Phase 2 | Code complete |
| SEC-11 | Phase 2 | Code complete |
| CHAT-01 | Phase 2 | Code complete |
| CHAT-02 | Phase 2 | Code complete |
| CHAT-03 | Phase 2 | Code complete |
| CHAT-04 | Phase 2 | Code complete |
| CHAT-05 | Phase 2 | Code complete |
| CHAT-06 | Phase 2 | Code complete |
| CHAT-07 | Phase 2 | Code complete |
| KNOW-01 | Phase 1 | Code complete |
| KNOW-02 | Phase 1 | Code complete |
| KNOW-03 | Phase 1 | Code complete |
| KNOW-04 | Phase 1 | Code complete |
| KNOW-05 | Phase 1 | Code complete |
| KNOW-06 | Phase 2 | Code complete |
| PERS-01 | Phase 2 | Code complete |
| PERS-02 | Phase 2 | Code complete |
| PERS-03 | Phase 2 | Code complete |
| AGENT-01 | Phase 3 | Code complete |
| AGENT-02 | Phase 3 | Code complete |
| AGENT-03 | Phase 3 | Code complete |
| AGENT-04 | Phase 3 | Code complete |
| AGENT-05 | Phase 3 | Code complete |
| AGENT-06 | Phase 3 | Code complete |
| AGENT-07 | Phase 3 | Code complete |
| AGENT-08 | Phase 3 | Code complete |
| AGENT-09 | Phase 3 | Code complete |
| AGENT-10 | Phase 3 | Code complete |
| EVAL-01 | Phase 4 | Code complete |
| EVAL-02 | Phase 4 | Code complete |
| EVAL-03 | Phase 4 | Code complete |
| EVAL-04 | Phase 4 | Code complete |
| EVAL-05 | Phase 4 | Code complete |
| EVAL-06 | Phase 4 | Code complete |

**Coverage:**
- v1 requirements: 43 total
- Mapped to phases: 43
- Unmapped: 0

---
*Requirements defined: 2026-03-14*
*Last updated: 2026-03-14 after plan revision (INFRA-03 corrected to @api_login_required)*
