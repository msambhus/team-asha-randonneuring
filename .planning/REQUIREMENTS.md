# Requirements: Team Asha Randonneuring

**Defined:** 2026-03-14 (Milestone 1) | 2026-03-17 (Milestone 2)
**Core Value:** Coaching that feels like it comes from a real teammate who knows you — matching each rider's communication style and each coach's authentic personality, grounded in actual conversation data.

## Milestone 1 Requirements (Complete)

All 62 requirements from Milestone 1 are code complete. See git history for details.
Categories: Infrastructure (6), Security (11), Chat Experience (7), Coaching & Knowledge (6), Personalization (3), Agentic Pipeline (10), Evals & Observability (6), WhatsApp Knowledge Base (10), Image Previews (9).

## Milestone 2 Requirements (Active)

### Personality Extraction

- [x] **EXTR-01**: System extracts personality traits per person from WhatsApp exported chat logs using GPT-4o
- [ ] **EXTR-02**: System extracts personality traits from blog posts (WordPress URL and Google Drive PDF)
- [x] **EXTR-03**: Extraction captures tone register, humor type, directness level, encouragement style, domain bias, signature phrases, response length tendency, and question-asking behavior
- [x] **EXTR-04**: Extraction stores 3-5 source example quotes per trait as evidence for admin verification
- [x] **EXTR-05**: Extraction pre-filters WhatsApp noise (media messages, system messages, short reactions) before trait analysis
- [x] **EXTR-06**: Extraction assigns confidence level per trait based on source message volume (high/medium/low)
- [ ] **EXTR-07**: Extraction merges blog-derived traits with chat-derived traits, weighting by confidence

### Personality Profiles

- [ ] **PROF-01**: Database stores personality profiles with structured, queryable fields (not free-form text blobs)
- [ ] **PROF-02**: Coach profiles include tone, humor type, directness, signature phrases, topic biases, and topics allowed
- [ ] **PROF-03**: Rider profiles include preferred formality, humor sensitivity, encouragement preference, and technical depth
- [ ] **PROF-04**: Each profile tracks extraction source (whatsapp/blog/manual), extraction date, source message count, and confidence
- [ ] **PROF-05**: Profile changes are auditable (last_modified_by, timestamp)

### Gear Preferences

- [ ] **GEAR-01**: Admin can capture gear preferences per rider: bike (make/model/year/material), wheels/tires, lighting, bags, navigation, kit
- [ ] **GEAR-02**: Admin can set value orientation per rider (budget/mid-range/premium/buy-once-buy-right)
- [ ] **GEAR-03**: Gear data is loadable into chatbot conversation context for grounded recommendations

### Admin UI — Personality

- [ ] **ADMN-01**: Admin can view list of all team members with profile completeness indicator
- [ ] **ADMN-02**: Admin can view and edit personality traits per person with structured fields (dropdowns for enumerations, text for phrases)
- [ ] **ADMN-03**: Admin can see source example quotes alongside each trait for verification
- [ ] **ADMN-04**: Admin can see confidence badge per trait (warns when LOW confidence)
- [ ] **ADMN-05**: Admin can trigger re-extraction per person from source data
- [ ] **ADMN-06**: Admin can view and edit gear preferences per rider

### Admin UI — Coaching Config

- [ ] **COACH-01**: Admin can view coach roster with persona status and active/inactive toggle
- [ ] **COACH-02**: Admin can assign topic domains per coach (replaces hardcoded keyword routing)
- [ ] **COACH-03**: Admin can configure routing rules: intent/keyword → coach mapping
- [ ] **COACH-04**: Admin can designate a fallback coach for unrouted queries
- [ ] **COACH-05**: Adding a new coach does not require code changes

### Coaching Guardrails

- [ ] **GUARD-01**: Guardrails stored as structured database rows (rule_type, rule_value, is_active), not hardcoded in prompts
- [ ] **GUARD-02**: Admin can configure topic scope per coach (what each coach can/cannot answer)
- [ ] **GUARD-03**: Admin can configure tone limits (e.g., never shame a rider for fitness)
- [ ] **GUARD-04**: Admin can configure escalation rules (when to deflect to doctor, RUSA, etc.)
- [ ] **GUARD-05**: Admin can toggle individual guardrail rules active/inactive without code deploy
- [ ] **GUARD-06**: Guardrails are version-stamped so Braintrust evals correlate to specific rule sets
- [ ] **GUARD-07**: Guardrails loaded at conversation start and injected into system prompt dynamically

### Evaluation

- [ ] **EVAL2-01**: Braintrust eval dataset covers scope enforcement (correct coach handles correct topics)
- [ ] **EVAL2-02**: Braintrust eval dataset covers topic blocking (off-cycling queries get redirected)
- [ ] **EVAL2-03**: Braintrust eval dataset covers medical deflection (health questions get "consult a doctor")
- [ ] **EVAL2-04**: Braintrust eval dataset covers persona consistency (Shriram mentions gear, Venki doesn't volunteer gear recs)
- [ ] **EVAL2-05**: Eval uses LLM-as-judge scoring (not keyword matching) for semantic compliance
- [ ] **EVAL2-06**: Eval results can be compared across guardrail rule versions

### Knowledge Base Expansion

- [ ] **KB-01**: System crawls URLs from the resources Google Sheets spreadsheet
- [ ] **KB-02**: Crawled content extracted (main text only, no nav/footer/ads), chunked, and embedded using text-embedding-3-small
- [ ] **KB-03**: Embedded content stored in existing pgvector table with source tagging (web_* prefix)
- [ ] **KB-04**: Admin can view list of embedded sources with URL, embed date, and chunk count
- [ ] **KB-05**: Admin can trigger re-embed per source (refresh stale content)
- [ ] **KB-06**: Admin can remove all embeddings from a specific source

## v3 Requirements (Deferred)

### Personalized Chat Responses

- **PCHAT-01**: Chatbot adapts response tone, humor, and depth to match each rider's communication style profile
- **PCHAT-02**: Coach personas dynamically generated from personality profile data (not hardcoded prompts)
- **PCHAT-03**: Rider self-service profile form for gear preferences and communication style preferences

### Advanced Features (from Milestone 1)

- **ADV-01**: Token budget dashboard — per-user daily/monthly usage
- **ADV-02**: Conversation summarization for long histories
- **ADV-03**: Per-message feedback (thumbs up/down) with review workflow
- **ADV-04**: Export conversation as markdown
- **ADV-05**: Context-aware page initialization — widget pre-populates based on current page
- **ADV-06**: Conversation search

## Out of Scope

| Feature | Reason |
|---------|--------|
| Real-time personality inference during conversation | High latency; noisy from few messages; contradicts stored profile architecture |
| Automatic coach persona updates from new chat data | Profile corruption risk without admin review |
| Rider-facing personality profile transparency | Could feel invasive for small team; no product value |
| Automated blog scraping on schedule | Blogs update infrequently; manual re-extraction sufficient |
| Multi-coach simultaneous responses | Committee feel; single-coach routing is correct |
| Personality trait suggestions based on other riders | Individual profiling is the goal |
| Real-time WhatsApp integration | Uses exported .txt files only |
| Free-form SQL generation | Security risk — LLM output IS the injection vector |
| Voice input/output | Text-only; complexity not justified |
| Mobile app | Web-first |
| LangChain/LangGraph | 50MB deps, slow cold starts, conflicts with psycopg2 |

## Traceability

<!-- Updated during roadmap creation -->

| Requirement | Phase | Status |
|-------------|-------|--------|
| PROF-01 | Phase 7 | Pending |
| PROF-02 | Phase 7 | Pending |
| PROF-03 | Phase 7 | Pending |
| PROF-04 | Phase 7 | Pending |
| PROF-05 | Phase 7 | Pending |
| GUARD-01 | Phase 7 | Pending |
| GUARD-06 | Phase 7 | Pending |
| EXTR-01 | Phase 8 | Complete |
| EXTR-02 | Phase 8 | Pending |
| EXTR-03 | Phase 8 | Complete |
| EXTR-04 | Phase 8 | Complete |
| EXTR-05 | Phase 8 | Complete |
| EXTR-06 | Phase 8 | Complete |
| EXTR-07 | Phase 8 | Pending |
| GUARD-07 | Phase 9 | Pending |
| COACH-02 | Phase 9 | Pending |
| COACH-03 | Phase 9 | Pending |
| COACH-04 | Phase 9 | Pending |
| COACH-05 | Phase 9 | Pending |
| GEAR-03 | Phase 9 | Pending |
| ADMN-01 | Phase 10 | Pending |
| ADMN-02 | Phase 10 | Pending |
| ADMN-03 | Phase 10 | Pending |
| ADMN-04 | Phase 10 | Pending |
| ADMN-05 | Phase 10 | Pending |
| ADMN-06 | Phase 10 | Pending |
| GEAR-01 | Phase 10 | Pending |
| GEAR-02 | Phase 10 | Pending |
| COACH-01 | Phase 10 | Pending |
| GUARD-02 | Phase 10 | Pending |
| GUARD-03 | Phase 10 | Pending |
| GUARD-04 | Phase 10 | Pending |
| GUARD-05 | Phase 10 | Pending |
| EVAL2-01 | Phase 11 | Pending |
| EVAL2-02 | Phase 11 | Pending |
| EVAL2-03 | Phase 11 | Pending |
| EVAL2-04 | Phase 11 | Pending |
| EVAL2-05 | Phase 11 | Pending |
| EVAL2-06 | Phase 11 | Pending |
| KB-01 | Phase 12 | Pending |
| KB-02 | Phase 12 | Pending |
| KB-03 | Phase 12 | Pending |
| KB-04 | Phase 12 | Pending |
| KB-05 | Phase 12 | Pending |
| KB-06 | Phase 12 | Pending |

**Coverage:**
- Milestone 2 requirements: 45 total
- Mapped to phases: 45
- Unmapped: 0 (100% coverage)

---
*Requirements defined: 2026-03-14 (M1) / 2026-03-17 (M2)*
*Last updated: 2026-03-17 after Milestone 2 roadmap creation*
