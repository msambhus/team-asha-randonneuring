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

### WhatsApp Knowledge Base

- [x] **WA-01**: WhatsApp export parser handles U+202F timestamps and multi-line messages
- [x] **WA-02**: Rule-based filter removes noise (short messages, media, joins/leaves)
- [x] **WA-03**: LLM classifier retains cycling-relevant content
- [x] **WA-04**: Two-stage filtering pipeline (rules then LLM)
- [x] **WA-05**: text-embedding-3-small embeddings stored in pgvector with HNSW index
- [x] **WA-06**: Incremental import -- only new messages after last imported timestamp
- [x] **WA-07**: UNIQUE constraint on (source, chunk_start, chunk_end) for idempotent re-import
- [x] **WA-08**: RAG retrieval for non-off-topic questions with natural attribution
- [x] **WA-09**: RAG failure degrades gracefully -- chatbot works without community knowledge
- [x] **WA-10**: CLI import script with progress reporting

### Image Previews

- [x] **IMG-01**: Backend endpoint (`/api/image-preview`) accepts a URL, validates it against an allowlist of domains, fetches OpenGraph `og:image` metadata, and returns `{image_url, title, domain}` JSON -- never proxies raw image bytes
- [x] **IMG-02**: Allowlist of approved domains for image preview: cycling/product sites (competitivecyclist.com, trekbikes.com, bike24.com, wiggle.com, chainreactioncycles.com, jensonusa.com, revelatedesigns.com, ortlieb.com, shimano.com, ridewithgps.com, strava.com)
- [x] **IMG-03**: Frontend detects URLs in assistant chat messages after stream completes, calls `/api/image-preview` per URL (max 3 per message), and renders image cards below the message bubble
- [x] **IMG-04**: Image cards render as styled `<img>` elements in a `.image-cards` container below the assistant bubble with title, domain, and link -- never inside the SSE stream or `innerHTML` from LLM output
- [x] **IMG-05**: Image preview endpoint enforces: 2-second timeout on outbound fetch, HTTPS-only scheme check, no private IP ranges, no redirect following, 100KB response size limit on HTML body read
- [x] **IMG-06**: Image preview caches successful results for 1 hour in-process (Flask-Caching SimpleCache already present) to avoid re-fetching the same URL across multiple conversations
- [x] **IMG-07**: Image card rendering degrades gracefully: if preview fetch fails, times out, or returns no `og:image`, no card is shown -- existing text link remains the fallback
- [x] **IMG-08**: CSP `img-src` header extended to include `https:` to permit loading images from external HTTPS origins (confirm existing CSP state before adding)
- [x] **IMG-09**: URLs in assistant messages are parsed on the frontend using a regex after stream completion -- no URL detection during streaming to avoid partial-URL false positives

### RWGPS Route Intelligence

- [ ] **RWGPS-01**: `route_discussion` intent resolves ride name to RWGPS route ID via `ride` table `rwgps_url` column using a new `get_ride_rwgps_url` allowed query
- [ ] **RWGPS-02**: Agent loop calls `fetch_route()` from `services/rwgps.py` when ride plan cache is missing — live RWGPS fallback in `route_discussion` branch
- [ ] **RWGPS-03**: Route data is summarized (distance, elevation, control stops, key segments) into a compact dict for LLM consumption — never dumps raw track_points
- [ ] **RWGPS-04**: Cached ride plan (if exists in `ride_plan` table) is returned first; live RWGPS fetch is the fallback only when cache misses
- [ ] **RWGPS-05**: RWGPS API errors (404, 401, 429, timeout, no waypoints) are caught and produce user-friendly error dicts, not exceptions
- [ ] **RWGPS-06**: RWGPS responses are cached in-memory for 5 minutes via Flask-Caching SimpleCache to avoid duplicate API calls within a chat session
- [ ] **RWGPS-07**: Intent classification prompt updated so `route_discussion` explicitly describes live RWGPS route data capability

### Weather/Wind Forecasting

- [ ] **WTHR-01**: `weather_query` intent type added to IntentResult and intent classification prompt
- [ ] **WTHR-02**: `get_route_weather` tool added to chat_tools.py, called from agent loop for weather_query intent
- [ ] **WTHR-03**: Route geometry sampling — extract lat/lng coordinates at ~50km intervals from RWGPS track_points
- [ ] **WTHR-04**: Bearing computation per segment using Haversine forward bearing formula
- [ ] **WTHR-05**: Headwind/tailwind component calculated from wind direction (meteorological convention: +180 deg) vs route bearing via cosine projection
- [ ] **WTHR-06**: Open-Meteo batch API call — single HTTP request with comma-separated multi-coordinate arrays for all sample points
- [ ] **WTHR-07**: Time-adjusted forecast selection — uses estimated arrival time from ride plan segment timing, not current-hour weather
- [ ] **WTHR-08**: Weather results cached for 1 hour using Flask-Caching SimpleCache
- [ ] **WTHR-09**: Structured segment summary response format with temperature, wind speed, wind assessment (headwind/tailwind/crosswind), and precipitation
- [ ] **WTHR-10**: Graceful degradation — Open-Meteo unavailable or no RWGPS track data returns clear explanation, not crash

### WhatsApp Knowledge Prioritization

- [ ] **WA-PRI-01**: RAG retrieval runs for ALL non-off-topic intents, including `web_search` — community knowledge is always available
- [ ] **WA-PRI-02**: When both RAG and web search results are present, community knowledge is presented FIRST in the response
- [ ] **WA-PRI-03**: Community knowledge attributed explicitly with team member names when available ("Venki suggested...", "Team discussion...")
- [ ] **WA-PRI-04**: Web search results attributed distinctly as external sources ("According to web sources...", "Online reviews suggest...")
- [ ] **WA-PRI-05**: Contradiction handling instruction in system prompt — frame differences between community and web knowledge constructively
- [ ] **WA-PRI-06**: When no community context matches, web-only responses proceed normally without hallucinated community references
- [ ] **WA-PRI-07**: Source cards SSE event still emitted after web search responses (no regression from existing behavior)
- [ ] **WA-PRI-08**: All existing tests pass after prompt/instruction changes (regression safety)

### Multi-Rider Strava Analysis

- [x] **MULTI-01**: Multi-rider Strava analysis page at `/ride/<ride_id>/all-strava` shows all FINISHED riders for a ride with summary table and per-rider accordion sections
- [x] **MULTI-02**: Riders with `strava_data_private = True` shown as "Analysis Private" in multi-rider view -- no Strava data exposed
- [x] **MULTI-03**: Multi-rider view only displays riders with existing cached `strava_ride_analysis` data -- no live Strava API calls triggered for other riders
- [ ] **MULTI-04**: Base/custom plan toggle in `ride_plan_detail.html` restricted to admin-only visibility -- non-admin users retain "View My Custom Plan" access

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
| WA-01 | Phase 5 | Code complete |
| WA-02 | Phase 5 | Code complete |
| WA-03 | Phase 5 | Code complete |
| WA-04 | Phase 5 | Code complete |
| WA-05 | Phase 5 | Code complete |
| WA-06 | Phase 5 | Code complete |
| WA-07 | Phase 5 | Code complete |
| WA-08 | Phase 5 | Code complete |
| WA-09 | Phase 5 | Code complete |
| WA-10 | Phase 5 | Code complete |
| IMG-01 | Phase 6 | Code complete |
| IMG-02 | Phase 6 | Code complete |
| IMG-03 | Phase 6 | Code complete |
| IMG-04 | Phase 6 | Code complete |
| IMG-05 | Phase 6 | Code complete |
| IMG-06 | Phase 6 | Code complete |
| IMG-07 | Phase 6 | Code complete |
| IMG-08 | Phase 6 | Code complete |
| IMG-09 | Phase 6 | Code complete |
| RWGPS-01 | Phase 7 | Planned |
| RWGPS-02 | Phase 7 | Planned |
| RWGPS-03 | Phase 7 | Planned |
| RWGPS-04 | Phase 7 | Planned |
| RWGPS-05 | Phase 7 | Planned |
| RWGPS-06 | Phase 7 | Planned |
| RWGPS-07 | Phase 7 | Planned |
| WTHR-01 | Phase 8 | Planned |
| WTHR-02 | Phase 8 | Planned |
| WTHR-03 | Phase 8 | Planned |
| WTHR-04 | Phase 8 | Planned |
| WTHR-05 | Phase 8 | Planned |
| WTHR-06 | Phase 8 | Planned |
| WTHR-07 | Phase 8 | Planned |
| WTHR-08 | Phase 8 | Planned |
| WTHR-09 | Phase 8 | Planned |
| WTHR-10 | Phase 8 | Planned |
| WA-PRI-01 | Phase 9 | Planned |
| WA-PRI-02 | Phase 9 | Planned |
| WA-PRI-03 | Phase 9 | Planned |
| WA-PRI-04 | Phase 9 | Planned |
| WA-PRI-05 | Phase 9 | Planned |
| WA-PRI-06 | Phase 9 | Planned |
| WA-PRI-07 | Phase 9 | Planned |
| WA-PRI-08 | Phase 9 | Planned |
| MULTI-01 | Phase 10 | Complete |
| MULTI-02 | Phase 10 | Complete |
| MULTI-03 | Phase 10 | Complete |
| MULTI-04 | Phase 10 | Planned |

**Coverage:**
- v1 requirements: 91 total (62 complete + 29 planned)
- Mapped to phases: 91
- Unmapped: 0

---
*Requirements defined: 2026-03-14*
*Last updated: 2026-03-25 -- Phase 10 requirements added (MULTI-01-04).*
