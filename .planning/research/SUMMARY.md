# Project Research Summary

**Project:** Team Asha Randonneuring — Personality-Driven AI Coaching (Milestone 2)
**Domain:** Brownfield LLM application — personality extraction, admin configuration, RAG knowledge expansion
**Researched:** 2026-03-17
**Confidence:** HIGH (stack and architecture derived from direct codebase inspection; features and pitfalls from domain knowledge)

---

## Executive Summary

This milestone is a brownfield extension of a working Flask/OpenAI chatbot. The core challenge is not building a chatbot — that is done — but replacing hardcoded coach personas with data-driven, admin-configurable personality profiles extracted from real WhatsApp conversations and blog posts. The approach research recommends is incremental and database-first: establish the schema and data access layer first, run offline extraction scripts next, wire the extracted data into the chat pipeline, and then give the admin UI the ability to review and tune the output. Nothing gets deployed to users until Venki and Shriram have reviewed the AI's representation of their personas.

The stack additions are minimal and deliberate. Three new libraries cover the new capability domains: `instructor` for structured LLM output extraction (replaces brittle raw JSON mode), `trafilatura` for web content extraction (handles blog boilerplate stripping that BeautifulSoup cannot), and `pdfplumber` for Google Drive PDF extraction. Everything else — database access, admin UI, evals — uses existing patterns. Critically, Vercel's serverless constraints rule out background workers entirely, so all heavy operations (extraction, crawling, embedding) run as local CLI scripts, not HTTP endpoints.

The key risk is persona quality. Personality extraction from noisy group chat data is not deterministic — the model can hallucinate traits, invert personality signals, or produce caricatures by over-applying extracted traits. The mitigation is a mandatory human review gate: extraction results go to a pending state, admins see source quotes that justify each trait, and coaches review actual AI responses before the personas go live. A secondary risk is prompt injection through admin-editable personality fields; this is addressed by storing traits as structured typed fields (not free-text blobs) and wrapping all database-sourced text in XML boundary markers in the system prompt.

---

## Key Findings

### Recommended Stack

The existing stack (Flask 3.0, psycopg2, OpenAI, pgvector, Tailwind, Braintrust) handles all new features except content extraction. Three additions are justified; all alternatives were ruled out for Vercel bundle size, framework conflict, or pattern inconsistency.

**New libraries:**
- `instructor` 1.14.5 — Structured LLM output via Pydantic; replaces brittle raw JSON mode; 3M+ downloads; already uses Pydantic which OpenAI SDK pulls in
- `trafilatura` 2.0.0 — Blog and web content extraction; strips boilerplate automatically; lighter than newspaper4k; no data downloads required
- `pdfplumber` 0.11.9 — Google Drive PDF text extraction; more reliable on Google Docs exports than pypdf; leaner than PyMuPDF

**Libraries explicitly ruled out (important for planning):**
- Celery / RQ / any background worker — incompatible with Vercel serverless; no persistent processes
- LangChain / LlamaIndex — 200MB+ bundles; conflicts with existing chat loop; Vercel bundle limit risk
- Flask-Admin / SQLAlchemy — requires rewriting all existing raw psycopg2 queries; Bootstrap clashes with Tailwind
- Playwright / PyMuPDF — compiled C extensions or Chromium binary would exceed Vercel's 500MB bundle limit
- Scrapy — full crawling framework for a bounded URL list; disproportionate overhead

**Version upgrades required:**
- `openai` 2.24.0 → 2.29.0 (instructor compatibility)
- `beautifulsoup4` 4.12.3 → 4.14.3 (bug fixes, existing lib)
- `lxml` 5.1.0 → 6.0.2 (existing lib)
- `autoevals` (unversioned) → pin to 0.0.130

**Model choice for extraction:** GPT-4o (not GPT-4o-mini). Trait extraction from noisy WhatsApp data requires stronger reasoning. GPT-4o-mini is appropriate for admin UI interactions and guardrail evals.

### Expected Features

**Must have (table stakes for milestone):**
- Personality trait extraction from WhatsApp chat logs per sender — the entire milestone depends on this
- Personality trait extraction from blog posts (Mihir's WordPress, Venki's Google Drive PDF) — blogs reveal coaching voice, not group-chat voice
- Personality profiles in database — structured, typed fields; not free-text; foundation for everything
- Admin UI: view and edit personality traits per team member — mandatory human review gate
- Admin UI: gear preferences per rider — grounds Shriram's gear-specific coaching
- Admin UI: coach assignment and topic routing — replaces hardcoded `_BIKE_KEYWORDS` inline code
- Coaching guardrails as database config — replaces hardcoded prompt strings; enables runtime admin editing
- Braintrust eval suite validating guardrail compliance — verifies rules actually hold with LLM-as-judge scorer
- Knowledge base expansion — crawl resources spreadsheet URLs, embed into existing pgvector table

**Should have (differentiation):**
- Source quote display in admin trait review — shows evidence that justified each extracted trait (prevents "trust the AI" problem)
- Soft-delete and edit history on all config tables — mandatory for an admin-only tool with no second approver
- Contextual modifiers on personality traits — humor_type applies in casual contexts, not when rider expresses frustration
- Structured gear schema — `{ "bike": { "brand", "model", "tire_width_mm" }, "value_orientation" }` so data is usable for coaching, not just display

**Defer to next milestone (explicitly flagged in PROJECT.md):**
- Chatbot using personality traits to match response tone to each rider (live chat integration of rider profiles)
- Coach personas dynamically generated from personality profiles in live chat

These two deferred items require the profiles to exist — which this milestone builds — and a separate milestone to wire them into live response generation. Building the data without immediately using it allows admin review of profile quality first.

**Anti-features (do not build):**
- Real-time personality inference during conversation (high latency, noisy signal, contradicts stored-profile architecture)
- Automatic persona updates without admin review (a few unusual messages can corrupt a persona)
- Rider-facing personality profile transparency surfaced explicitly (privacy risk in a small team)
- Scheduled blog scraping (one-time extraction is sufficient; scheduling adds infrastructure for marginal value)

### Architecture Approach

The codebase follows a strict three-layer architecture (routes → services → models → database) that new features must respect. All new code slots into existing layers or extends existing patterns; no new frameworks, blueprints, or database access patterns are introduced. Heavy offline operations (extraction, crawling, embedding) run as CLI scripts in `scripts/`, never as Flask request handlers, to stay within Vercel serverless constraints. The chat pipeline is extended — not replaced — by adding a new `assemble_coach_context()` function call after existing context assembly functions.

**Major components:**
1. `scripts/extract_personality_whatsapp.py` and `extract_personality_blog.py` — offline extraction scripts; follow `import_whatsapp.py` pattern; write to `personality_profile` table; use instructor + GPT-4o
2. Schema tables (`personality_profile`, `gear_preference`, `coach_assignment`, `coaching_guardrail`) — JSONB for flexible trait fields; structured typed columns for queryable fields; no caching (admin edits must take immediate effect)
3. `services/chat_service.py` additions — `assemble_coach_context()` loads coach persona + guardrails from DB; `select_coach_for_message()` replaces hardcoded `_BIKE_KEYWORDS` with DB-driven coach assignment lookup
4. Admin routes in `routes/admin.py` — appended to existing blueprint, not a new blueprint; Jinja2 + Tailwind following established admin patterns; vanilla `fetch()` for per-field AJAX saves
5. `scripts/embed_resources.py` — crawls resources spreadsheet URLs; reuses `whatsapp_chunk` table with `web_*` source prefix; existing HNSW index covers all rows automatically
6. `evals/eval_guardrail_dynamic.py` — loads guardrails from DB at eval time; generates test cases per rule; uses autoevals `LLMClassifier` for semantic compliance scoring

**Key data flow — chat request:**
```
User message → moderate → assemble_rider_context → assemble_team_context
→ assemble_coach_context [NEW: DB-loaded persona + guardrails]
→ select_coach_for_message [NEW: DB-driven routing]
→ run_agent_loop → classify_intent → RAG retrieval → stream response
```

**Key integration point:** The `assemble_coach_context()` call is additive — the existing `CHAT_SYSTEM_PROMPT` string stays in place for structural randonneuring knowledge; coach personality is layered on top via a `<coach_context>` XML block in the same format as existing `<rider_data>` and `<team_context>` blocks.

### Critical Pitfalls

1. **Prompt injection via admin-editable personality fields** — Database-sourced text injected into system prompts is a prompt injection vector even with admin-only access. Prevention: store traits as structured typed fields with character limits, wrap all DB-sourced text in explicit `<personality_context>` XML boundaries, add regex validation for instruction-pattern keywords in admin UI. Address in schema design phase, before admin UI ships.

2. **Prose guardrails are soft guidance, not enforcement** — "Do not give medical advice" in a system prompt is a preference the model will override when it judges helpfulness to be more important in context. Prevention: two-stage architecture — intent/guardrail classifier as a separate pass before the persona prompt is applied; canned redirect messages from config for DENY rules, not model-generated responses. Address before Braintrust eval suite, because evals must test the enforcement mechanism, not just model behavior.

3. **Personality extraction producing shallow or inverted traits** — Group WhatsApp chat has low information density for personality modeling (reactions, short acknowledgments, context-dependent messages). The model can extract group-performance behavior instead of coaching style. Prevention: use GPT-4o (not mini); pre-filter to messages over 15 words; weight blog content more heavily than group chat in extraction prompt; require admin review with source quotes before traits go live.

4. **Uncanny valley persona from trait over-application** — Extracted traits applied unconditionally produce caricatures (every Shriram response includes gear recommendations regardless of topic). Prevention: store contextual modifiers per trait; add emotional context detection to suppress humor when rider expresses frustration; require Venki and Shriram to review 5-10 sample AI responses before deployment.

5. **Knowledge base pollution from boilerplate and duplicate URLs** — Crawled pages embed navigation menus, cookie banners, and repeated content from multiple alias URLs. Prevention: use trafilatura for content extraction (not raw BeautifulSoup); store SHA-256 content hash per chunk and reject re-ingestion of identical content; validate chunk quality (minimum 100 characters, low symbol ratio); per-URL status tracking with `ON CONFLICT DO NOTHING` for idempotent re-runs.

---

## Implications for Roadmap

The architecture research is unambiguous about ordering — it maps directly to a six-phase build sequence based on dependency analysis. The critical path is: schema → extraction → chat integration → admin → evals → knowledge expansion. The last phase (knowledge expansion) is independent and can be parallelized.

### Phase 1: Data Foundation

**Rationale:** Everything depends on the schema. Admin UI, extraction scripts, chat integration, and evals all require `personality_profile`, `gear_preference`, `coach_assignment`, and `coaching_guardrail` tables plus model functions in `models.py`. No other phase can meaningfully proceed without these. Manually seed Shriram and Venki profiles from the content currently hardcoded in `CHAT_SYSTEM_PROMPT` so the system is functional and testable before extraction scripts are ready.
**Delivers:** Schema migration (`schema/personality_schema.sql` + `migrations/apply_migration_007.py`), all model functions in `models.py`, manually seeded coach profiles
**Addresses:** Features 3 (personality profiles in DB), foundational parts of Features 6 and 7
**Avoids:** Pitfall 1 (define typed fields and character limits here, before any UI is built), Pitfall 8 (add `updated_at`, `deleted_at`, edit history JSONB columns to every config table from the start)
**Research needed:** No — direct codebase-derived patterns; HIGH confidence

### Phase 2: Personality Extraction

**Rationale:** Extraction runs offline as CLI scripts with no frontend dependency. Once the schema exists, extraction can run and populate real data for admin review. Blog extraction must run alongside WhatsApp extraction — not deferred — because coaching persona quality depends on both sources. Run both extractors before admin UI is built so admins have real data to review, not placeholder data.
**Delivers:** `scripts/extract_personality_whatsapp.py`, `scripts/extract_personality_blog.py` (WordPress URL + Google Drive PDF), real personality profiles for all coaches and riders
**Uses:** `instructor` 1.14.5, `trafilatura` 2.0.0, `pdfplumber` 0.11.9, GPT-4o
**Addresses:** Features 1, 2, 14 (extraction with source quote evidence stored alongside traits)
**Avoids:** Pitfall 3 (use GPT-4o; pre-filter messages; structured schema with examples), Pitfall 7 (route by URL type before extracting; validate content length with a minimum threshold), Pitfall 11 (WhatsApp format inconsistency; handle iOS and Android formats, continuation lines, system messages), Pitfall 12 (weight blog content more than group chat in extraction prompt)
**Research needed:** No for infrastructure; LOW for prompt design — test extraction quality empirically on 20-30 real messages before finalizing the prompt schema

### Phase 3: Chat Integration

**Rationale:** With real personality data in the database, the chat pipeline can be wired to use it. This phase replaces the hardcoded `CHAT_SYSTEM_PROMPT` persona and the hardcoded `_BIKE_KEYWORDS` routing with database-driven alternatives. This is the highest-impact change for the end user and should happen early to allow testing with real conversations. Admin can observe the effect of their data on actual chatbot responses before the admin UI is fully built.
**Delivers:** `assemble_coach_context()` function in `services/chat_service.py`, `select_coach_for_message()` replacing `_BIKE_KEYWORDS`, gear preference loading into conversation context
**Addresses:** Features 6 (coach assignment), 7 (guardrails as config), 10 (rider tone matching), 11 (data-derived coach personas), 12 (gear-aware recommendations), 13 (value orientation in recommendations)
**Avoids:** Pitfall 1 (wrap all DB-sourced personality text in `<personality_context>` XML boundary), Pitfall 2 (implement two-stage architecture: classifier pass before persona prompt), Pitfall 4 (add contextual modifiers; test with Venki and Shriram before deploy)
**Research needed:** Moderate — the two-stage guardrail classifier architecture needs careful design to avoid latency impact; validate approach with a small prototype before full implementation

### Phase 4: Admin UI

**Rationale:** By this phase, real extracted personality data exists in the database and the chat pipeline uses it. Admins now have something meaningful to review and tune. The admin UI is the verification and correction layer — it should be built after there is real data to show, not before. Building admin UI against placeholder data produces forms that don't reflect real data complexity.
**Delivers:** Admin routes for personalities, gear preferences, coach assignments, guardrails — all in `routes/admin.py` (extended, not a new blueprint); Jinja2 templates following existing admin patterns; per-field AJAX saves via vanilla `fetch()`
**Addresses:** Features 4 (trait view/edit with source quotes), 5 (gear preferences CRUD), 6 (coach assignment), 7 (guardrail management)
**Avoids:** Pitfall 8 (soft-delete, confirmation dialogs for destructive ops, edit history visible in UI), Pitfall 10 (gear preference schema must be structured — dropdowns and typed fields, not text inputs — defined in Phase 1 and enforced here)
**Research needed:** No — well-documented Flask + Jinja2 patterns; existing admin routes are direct templates

### Phase 5: Braintrust Evals

**Rationale:** Evals validate that the guardrail configuration built in Phase 4 is actually enforced by the chat pipeline from Phase 3. This is not a "nice to have" validation step — it is the verification mechanism that gives confidence before real riders use the configured personas. Start with adversarial and boundary test cases before writing eval infrastructure; happy-path-only evals give false confidence.
**Delivers:** `evals/eval_guardrail_dynamic.py` loading guardrails from DB at eval time, LLM-as-judge scorer using `autoevals.LLMClassifier`, test cases covering clear violations, clear passes, boundary cases, and adversarial inputs per guardrail
**Uses:** `braintrust` 0.9.0, `autoevals` 0.0.130 (both already in dev dependencies)
**Addresses:** Feature 8 (Braintrust eval suite for guardrail validation)
**Avoids:** Pitfall 2 (evals must test the classifier enforcement layer, not just end-to-end model behavior), Pitfall 9 (write 3 test cases per guardrail — clear violation, clear pass, boundary ambiguous — plus 2 adversarial cases)
**Research needed:** No — existing `evals/eval_guardrail.py` is the direct template; well-documented Braintrust pattern

### Phase 6: Knowledge Base Expansion

**Rationale:** This phase is architecturally independent — it reuses `whatsapp_chunk` with a `web_*` source prefix and the existing HNSW index covers all new rows automatically. It can run in parallel with Phases 3-5, but is listed last because: (a) the randonneuring knowledge base from Phase 1 already provides a working RAG foundation, and (b) knowledge quality filtering needs time and care. The Google Sheets URL export approach avoids the Google API client entirely for public spreadsheets.
**Delivers:** `scripts/embed_resources.py` with per-URL processing, content quality filtering, SHA-256 deduplication, ingestion state tracking; admin `/admin/knowledge` page showing per-source chunk counts and embedding status
**Uses:** `trafilatura` 2.0.0 (same library as Phase 2), existing `text-embedding-3-small` embedding pattern from `import_whatsapp.py`
**Addresses:** Feature 9 (knowledge base expansion from resources spreadsheet)
**Avoids:** Pitfall 5 (use trafilatura not raw BeautifulSoup; minimum chunk quality threshold), Pitfall 6 (content hash deduplication; normalized URL uniqueness check; `ON CONFLICT DO NOTHING`), Pitfall 13 (per-URL operation model, not bulk; Vercel-safe design)
**Research needed:** Low — crawling and embedding patterns are well-established; verify robots.txt compliance per target domain before embedding

---

### Phase Ordering Rationale

The ordering follows strict dependency chains identified in the architecture research:

- **Schema first (Phase 1):** All other phases read or write to tables that don't exist yet. The manually seeded coach profiles make Phase 3 testable before Phase 2 is complete.
- **Extraction before admin UI (Phase 2 before Phase 4):** Admin UI built against real extracted data is significantly more useful than one built against placeholder data. Admins need to see actual extraction artifacts — source quotes, confidence signals — to evaluate and tune.
- **Chat integration early (Phase 3):** Wiring personality data into the chat pipeline as soon as real data exists allows testing with real conversations throughout Phases 4-6. It also creates urgency for the human review gate in Phase 4 — admins can see the chatbot's actual behavior and correct what they observe.
- **Evals after admin (Phase 5 after Phase 4):** Evals load guardrails from the database. Guardrails must be configured in the admin UI before there is anything meaningful to evaluate.
- **Knowledge expansion independent (Phase 6):** This phase shares no tables with personality/guardrail infrastructure and can run in parallel after Phase 1 schema is applied. It is sequenced last to prioritize the core personality-driven coaching capability.

### Parallelization Opportunities

- Phase 6 can begin after Phase 1 is complete and run in parallel with Phases 2-5
- Within Phase 2, WhatsApp extraction and blog/PDF extraction are independent scripts and can run simultaneously

### Research Flags

**Phases needing deeper research during planning:**
- **Phase 2 (extraction prompt design):** The personality trait schema and extraction prompt quality directly determine persona quality for the entire milestone. Validate empirically on 20-30 real WhatsApp messages before finalizing. The taxonomy (humor_type, directness, etc.) is defined in research but the extraction prompt needs iteration.
- **Phase 3 (two-stage guardrail classifier):** The architecture recommendation is clear but the latency trade-off of adding a classifier pass per message needs quantification before committing. Test with a small prototype using a fast model (GPT-4o-mini) for the classifier step.

**Phases with standard patterns (skip research):**
- **Phase 1 (schema):** Direct derivation from existing codebase patterns; ARCHITECTURE.md provides the complete SQL
- **Phase 4 (admin UI):** Existing `routes/admin.py` is the direct template; Jinja2 + Tailwind patterns are established
- **Phase 5 (evals):** Existing `evals/eval_guardrail.py` is the direct template; autoevals LLMClassifier is the well-documented pattern
- **Phase 6 (knowledge expansion):** Existing `scripts/import_whatsapp.py` is the direct template; trafilatura + embedding pattern is established

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All new library choices verified via PyPI JSON API; version numbers confirmed; existing codebase directly analyzed to justify each selection and each exclusion |
| Features | MEDIUM | Feature scope derived from PROJECT.md (HIGH) and domain knowledge about AI coaching platforms (MEDIUM); no live competitor research was possible |
| Architecture | HIGH | All findings from direct inspection of actual source files (`chat_service.py`, `admin.py`, `models.py`, `import_whatsapp.py`, `eval_guardrail.py`); no inference required |
| Pitfalls | HIGH | All critical pitfalls are well-documented field patterns (OWASP LLM Top 10 concepts); Vercel timeout limits confirmed from docs; extraction quality risk is empirically grounded |

**Overall confidence:** HIGH for build decisions; MEDIUM for persona quality outcomes (the extraction prompt and human review gate are the key unknowns)

### Gaps to Address

- **Google Drive PDF authentication:** STACK.md flags that if Venki's PDF is not publicly shared, OAuth credential management complexity is added. Confirm with Venki that the file is publicly accessible before building Phase 2 extraction. If private, add `google-api-python-client` to the dependency list and plan for credential setup.

- **WhatsApp export format from actual exports:** The existing `scripts/import_whatsapp.py` parser was written for one specific export. Before Phase 2, audit the actual export files for Shriram and Venki to identify the timestamp format, multi-line message handling, and media message patterns. Pitfall 11 covers the known risks; empirical verification against real files is the mitigation.

- **Extraction prompt quality:** This is the highest-uncertainty item in the entire milestone. The structured schema is defined (in STACK.md and FEATURES.md), but prompt iteration against real WhatsApp data is required before finalizing. Allocate time in Phase 2 planning for 2-3 extraction test runs with admin review before committing to the final schema.

- **Venki and Shriram review gate timing:** Both coaches must review AI persona output before Phase 3 goes live to real riders. This is a human coordination dependency, not a technical one. Plan for this review in the Phase 3 timeline and do not skip it under time pressure.

- **Vercel timeout limit (Hobby vs Pro):** STACK.md cites 300s on Hobby plan (fluid compute) but PITFALLS.md cites 60s. The discrepancy should be resolved by checking the current Vercel plan and current documentation before Phase 3 (chat integration) and Phase 6 (knowledge expansion admin triggers) are scoped.

---

## Sources

### Primary (HIGH confidence)
- Direct codebase inspection — `services/chat_service.py`, `services/openai_coach.py`, `routes/admin.py`, `models.py`, `scripts/import_whatsapp.py`, `evals/eval_guardrail.py`, `evals/eval_e2e.py`, `schema/whatsapp_schema.sql`, `schema/schema.sql`
- PyPI JSON API (`/pypi/{package}/json`) — all version numbers for instructor, trafilatura, pdfplumber, autoevals
- Vercel documentation — serverless function duration limits and bundle size constraints
- GitHub: instructor-ai/instructor README — usage pattern, retry behavior, Pydantic integration
- `.planning/PROJECT.md` — feature requirements, out-of-scope boundaries, external data sources

### Secondary (MEDIUM confidence)
- Domain knowledge: AI coaching platform patterns for personality extraction, guardrail configuration, admin interface design
- OWASP Top 10 for LLM Applications — prompt injection (LLM01), sensitive information disclosure (LLM02) patterns
- trafilatura capabilities — from PyPI description and GitHub README (readthedocs was unavailable)

### Tertiary (LOW confidence)
- LangChain/SQLAlchemy bundle sizes — rationale for exclusion; verify before dismissing if requirements change
- Pitfall 14 (privacy): social/trust question as much as technical; verify against the team's actual comfort level with profiling before committing to the privacy posture

---
*Research completed: 2026-03-17*
*Ready for roadmap: yes*
