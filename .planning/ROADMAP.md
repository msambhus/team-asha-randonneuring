# Roadmap: Team Asha Randonneuring Chatbot

## Milestones

- ✅ **v1.0 Chatbot MVP** - Phases 1-6 (shipped 2026-03-17)
- 🚧 **v2.0 Personality-Driven Coaching** - Phases 7-12 (in progress)

## Phases

<details>
<summary>✅ v1.0 Chatbot MVP (Phases 1-6) - SHIPPED 2026-03-17</summary>

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
- [x] 06-01-PLAN.md — Backend image preview service (TDD): fetch_og_image(), domain allowlist, /api/image-preview endpoint with SSRF defenses and caching
- [x] 06-02-PLAN.md — Frontend image card rendering: URL extraction, DOM card builder, CSS styles, finishStream() integration, human-verify checkpoint

</details>

---

## 🚧 v2.0 Personality-Driven Coaching (In Progress)

**Milestone Goal:** Replace hardcoded coach personas with data-driven personality profiles extracted from real WhatsApp conversations and blog posts, managed through an admin interface, validated by Braintrust evals, and grounded by an expanded cycling knowledge base.

### Phase 7: Data Foundation
**Goal**: All database tables required for personality-driven coaching exist with typed, queryable fields — `personality_profile`, `gear_preference`, `coach_assignment`, and `coaching_guardrail` — manually seeded with Shriram and Venki profiles so the system is testable before extraction runs
**Depends on**: Phase 6
**Requirements**: PROF-01, PROF-02, PROF-03, PROF-04, PROF-05, GUARD-01, GUARD-06
**Success Criteria** (what must be TRUE):
  1. The four new tables exist in Supabase with the correct typed columns, indexes, and foreign keys — a migration script applies cleanly against the live database
  2. Shriram and Venki have manually seeded personality profiles that replicate the content currently hardcoded in `CHAT_SYSTEM_PROMPT` — existing chatbot behavior is preserved, not degraded, after migration
  3. Coach profiles include structured typed fields (tone, humor_type, directness, signature_phrases, topic_biases, topics_allowed) — not free-text blobs; character limits are enforced at the column level
  4. Each config table (`coach_assignment`, `coaching_guardrail`) has `updated_at`, `updated_by`, and `deleted_at` columns for soft-delete and audit history from day one
  5. Guardrail rows carry a `rule_version` stamp that increments on edit — a specific version is queryable without looking at audit history
**Plans**: 3 plans

Plans:
- [x] 07-01-PLAN.md — Schema migration: four new tables, indexes, FK constraints, character limits, soft-delete columns, rule_version trigger
- [x] 07-02-PLAN.md — Model functions: CRUD functions in models.py for all four new tables with parameterized queries
- [x] 07-03-PLAN.md — Seed data: manually seed Shriram and Venki profiles and coach assignments from CHAT_SYSTEM_PROMPT content

### Phase 8: Personality Extraction
**Goal**: Offline CLI scripts extract structured personality traits per person from WhatsApp chat exports and blog posts, store them with source quote evidence and confidence levels in the `personality_profile` table, and merge multi-source traits into a single profile ready for admin review
**Depends on**: Phase 7
**Requirements**: EXTR-01, EXTR-02, EXTR-03, EXTR-04, EXTR-05, EXTR-06, EXTR-07
**Success Criteria** (what must be TRUE):
  1. Running `scripts/extract_personality_whatsapp.py <chat_export.txt>` produces a populated `personality_profile` row per sender with tone, humor_type, directness, encouragement_style, domain_bias, signature_phrases, response_length_tendency, and question_asking_behavior fields filled
  2. Running `scripts/extract_personality_blog.py <url_or_pdf_path>` extracts personality traits from Mihir's WordPress post and Venki's Google Drive PDF — both sources produce profile rows without manual intervention
  3. Each extracted trait row includes 3-5 verbatim source quotes from the actual messages or blog text that justified that trait — an admin reading the quotes can verify or dispute the extraction
  4. Each trait carries a confidence level (high/medium/low) calculated from the source message volume for that sender — a sender with fewer than 20 qualifying messages shows LOW confidence
  5. When a person has both WhatsApp and blog extraction results, the merge script combines them into one profile, weighting blog-derived traits more heavily than group-chat-derived traits, with no duplicate fields
**Plans**: 3 plans

Plans:
- [x] 08-01-PLAN.md — Schema migration (3 new columns, evidence table, UNIQUE constraint fix), test scaffolds, dev dependencies
- [x] 08-02-PLAN.md — WhatsApp extraction: personality_helpers.py shared module + extract_personality_whatsapp.py CLI script
- [x] 08-03-PLAN.md — Blog extraction (trafilatura + pdfplumber) + merge_personality.py multi-source merge script

### Phase 9: Chat Integration
**Goal**: The live chat pipeline reads coach personas, routing rules, and guardrails from the database instead of hardcoded strings — `assemble_coach_context()` replaces the static `CHAT_SYSTEM_PROMPT` persona block and `select_coach_for_message()` replaces the hardcoded `_BIKE_KEYWORDS` routing, with guardrails enforced via a classifier pass before the persona prompt
**Depends on**: Phase 7 (schema), Phase 8 recommended (real data for testing)
**Requirements**: GUARD-07, COACH-02, COACH-03, COACH-04, COACH-05, GEAR-03
**Success Criteria** (what must be TRUE):
  1. A chat message about tires routes to Shriram and a chat message about training plans routes to Venki — routing is driven by `coach_assignment` rows in the database, not by the hardcoded `_BIKE_KEYWORDS` list in code
  2. Adding a new coach row to `coach_assignment` with topic domain mappings causes the chatbot to route matching queries to that coach — no code deployment is required
  3. A fallback coach is designated in the database and handles all unrouted queries — removing all domain mappings for one coach does not break the chatbot
  4. Guardrail rules are loaded from the `coaching_guardrail` table at conversation start and injected into the system prompt as a `<guardrails>` XML block — changing a rule in the DB takes effect on the next message without a redeploy
  5. Gear preferences for the logged-in rider are loaded from `gear_preference` and included in the conversation context — a rider who has a Trek Checkpoint listed receives gear recommendations grounded to that specific bike
**Plans**: 2 plans

Plans:
- [ ] 09-01-PLAN.md — DB-driven coach routing: select_coach_for_message(), get_rider_by_id() helper, TDD test scaffold
- [ ] 09-02-PLAN.md — Guardrail injection, gear context assembly, wire into process_message/run_agent_loop, seed guardrails

### Phase 10: Admin UI
**Goal**: The admin interface lets Mihir view all team member profiles with completeness indicators, edit personality traits with source quote evidence, manage gear preferences per rider, configure coach assignments and routing rules, and manage all guardrail rules — all through the existing admin blueprint without any new frameworks
**Depends on**: Phase 7 (schema), Phase 8 (real data to review), Phase 9 (routing config that admin controls)
**Requirements**: ADMN-01, ADMN-02, ADMN-03, ADMN-04, ADMN-05, ADMN-06, GEAR-01, GEAR-02, COACH-01, GUARD-02, GUARD-03, GUARD-04, GUARD-05
**Success Criteria** (what must be TRUE):
  1. The admin `/admin/personalities` page lists all team members with a profile completeness indicator (e.g., "7/8 traits filled", "LOW confidence") visible at a glance without clicking into individual profiles
  2. Clicking a team member shows editable personality trait fields with structured dropdowns for enumerations (humor_type, directness) and text inputs for phrases — alongside the 3-5 source quotes that justified each extracted trait
  3. The admin can trigger re-extraction for a specific person from the UI and see the page refresh with updated trait values and new confidence badges
  4. The admin `/admin/gear` page shows per-rider gear fields (bike make/model/year/material, wheels/tires, lighting, bags, navigation, kit) and value orientation — all editable with typed fields, no free-text blobs
  5. The admin `/admin/coaches` page shows the coach roster with active/inactive toggle, topic domain assignments editable per coach, routing rules, and fallback coach designation
  6. The admin `/admin/guardrails` page shows all guardrail rules with type, value, and active/inactive toggle — rules can be created, edited, toggled off, and soft-deleted without touching code or redeploying
**Plans**: 3 plans

Plans:
- [ ] 10-01-PLAN.md — Personality admin: model helpers (get_trait_evidence, get_all_guardrails), team list with completeness, trait edit with evidence quotes and confidence badges
- [ ] 10-02-PLAN.md — Gear admin: per-rider gear list, gear edit form with typed inputs and value orientation dropdown
- [ ] 10-03-PLAN.md — Coach and guardrail admin: coach roster with domain assignments, guardrail CRUD with toggle/create/edit/delete

### Phase 11: Braintrust Evals
**Goal**: A Braintrust eval suite validates that the guardrail configuration in the database is actually enforced by the chat pipeline — loading rules dynamically at eval time, generating test cases per rule, and scoring compliance with LLM-as-judge across scope enforcement, topic blocking, medical deflection, and persona consistency
**Depends on**: Phase 9 (chat pipeline enforcing guardrails), Phase 10 (guardrails configured via admin)
**Requirements**: EVAL2-01, EVAL2-02, EVAL2-03, EVAL2-04, EVAL2-05, EVAL2-06
**Success Criteria** (what must be TRUE):
  1. Running `evals/eval_guardrail_dynamic.py` loads guardrail rules from the live database and generates test cases automatically — adding a new guardrail rule in admin immediately produces new eval test cases without editing the eval script
  2. The eval dataset covers all four compliance categories: scope enforcement (correct coach handles correct topics), topic blocking (off-cycling queries get redirected), medical deflection (health questions get "consult a doctor"), and persona consistency (Shriram mentions gear, Venki does not volunteer gear recommendations)
  3. Each guardrail rule has at least 3 test cases: a clear violation that must fail, a clear pass that must pass, and a boundary case — plus 2 adversarial inputs designed to elicit the blocked behavior
  4. Scoring uses `autoevals.LLMClassifier` for semantic compliance, not keyword matching — a response that deflects with different wording still scores as compliant
  5. Eval results are tagged with the guardrail rule version stamp so a specific result set can be correlated to the exact rule configuration that produced it — a rule change produces a new version and a new comparable result set
**Plans**: TBD

Plans:
- [ ] 11-01: Dynamic eval script — DB-driven test case generation, LLMClassifier scorer, version-stamped result tagging
- [ ] 11-02: Eval dataset coverage — scope enforcement, topic blocking, medical deflection, persona consistency cases with adversarial inputs

### Phase 12: Knowledge Base Expansion
**Goal**: The chatbot's knowledge base is expanded with content from external cycling and randonneuring URLs listed in the resources Google Sheets spreadsheet — crawled, content-extracted, quality-filtered, deduplicated, and embedded into the existing pgvector table with admin visibility and control over each source
**Depends on**: Phase 7 (schema for source tracking), Phase 6 (existing pgvector table)
**Requirements**: KB-01, KB-02, KB-03, KB-04, KB-05, KB-06
**Success Criteria** (what must be TRUE):
  1. Running `scripts/embed_resources.py` reads URLs from the resources Google Sheets spreadsheet (via CSV export URL, no Google API client), fetches each page with trafilatura content extraction, and embeds the result — a complete run against all listed URLs completes without manual intervention
  2. Embedded chunks are stored in the existing `whatsapp_chunk` table with a `web_*` source prefix — a chunk from `https://randonneuring.org/guide` has `source = "web_randonneuring.org"` and is retrieved by the existing HNSW index automatically
  3. Duplicate detection prevents re-embedding identical content — running the script twice against the same URL produces the same chunk count, not double the chunks
  4. The admin `/admin/knowledge` page lists every embedded source with URL, embed date, and chunk count — an admin can see at a glance what is in the knowledge base and when it was last indexed
  5. The admin can trigger re-embed for a specific URL from the UI (refreshes stale content) and can remove all embeddings from a specific source (removes a low-quality or off-topic site) — both without code changes
**Plans**: TBD

Plans:
- [ ] 12-01: Embed script — Google Sheets CSV export URL parsing, trafilatura extraction, quality filter, SHA-256 deduplication, source-tagged pgvector ingestion
- [ ] 12-02: Knowledge admin page — per-source list with chunk counts, embed dates, re-embed trigger, source removal

## Progress

**Execution Order:**
Phases 7-12 execute in numeric order (with Phase 12 parallelizable after Phase 7).

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Secure Foundation | v1.0 | 3/3 | Complete | 2026-03-15 |
| 2. Core Chat Experience | v1.0 | 3/3 | Complete | 2026-03-15 |
| 3. Agentic Tool-Calling Pipeline | v1.0 | 3/3 | Complete | 2026-03-15 |
| 4. Braintrust Evals + Observability | v1.0 | 2/2 | Complete | 2026-03-15 |
| 5. WhatsApp Knowledge Base | v1.0 | 3/3 | Complete | 2026-03-16 |
| 6. Image Preview Cards | v1.0 | 2/2 | Complete | 2026-03-17 |
| 7. Data Foundation | v2.0 | 0/3 | Not started | - |
| 8. Personality Extraction | 2/3 | In Progress|  | - |
| 9. Chat Integration | v2.0 | 0/2 | Not started | - |
| 10. Admin UI | v2.0 | 0/3 | Not started | - |
| 11. Braintrust Evals | v2.0 | 0/2 | Not started | - |
| 12. Knowledge Base Expansion | v2.0 | 0/2 | Not started | - |
