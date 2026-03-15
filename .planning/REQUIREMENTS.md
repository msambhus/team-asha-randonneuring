# Requirements: Team Asha Randonneuring Chatbot

**Defined:** 2026-03-14
**Core Value:** Personalized, data-grounded cycling coaching and randonneuring information — answering "Am I ready for my next brevet?" with actual training data, not generic advice.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Infrastructure

- [ ] **INFRA-01**: DB schema — `conversation` and `chat_message` tables with proper indexes
- [ ] **INFRA-02**: Chat API endpoint with SSE streaming (`/api/chat/stream`)
- [ ] **INFRA-03**: Auth gating via `@api_login_required` (returns 401 JSON, no debug bypass) on all chat endpoints
- [ ] **INFRA-04**: `maxDuration: 60` in `vercel.json` for chat routes
- [ ] **INFRA-05**: Conversation CRUD functions in `models.py`
- [ ] **INFRA-06**: Chat message CRUD functions in `models.py`

### Security

- [ ] **SEC-01**: Read-only PostgreSQL role for chat queries
- [ ] **SEC-02**: `ALLOWED_QUERIES` dict — LLM picks query type from enum, Python owns all SQL
- [ ] **SEC-03**: `sqlparse` validation as secondary defense layer on any SQL
- [ ] **SEC-04**: OpenAI Moderation API on all user input before processing
- [ ] **SEC-05**: Prompt injection defense — user content in `role: user` only, DB data in delimited sections
- [ ] **SEC-06**: `max_tokens` enforced on all completions (500-800)
- [ ] **SEC-07**: Conversation history capped at last 10 turns per request
- [ ] **SEC-08**: Specific OpenAI error handling (`RateLimitError`, `APITimeoutError`, `InternalServerError`)
- [ ] **SEC-09**: Input validation — character limits and sanitization on chat input
- [ ] **SEC-10**: Cross-user isolation — all queries filtered by `WHERE user_id = authenticated_user_id`
- [ ] **SEC-11**: Respect `strava_data_private` flag in context assembly

### Chat Experience

- [ ] **CHAT-01**: Floating chat widget accessible on every page
- [ ] **CHAT-02**: Widget open/close state persisted via `sessionStorage`
- [ ] **CHAT-03**: Multi-turn conversation — last 8 turns loaded from DB
- [ ] **CHAT-04**: Streaming response rendering in widget (SSE client)
- [ ] **CHAT-05**: User-visible error handling — friendly message on API failure
- [ ] **CHAT-06**: Conversation list UI — view/continue previous conversations
- [ ] **CHAT-07**: New conversation creation from widget

### Coaching & Knowledge

- [ ] **KNOW-01**: System prompt with cycling/randonneuring guardrails
- [ ] **KNOW-02**: Randonneuring knowledge — ACP/RUSA rules, brevet distances, cutoffs, SR/R-12, PBP
- [ ] **KNOW-03**: Off-topic query handling — polite redirect with cycling topic suggestion
- [ ] **KNOW-04**: Bike repair, maintenance, and gear guidance in system prompt
- [ ] **KNOW-05**: Nutrition advice for long-distance cycling in system prompt
- [ ] **KNOW-06**: Training plan suggestions based on upcoming rides and fitness

### Personalization

- [ ] **PERS-01**: Strava context injection — fitness score + recent activities + upcoming brevets
- [ ] **PERS-02**: Graceful fallback for non-Strava users (general knowledge mode)
- [ ] **PERS-03**: Team Asha context — upcoming brevets, ride plans, routes, team stats

### Agentic Pipeline

- [ ] **AGENT-01**: Intent classification via `chat.completions.parse()` with Pydantic model
- [ ] **AGENT-02**: Intent enum: `data_query`, `coaching`, `knowledge`, `route_discussion`, `off_topic`
- [ ] **AGENT-03**: Tool execution for `data_query` intents — maps query type enum to pre-written SQL
- [ ] **AGENT-04**: Named tool coverage: `fitness_score`, `brevet_history`, `upcoming_rides`, `career_stats`, `recent_activities`
- [ ] **AGENT-05**: `get_team_stats` tool — season stats, upcoming brevets (non-scoped)
- [ ] **AGENT-06**: `get_ride_plan` tool — control stops, distances, elevation for specific ride
- [ ] **AGENT-07**: Agent loop with `MAX_ITERATIONS=5` guard; max 3 DB queries per message
- [ ] **AGENT-08**: Tool results capped at 50 rows; per-query 5s timeout
- [ ] **AGENT-09**: Data citation — LLM references specific numbers from tool results
- [ ] **AGENT-10**: Token logging (`prompt_tokens`, `completion_tokens`) in `chat_message` table

### Evals & Observability

- [ ] **EVAL-01**: Braintrust project linked to Team Asha workspace
- [ ] **EVAL-02**: Eval dataset for intent classification accuracy (golden labeled messages)
- [ ] **EVAL-03**: Eval dataset for data grounding (questions with known correct DB values)
- [ ] **EVAL-04**: Eval dataset for guardrail effectiveness (off-topic bypass patterns)
- [ ] **EVAL-05**: Logging of `span_id`/`trace_id` from Braintrust in `chat_message.metadata`
- [ ] **EVAL-06**: Conversation-level quality metrics visible in Braintrust dashboard

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
| INFRA-01 | Phase 1 | Pending |
| INFRA-02 | Phase 1 | Pending |
| INFRA-03 | Phase 1 | Pending |
| INFRA-04 | Phase 1 | Pending |
| INFRA-05 | Phase 1 | Pending |
| INFRA-06 | Phase 1 | Pending |
| SEC-01 | Phase 1 | Pending |
| SEC-02 | Phase 1 | Pending |
| SEC-03 | Phase 1 | Pending |
| SEC-04 | Phase 1 | Pending |
| SEC-05 | Phase 1 | Pending |
| SEC-06 | Phase 1 | Pending |
| SEC-07 | Phase 1 | Pending |
| SEC-08 | Phase 1 | Pending |
| SEC-09 | Phase 1 | Pending |
| SEC-10 | Phase 2 | Pending |
| SEC-11 | Phase 2 | Pending |
| CHAT-01 | Phase 2 | Pending |
| CHAT-02 | Phase 2 | Pending |
| CHAT-03 | Phase 2 | Pending |
| CHAT-04 | Phase 2 | Pending |
| CHAT-05 | Phase 2 | Pending |
| CHAT-06 | Phase 2 | Pending |
| CHAT-07 | Phase 2 | Pending |
| KNOW-01 | Phase 1 | Pending |
| KNOW-02 | Phase 1 | Pending |
| KNOW-03 | Phase 1 | Pending |
| KNOW-04 | Phase 1 | Pending |
| KNOW-05 | Phase 1 | Pending |
| KNOW-06 | Phase 2 | Pending |
| PERS-01 | Phase 2 | Pending |
| PERS-02 | Phase 2 | Pending |
| PERS-03 | Phase 2 | Pending |
| AGENT-01 | Phase 3 | Pending |
| AGENT-02 | Phase 3 | Pending |
| AGENT-03 | Phase 3 | Pending |
| AGENT-04 | Phase 3 | Pending |
| AGENT-05 | Phase 3 | Pending |
| AGENT-06 | Phase 3 | Pending |
| AGENT-07 | Phase 3 | Pending |
| AGENT-08 | Phase 3 | Pending |
| AGENT-09 | Phase 3 | Pending |
| AGENT-10 | Phase 3 | Pending |
| EVAL-01 | Phase 4 | Pending |
| EVAL-02 | Phase 4 | Pending |
| EVAL-03 | Phase 4 | Pending |
| EVAL-04 | Phase 4 | Pending |
| EVAL-05 | Phase 4 | Pending |
| EVAL-06 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 43 total
- Mapped to phases: 43
- Unmapped: 0

---
*Requirements defined: 2026-03-14*
*Last updated: 2026-03-14 after plan revision (INFRA-03 corrected to @api_login_required)*
