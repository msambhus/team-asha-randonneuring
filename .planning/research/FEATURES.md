# Feature Landscape: Personality-Driven AI Coaching

**Domain:** Personality-driven AI coaching chatbot with admin management
**Project:** Team Asha Randonneuring — Milestone 2
**Researched:** 2026-03-17
**Confidence:** MEDIUM (based on project context + domain knowledge; live web search unavailable)

---

## Context: What Already Exists

The v1.0 milestone delivered a working agentic chatbot. This milestone is additive — it layers
personality data and admin control on top of an existing, functioning system. Features are
categorized relative to this milestone's goal: replacing hardcoded personas with data-driven,
admin-configurable personality profiles.

**Already shipped (not in scope):**
- Floating chat widget with streaming SSE
- Agentic intent classification + tool calls
- WhatsApp RAG pipeline (pgvector)
- Braintrust observability + eval framework
- Hardcoded coach personas (Shriram for bikes, Venki for everything else)
- Off-topic guardrails (hardcoded in system prompt)
- Admin panel for ride management

---

## Table Stakes

Features that must exist for the milestone to deliver its stated value. Missing any of these means
the "personality-driven coaching" goal is not achieved — the chatbot remains persona-free or
hardcoded.

### 1. Personality Trait Extraction from WhatsApp Chat Logs

**Why expected:** The entire milestone is predicated on data-driven personality profiles. Without
extraction, there are no profiles. Everything else depends on this.

**What to extract from conversation data:**
- **Tone register** — formal vs. informal, technical vs. accessible
- **Humor type** — dry/deadpan, sarcastic, self-deprecating, teasing, wordplay
- **Directness level** — blunt and terse vs. long-winded and qualifying
- **Encouragement style** — pushes hard, validates effort, uses tough love, stays neutral
- **Domain bias** — what topics they naturally expand on (Shriram: gear; Venki: philosophy/strategy)
- **Signature phrases and expressions** — recurring vocabulary patterns that mark their voice
- **Response length tendency** — short punchy responses vs. paragraph-length explanations
- **Question-asking behavior** — do they probe with follow-up questions or prescribe directly?

**How to present extracted traits:**
- Store as structured key-value fields in the database (not free-form text blobs)
- Each field should be human-readable and admin-editable (e.g., `humor_type: "sarcastic"`)
- Include a confidence score per trait derived from message volume (low volume = LOW confidence)
- Show the source signal (e.g., "based on 847 messages in 2023-2024 exports")
- Surface 3-5 example phrases that illustrate each detected trait — this lets admins verify accuracy

**Complexity:** High — requires GPT-4o (not GPT-4o-mini) for reliable extraction; needs careful
prompt design to avoid hallucinated traits; WhatsApp exports have noise (media messages, system
messages, one-word responses) that must be filtered first.

**Dependency:** Must precede admin trait editing (you can't edit what doesn't exist).

---

### 2. Personality Trait Extraction from Blog Posts

**Why expected:** The PROJECT.md explicitly scopes Mihir's WordPress blog and Venki's Google Drive
PDF as extraction sources. Blog writing reveals more considered, reflective personality traits that
WhatsApp chat (reactive, conversational) does not.

**What blogs add over chat data:**
- Written voice vs. spoken/typed voice — more deliberate word choices
- Narrative structure — how they organize and tell a story
- Values signals — what they choose to write about and how they frame it
- Aspirational language vs. pragmatic language

**How to handle:**
- WordPress blog: fetch via URL (already constrained to Mihir's blog — one URL)
- Google Drive PDF: parse and extract text, then process same as chat
- Merge blog-derived traits with chat-derived traits — weight by confidence

**Complexity:** Medium — blog parsing is simpler than WhatsApp (no format noise), but PDF
extraction from Google Drive needs a reliable text extraction step (PyMuPDF or pdfminer).

**Dependency:** Requires same extraction pipeline as WhatsApp; must run before admin trait review.

---

### 3. Personality Profiles Stored in Database (Structured, Editable)

**Why expected:** Without database persistence, traits can't be reused across requests, can't be
admin-edited, and can't evolve. This is the data model foundation for the entire milestone.

**Schema must include:**
- `person_id` — linked to existing rider/user record
- `role` — `coach` | `rider` (coaches get persona-style traits; riders get communication style traits)
- **For coaches:** tone, humor type, directness, signature phrases, topic biases, topics_allowed (FK to guardrails)
- **For riders:** preferred_formality, humor_sensitivity, encouragement_preference, technical_depth
- `extraction_source` — `whatsapp` | `blog` | `manual`
- `extraction_date` — when the extraction ran
- `source_message_count` — how much data backed this extraction
- `confidence` — `high` | `medium` | `low`
- `last_modified_by` — tracks admin edits for audit trail

**Complexity:** Medium — schema design is straightforward; the complexity is in designing fields
flexible enough to capture diverse personalities without becoming so generic they lose meaning.

**Dependency:** Required by all admin UI and chatbot persona features.

---

### 4. Admin Page: View and Edit Personality Traits Per Team Member

**Why expected:** The PROJECT.md explicitly requires this. Automated extraction is imperfect —
admins need to review, correct, and tune the extracted traits before they drive the chatbot.

**Standard controls for this type of admin UI:**
- List view: all team members with profile completeness indicator (e.g., "8/10 traits configured")
- Detail view per person: each trait as an editable field (dropdowns for enumerations, text for phrases)
- Inline save (per-field or per-section, not full-page submit) — reduces accidental overwrites
- "Re-extract" button per person — triggers re-extraction from source data without full page reload
- Extraction source display — shows which messages/content drove each trait ("based on 847 messages")
- Side-by-side example phrases — shows actual quotes from their messages to validate trait accuracy
- Confidence badge per trait — warns admin when a trait has LOW confidence (thin source data)
- Change history log — timestamped record of who changed what (simple append-only log table)

**What NOT to do:**
- Do not make traits a free-form textarea — this destroys queryability and prompt composability
- Do not auto-apply extraction results without admin review step

**Complexity:** Medium — standard CRUD UI; the UX consideration is making trait fields intuitive
for non-technical admins (Mihir) without requiring them to understand LLM prompting.

**Dependency:** Requires personality profiles DB schema.

---

### 5. Admin Page: Gear Preferences Per Rider

**Why expected:** Shriram's persona is explicitly described as recommending gear and recognizing
riders by their bikes. Gear preference data is the raw material that makes Shriram's coaching
specific rather than generic.

**What to track per rider:**
- **Bikes:** make, model, year, frame material (e.g., "Cannondale SuperSix EVO, carbon, 2022")
- **Wheels/tires:** current setup, tubeless or not, preferred tire widths
- **Lighting:** front and rear systems (randonneuring-critical — night riding requires serious lights)
- **Bags/luggage:** handlebar bag, frame bag, saddle bag brands and models
- **Navigation:** GPS device or phone mount
- **Kit:** jersey/bibs brand preferences
- **Value orientation:** budget-conscious | mid-range | premium | "buy once buy right"
- **Upgrade recency:** tendency to upgrade frequently vs. ride what they have

**How to capture:**
- Manual data entry by admin (not extracted — riders don't always discuss gear in chat)
- Optionally, riders self-report via a profile form (future phase)
- WhatsApp extraction can pre-populate gear mentions (Shriram's coaching style will surface these)

**Complexity:** Low — this is straightforward CRUD with a defined schema. The value is not in the
UI complexity but in having the data available to the chatbot at query time.

**Dependency:** Chatbot must load gear preferences at conversation start to ground Shriram's
recommendations.

---

### 6. Admin Page: Coach Assignment and Configuration

**Why expected:** The current system hardcodes coach routing (bike keywords → Shriram, everything
else → Venki). The milestone makes this configurable. Without this admin page, the routing remains
hardcoded and personality data is unused.

**Controls that are standard for this type of system:**
- Coach roster: list of all configured coaches with their persona status
- Topic assignment: which coaches handle which topic domains (dropdowns per coach)
- Routing rules: keyword/intent → coach mapping (replaces the current hardcoded check)
- Active/inactive toggle: temporarily disable a coach persona without deleting their config
- Fallback coach designation: which coach handles anything not explicitly routed

**Complexity:** Low to Medium — the routing logic is simple; the challenge is designing the config
schema so that adding a third or fourth coach doesn't require code changes.

**Dependency:** Requires coach personality profiles to exist before assignment makes sense.

---

### 7. Coaching Guardrails as Structured Config (Not Hardcoded Prompts)

**Why expected:** PROJECT.md explicitly lists this: "Coaching guardrails stored as structured
config, not hardcoded in prompts." The existing system has hardcoded guardrails in the system
prompt string in `services/openai_coach.py`. This means changing them requires a code deploy.

**What guardrails typically control in AI coaching platforms:**
- **Topic scope per coach:** Shriram can answer gear questions; Venki can answer training questions;
  neither should answer medical questions
- **Escalation rules:** when to deflect ("consult a doctor," "contact RUSA directly")
- **Tone limits:** "never shame a rider for their fitness," "never make medical diagnoses"
- **Response constraints:** maximum response length, citation requirements, disclaimer requirements
- **Off-topic behavior:** how to respond to sports outside cycling, politics, personal life questions

**Storage format:**
- Database table with one row per rule: `coach_id`, `rule_type`, `rule_value`, `is_active`
- Rules are loaded at conversation start and injected into the system prompt dynamically
- Admin UI allows toggling rules active/inactive and editing rule values
- Version-stamped rules (so Braintrust evals can be correlated to specific rule sets)

**Complexity:** Medium — the schema is simple; the complexity is in how rules are composed into
prompts without creating contradictory or bloated system prompts. Each rule should be a short,
declarative statement.

**Dependency:** Required before Braintrust eval suite can validate guardrails.

---

### 8. Braintrust Eval Suite for Guardrail Validation

**Why expected:** The existing Braintrust integration (Phase 4) already runs evals for intent
classification and data grounding. The milestone adds a guardrail-specific eval dataset. Without
this, there's no verifiable assurance that the admin-configured guardrails are actually enforced
by the LLM.

**What the eval must cover:**
- **Scope enforcement:** questions about Shriram's topic domains routed to Shriram, not Venki
- **Topic blocking:** questions outside cycling receive a redirect (not an answer)
- **Medical deflection:** health/injury questions receive "consult a doctor" (not advice)
- **Tone compliance:** responses for a rider with `humor_sensitivity: low` don't include sarcasm
- **Persona consistency:** Shriram's responses mention gear when relevant; Venki's responses don't
  volunteer gear recommendations

**Eval dataset structure:**
- Input: `{message, rider_profile, coach_assigned, guardrails_active}`
- Expected behavior: `{topic_handled_by, off_topic_redirected, medical_deflected}`
- Scorer: LLM-as-judge checking whether the response matches expected behavior

**Complexity:** Medium — Braintrust SDK is already integrated; this is dataset creation and
scorer design, not infrastructure work.

**Dependency:** Requires guardrails as structured config (so test inputs can specify rule sets).

---

### 9. Knowledge Base Expansion: Embed External Cycling Resources

**Why expected:** PROJECT.md explicitly includes crawling the resources spreadsheet links and
embedding external cycling/randonneuring sites. The existing WhatsApp RAG covers community
knowledge; external resources add authoritative technical knowledge (training methodologies, route
guides, equipment reviews).

**What to crawl:**
- Resources spreadsheet: https://docs.google.com/spreadsheets/d/1UHgJyigNRnOG6J4pZe7LL5mpzxNbipSkTZ-TYs-O3WU
- Crawled pages: fetch, extract main text content, filter to cycling relevance, chunk, embed
- Randonneuring-specific sources: RUSA rules, ACP rulebook excerpts, brevet calendar references

**Admin controls for knowledge base:**
- List of embedded sources with URL, embed date, chunk count
- "Re-embed" button per source (refresh stale content)
- "Remove" button per source (remove all embeddings from that source)
- Source tag per chunk (so retrieval can filter by source type: community vs. authoritative)

**Complexity:** Medium — crawling and embedding is well-trodden (BeautifulSoup + text-embedding-3-
small already in the stack); the complexity is in content quality filtering (don't embed nav bars,
footers, cookie banners) and respecting robots.txt.

**Dependency:** Requires existing pgvector schema from Phase 5 (already shipped).

---

## Differentiators

Features that create genuine competitive advantage for this specific product — things generic
coaching chatbots don't do.

### 10. Per-Rider Communication Style Matching

**Value proposition:** The chatbot adapts its response tone, humor, and depth to match how the
rider communicates — a rider who uses short punchy messages gets short punchy coaching; a rider
who writes paragraphs gets paragraph responses.

**How it works:**
- Rider's personality profile (extracted from their WhatsApp messages) provides communication
  style dimensions
- System prompt includes rider-specific style instructions derived from their profile
- Response quality eval specifically tests whether tone matches the rider profile

**Why differentiating:** Generic AI coaches give everyone the same voice. This gives each rider
the feeling that the AI "knows" them — not just their data, but their communication preferences.

**Complexity:** Low to implement (add rider profile to system prompt context) but High to get
right (requires quality extraction to drive it).

**Dependency:** Requires rider personality profiles (feature 1 and 3).

---

### 11. Data-Derived Coach Personas (Not Fictional Characters)

**Value proposition:** Coach Venki and Coach Shriram are real people the riders know. The AI
versions should feel like them — tongue-in-cheek wisdom for Venki, bike snobbery for Shriram —
not like a generic "friendly cycling coach."

**How it makes the product different:**
- Generic coaching AI: "Great question! Here's some advice about tire selection..."
- Shriram-persona AI: "Of course you're running 28mm. Let me guess — because they came with the
  bike? Let's talk about what you're actually missing."

**What drives the persona:**
- Extracted signature phrases seeded into system prompt examples
- Humor type drives tone of responses
- Topic bias amplifies depth when in-domain

**Complexity:** Medium — the extraction quality determines the persona quality. The prompt
engineering to make a persona feel authentic without becoming a caricature is non-trivial.

**Dependency:** Requires coach personality profiles and admin trait review (to catch extraction errors).

---

### 12. Gear Preference-Aware Recommendations

**Value proposition:** Shriram knows that Mihir rides a Specialized with a 105 groupset. When
Mihir asks about upgrading, Shriram doesn't recommend a new bike — he recommends what would
actually improve Mihir's current setup.

**How it makes the product different:** Generic AI gives gear advice in the abstract. This system
gives advice grounded in what the rider actually owns.

**Implementation:** Gear preferences are loaded into the conversation context alongside Strava
data. The system prompt instructs the coach to reference the rider's known setup when relevant.

**Complexity:** Low (data retrieval + prompt injection) once gear data is captured.

**Dependency:** Requires gear preference capture (feature 5).

---

### 13. Preference Pattern Recognition (Premium vs. Value Orientation)

**Value proposition:** Shriram's "bike snob" persona should know whether to recommend the Shimano
105 or the Dura-Ace. If a rider consistently buys mid-range gear, recommending Dura-Ace is tone-
deaf. Tracking value orientation prevents this.

**How it works:** The admin captures a rider's value orientation (budget | mid-range | premium |
"buy once buy right"). The chatbot uses this to calibrate gear recommendations.

**Complexity:** Low — simple enum field on the gear profile; the value is in the data, not the
feature complexity.

**Dependency:** Part of gear preferences (feature 5); requires coaching prompt to use it.

---

### 14. Coach Personality Trait Admin Verification with Source Examples

**Value proposition:** When the system extracts "Venki: sarcastic humor," the admin can see
actual quotes from Venki's messages that justify that classification. This builds trust in the
extraction and enables informed corrections.

**Why this matters:** Automated personality extraction can hallucinate traits or overweight a few
atypical messages. Showing source evidence turns the admin review from a "trust the AI" exercise
into a genuine verification step.

**Complexity:** Medium — requires storing example quotes alongside extracted traits during
extraction; UI to display them alongside trait fields.

**Dependency:** Must be built into the extraction pipeline from the start (can't add evidence
retroactively without re-running extraction).

---

## Anti-Features

Features to deliberately NOT build in this milestone, with reasons.

### Anti-Feature 1: Real-Time Personality Inference During Conversation

**What it is:** Analyze the current conversation to infer the rider's personality on the fly,
rather than using pre-extracted profiles.

**Why to avoid:**
- High latency per message (adds an extra LLM call to every turn)
- Results are noisy (a few messages in one conversation don't reveal stable personality)
- Contradicts the architecture decision to use stored, admin-reviewed profiles
- Premature optimization — pre-extracted profiles cover the use case for 15-40 riders

**What to do instead:** Pre-extracted profiles loaded once per conversation. Update profiles
periodically (manual re-extraction, not real-time).

---

### Anti-Feature 2: Automatic Coach Persona Updates from New Chat Data

**What it is:** As new WhatsApp exports are added, automatically update coach personality profiles
without admin review.

**Why to avoid:**
- Without admin review, a few unusual messages can corrupt a persona
- Venki having a bad day shouldn't make the AI Venki permanently grumpy
- The admin review step is a trust-building checkpoint, not a bottleneck

**What to do instead:** Extraction runs on-demand, results go to a "pending review" state until
admin approves.

---

### Anti-Feature 3: Rider-Facing Personality Profile Transparency

**What it is:** Show riders their own extracted personality profile (e.g., "We think you
communicate with dry humor and prefer direct feedback").

**Why to avoid:**
- Riders didn't consent to being profiled from group chat data; surfacing the profile explicitly
  could feel invasive or embarrassing
- The team is small (15-40 riders) — this creates social awkwardness if profiles feel judgmental
- No product value: riders don't need to know their profile exists to benefit from it

**What to do instead:** Profiles operate silently in the background. Riders experience the
adaptive tone without knowing they have a profile.

---

### Anti-Feature 4: Automated Blog Scraping on Schedule

**Why to avoid:** Already in the OUT OF SCOPE list in PROJECT.md. One-time extraction is
sufficient — blogs update infrequently and the team is small. Scheduled scraping adds infrastructure
complexity (cron jobs, change detection, rate limiting) for marginal value.

**What to do instead:** Admin triggers re-extraction manually when Mihir or Venki posts something
new.

---

### Anti-Feature 5: Full Conversation Export / Personality Report Generation

**What it is:** PDF or formatted report of a rider's extracted personality profile.

**Why to avoid:** Over-engineering for a team of 15-40 riders. The admin UI is the report.

---

### Anti-Feature 6: Personality Trait Suggestions Based on Other Riders

**What it is:** "Rider X is similar to Rider Y — apply Y's profile to X?"

**Why to avoid:** Personality profiling must be individual. Clustering or templating profiles
defeats the purpose and risks mischaracterizing individuals in a small, close-knit team.

---

### Anti-Feature 7: Multi-Coach Simultaneous Responses

**What it is:** "Both Venki and Shriram weigh in on this question."

**Why to avoid:** Adds UI complexity (which voice wins?), increases token cost, and makes
conversations feel like a committee not a personal coach. Single-coach routing per message is
the right model.

---

## Feature Dependencies

```
WhatsApp extraction (1) ─────────────────────────────→ Admin trait editing UI (4)
                                                      → Coach persona generation (11)
                                                      → Rider tone matching (10)

Blog extraction (2) ──────────────────────────────────→ Admin trait editing UI (4)
                                                      → Coach persona generation (11)

Personality profiles in DB (3) ───────────────────────→ Admin trait editing UI (4)
                                                      → Rider tone matching (10)
                                                      → Coach persona generation (11)
                                                      → Gear-aware recommendations (12)

Admin trait editing UI (4) ───────────────────────────→ Coach persona generation (11)
                                                      → Braintrust evals (8)

Gear preferences (5) ─────────────────────────────────→ Gear-aware recommendations (12)
                                                      → Preference pattern recognition (13)

Coach assignment config (6) ──────────────────────────→ Guardrails as structured config (7)
                                                      → Braintrust evals (8)

Guardrails as structured config (7) ──────────────────→ Braintrust evals (8)

Knowledge base expansion (9) ─────────────────────────→ (independent; enhances RAG quality)
```

**Critical path:** Feature 1 → Feature 3 → Feature 4 → Feature 7 → Feature 8

This means extraction must run before any admin UI work can be reviewed or tested end-to-end.
Build and test the extraction pipeline first.

---

## MVP Recommendation

For this milestone, prioritize in this order:

**Phase A — Foundation (must ship first):**
1. Personality extraction pipeline (WhatsApp + blogs) [Features 1, 2]
2. Personality profiles DB schema [Feature 3]
3. Admin trait view + edit UI [Feature 4]

**Phase B — Admin Configuration:**
4. Gear preferences capture [Feature 5]
5. Coach assignment configuration [Feature 6]
6. Guardrails as structured config [Feature 7]

**Phase C — Validation + Knowledge:**
7. Braintrust eval suite for guardrails [Feature 8]
8. Knowledge base expansion from external resources [Feature 9]

**Defer to future milestone (already flagged in PROJECT.md as "future phase"):**
- Per-rider communication style matching in live chatbot [Feature 10]
- Fully dynamic coach personas driven by profiles [Feature 11]

These two deferred features require the profiles to exist (built in this milestone) and then a
separate milestone to wire them into the chatbot's response generation. Building the data without
immediately wiring it into the chat is intentional — it allows admin review of profile quality
before the profiles affect responses.

---

## Complexity Summary

| Feature | Complexity | Why |
|---------|------------|-----|
| 1. WhatsApp personality extraction | High | Noisy input; needs GPT-4o; prompt engineering for trait accuracy |
| 2. Blog personality extraction | Medium | Cleaner input; PDF parsing adds complexity |
| 3. Personality profiles DB schema | Medium | Schema design must be flexible but queryable |
| 4. Admin trait view/edit UI | Medium | UX for non-technical admin; per-field save behavior |
| 5. Gear preferences capture | Low | Straightforward CRUD |
| 6. Coach assignment config | Low-Med | Simple schema; routing logic replacement |
| 7. Guardrails as structured config | Medium | Prompt composition from rule rows; version management |
| 8. Braintrust eval suite | Medium | Dataset creation; scorer design; existing SDK integration |
| 9. Knowledge base expansion | Medium | Web crawl + embed; content quality filtering |
| 10. Rider tone matching (deferred) | Low to wire, High to validate | Wiring is prompt injection; validation needs eval coverage |
| 11. Dynamic coach personas (deferred) | Medium | Depends on profile quality; prompt engineering |
| 12. Gear-aware recommendations | Low | Data retrieval + prompt injection |
| 13. Preference pattern recognition | Low | Enum field; prompt instruction |
| 14. Trait verification with source examples | Medium | Storage during extraction; UI display |

---

## Sources

- Project context: `/Users/msambhus/LocalDocuments/Personal/TeamAshaRandonneuring/.planning/PROJECT.md`
- Codebase analysis: `.planning/codebase/ARCHITECTURE.md`, `STACK.md`, `CONCERNS.md`, `INTEGRATIONS.md`
- Existing roadmap: `.planning/ROADMAP.md` (Phases 1-6 already shipped or planned)
- Domain knowledge: AI coaching platform patterns (training data, confidence MEDIUM — verify against
  live examples if implementing novel patterns)
- Confidence note: Live web search was unavailable; findings are based on project documentation
  and training knowledge about AI coaching systems. Patterns for personality extraction, guardrail
  configuration, and admin interfaces are well-established in the LLM application space and carry
  MEDIUM confidence. The specific trait taxonomy (humor type, directness level, etc.) is derived
  from the project's own personality descriptions of Venki and Shriram and carries HIGH confidence
  for this specific team's needs.
