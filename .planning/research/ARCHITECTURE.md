# Architecture Patterns: Personality-Driven Coaching Integration

**Domain:** Brownfield Flask/PostgreSQL/OpenAI coaching chatbot — milestone 2 additions
**Researched:** 2026-03-17
**Confidence:** HIGH — based on direct codebase inspection of all relevant files

---

## Existing Architecture (Baseline)

The app follows a clean three-layer structure that new features must respect:

```
Routes (blueprints)      — HTTP boundary, auth, form parsing, flash messages
  |
Services (Python)        — Business logic, OpenAI calls, RAG, data assembly
  |
Models (models.py)       — All SQL queries, psycopg2, caching via Flask-Caching
  |
Database (PostgreSQL/Supabase)  — pgvector for embeddings, standard tables for state
```

The critical existing files and their roles:

- `services/openai_coach.py` — Houses `CHAT_SYSTEM_PROMPT` (the static hardcoded persona text) and coaching advice generation. This is where personality data will flow in.
- `services/chat_service.py` — Orchestrates the agent loop: moderation → intent classification → RAG retrieval → tool execution → streaming SSE. Calls `_get_system_prompt()` which returns `CHAT_SYSTEM_PROMPT`.
- `routes/admin.py` — The existing admin blueprint with `_require_admin()` guard. New admin pages extend this blueprint.
- `models.py` — 2400+ lines of SQL functions, all using `_execute()` helper. New data access functions go here.
- `evals/eval_guardrail.py` — Existing Braintrust eval pattern (dataset seed + Eval() call + scorer functions).
- `scripts/import_whatsapp.py` — Template for the resource crawling pipeline (parse → filter → embed → insert pattern already established).

---

## Component Map: New Features and Where They Live

### Component 1: Personality Extraction Pipeline

**Lives in:** `scripts/` directory (not `services/`)

**Rationale:** This is a one-time or manually-triggered offline operation. It reads external files (WhatsApp exports, blog PDFs), calls OpenAI GPT-4o (not mini — need quality for trait extraction), and writes structured data to the database. It does not belong in the request-response path. The existing `scripts/import_whatsapp.py` establishes the exact pattern: CLI script, argparse, direct DB connection, OpenAI client, prints progress summary.

**New scripts:**
- `scripts/extract_personality_whatsapp.py` — Read per-sender message history from `whatsapp_chunk` table (already imported), group by sender, call GPT-4o with structured output, write `personality_profile` records.
- `scripts/extract_personality_blog.py` — Fetch blog URLs or read PDFs, call GPT-4o, write personality profiles for Mihir and Venki.

**What the scripts produce:**
A structured personality profile record per person, stored in the database. The structure should be JSON/JSONB to allow evolution without schema changes:

```python
# Output structure per person (stored as JSONB columns)
{
  "communication_style": "direct|verbose|terse|storytelling",
  "humor_type": "sarcastic|tongue_in_cheek|dry|earnest|none",
  "tone_patterns": ["uses rhetorical questions", "often references past rides"],
  "expertise_signals": ["gear_obsession", "technique_focused"],
  "sample_phrases": ["classic Venki line 1", "classic Venki line 2"],
  "coaching_approach": "encourager|challenger|technical|motivational",
  "extracted_from": ["whatsapp_fresh_start", "blog_venki"],
  "extraction_model": "gpt-4o",
  "extraction_date": "2026-03-17"
}
```

---

### Component 2: Personality Profile Database Schema

**Lives in:** `schema/personality_schema.sql` + `migrations/apply_migration_007.py`

**Tables needed:**

```sql
-- personality_profile: one row per team member (rider or coach)
-- rider_id is nullable to allow non-rider coaches
CREATE TABLE personality_profile (
    id SERIAL PRIMARY KEY,
    rider_id INTEGER REFERENCES rider(id) ON DELETE SET NULL,
    name TEXT NOT NULL,                    -- display name (may not have rider record)
    role TEXT NOT NULL DEFAULT 'rider',    -- 'rider' | 'coach'
    communication_style TEXT,
    humor_type TEXT,
    tone_patterns JSONB DEFAULT '[]',      -- array of strings
    expertise_signals JSONB DEFAULT '[]',
    sample_phrases JSONB DEFAULT '[]',
    coaching_approach TEXT,
    raw_traits JSONB DEFAULT '{}',         -- full LLM extraction output, for reference
    is_active BOOLEAN DEFAULT TRUE,
    extracted_from JSONB DEFAULT '[]',     -- source list
    extraction_model TEXT,
    notes TEXT,                            -- admin free-text override notes
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- gear_preference: one row per rider per gear category
CREATE TABLE gear_preference (
    id SERIAL PRIMARY KEY,
    rider_id INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    category TEXT NOT NULL,               -- 'bike' | 'wheels' | 'lights' | 'bags' | 'kit' | 'accessories'
    item_name TEXT NOT NULL,
    brand TEXT,
    notes TEXT,
    price_tier TEXT,                      -- 'budget' | 'mid' | 'premium' | 'ultra'
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- coach_assignment: maps coaches to topic domains
CREATE TABLE coach_assignment (
    id SERIAL PRIMARY KEY,
    personality_profile_id INTEGER NOT NULL REFERENCES personality_profile(id) ON DELETE CASCADE,
    topic_domain TEXT NOT NULL,           -- 'bikes' | 'training' | 'nutrition' | 'general' | 'routes'
    is_default BOOLEAN DEFAULT FALSE,     -- true = fallback when no specific domain matches
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- coaching_guardrail: topic allow/deny rules per coach
CREATE TABLE coaching_guardrail (
    id SERIAL PRIMARY KEY,
    personality_profile_id INTEGER NOT NULL REFERENCES personality_profile(id) ON DELETE CASCADE,
    rule_type TEXT NOT NULL,              -- 'allow' | 'deny'
    topic TEXT NOT NULL,                  -- free text description
    keywords JSONB DEFAULT '[]',          -- array of keywords that trigger this rule
    response_template TEXT,              -- optional canned response for deny rules
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Key decision — JSONB for trait fields:** Trait data is exploratory. Using JSONB columns (`tone_patterns`, `sample_phrases`, etc.) avoids premature normalization. Admin can edit via the UI, LLM can add new fields. Only promote to columns if querying/filtering on a trait becomes needed.

**Key decision — `coaching_guardrail` as a table, not config file:** Guardrails are admin-editable at runtime without redeploy. The table rows get loaded into the system prompt at request time.

---

### Component 3: Data Access Layer Extensions

**Lives in:** `models.py` — append new functions following existing pattern

**New functions to add:**

```python
# Personality profiles
def get_personality_profile(rider_id)     # fetch by rider
def get_all_personality_profiles()        # for admin list view
def upsert_personality_profile(...)       # create or update
def get_coach_profiles()                  # profiles where role='coach'

# Gear preferences
def get_gear_preferences(rider_id)        # all gear for one rider
def upsert_gear_preference(...)           # save from admin form
def delete_gear_preference(pref_id)

# Coach assignments
def get_coach_assignments()               # all assignments for admin display
def set_coach_assignment(...)             # replace assignment for a domain

# Coaching guardrails
def get_guardrails_for_coach(profile_id)  # load for prompt injection
def get_all_guardrails()                  # for admin list
def upsert_guardrail(...)                 # save from admin form
def delete_guardrail(guardrail_id)
```

Do NOT cache personality or guardrail queries — these are admin-editable and must reflect changes immediately. The existing `@cache.memoize` decorator is appropriate only for season/ride data that changes rarely.

---

### Component 4: Personality Data Flowing into the System Prompt

**Lives in:** `services/chat_service.py` — new function, wired into `process_message()`

**Current flow:**
```
process_message()
  → _get_system_prompt()          # returns static CHAT_SYSTEM_PROMPT string
  → assemble_rider_context()      # appends <rider_data> block
  → assemble_team_context()       # appends <team_context> block
  → build_messages()              # packages for OpenAI
```

**New flow (extend, don't replace):**
```
process_message()
  → _get_system_prompt()          # still returns base CHAT_SYSTEM_PROMPT string
  → assemble_rider_context()      # unchanged
  → assemble_team_context()       # unchanged
  → assemble_coach_context()      # NEW: injects coach persona + guardrails
  → build_messages()
```

**New function `assemble_coach_context(user_message)`:**

```python
def assemble_coach_context(user_message):
    """Build dynamic coach persona block from personality_profile and coaching_guardrail tables.

    Returns XML-delimited coach context string, or empty string if no coach data.
    Called after intent classification so we know which domain we're in.
    """
    # 1. Determine which coach domain applies (replaces _BIKE_KEYWORDS hardcode)
    # 2. Load coach personality_profile from DB
    # 3. Load that coach's active coaching_guardrails
    # 4. Format as XML block for system prompt injection

    return "<coach_context>\n...\n</coach_context>"
```

**XML block format (consistent with existing `<rider_data>` and `<team_context>` convention):**

```
<coach_context>
COACH: Shriram K
ROLE: Bike equipment specialist
COMMUNICATION STYLE: Direct, enthusiastic about gear
HUMOR: Dry bike snobbery, names riders by their bikes
COACHING APPROACH: Technical, often nudges toward better gear

GUARDRAILS:
- ALLOWED: Bike components, maintenance, gear recommendations, bike fit
- ALLOWED: Equipment pricing and comparisons
- DENIED: Medical advice — respond with: "See a doctor for that."
- DENIED: Non-cycling topics — redirect to cycling

SAMPLE PHRASES: ["You are riding what for a 600k?", "A proper bike deserves proper tires."]
</coach_context>
```

**Why not replace `CHAT_SYSTEM_PROMPT` entirely?** The base prompt contains structural rules (randonneuring time limits, nutrition framework, brevet history interpretation logic) that apply regardless of coach. Coach context is personality coloring on top of that foundation. Keep them separate.

**Coach selection logic** (replaces `_BIKE_KEYWORDS` inline code in `run_agent_loop()`):

The current hardcoded keyword check (`_BIKE_KEYWORDS`) must be replaced with a database-driven lookup. New function `select_coach_for_message(intent_result, user_message)` queries `coach_assignment` table to find the matching domain, returns the `personality_profile_id`. This function lives in `services/chat_service.py`.

---

### Component 5: Admin Pages for Personality Management

**Lives in:** `routes/admin.py` (new route groups) + `templates/admin/` (new templates)

**Extension pattern** — follow exactly the existing admin blueprint convention:

```python
# All new routes in routes/admin.py, inside admin_bp Blueprint

@admin_bp.route('/personalities')
@user_login_required
def personalities():
    _require_admin()
    profiles = models.get_all_personality_profiles()
    return render_template('admin/personalities.html', profiles=profiles)

@admin_bp.route('/personalities/<int:profile_id>/edit', methods=['GET', 'POST'])
@user_login_required
def edit_personality(profile_id):
    _require_admin()
    # GET: render form with existing data
    # POST: validate, upsert, flash success, redirect
```

**New admin route groups to add (all under `/admin/` prefix, all using `_require_admin()`):**

| URL | Purpose | Template |
|-----|---------|----------|
| `/admin/personalities` | List all personality profiles | `admin/personalities.html` |
| `/admin/personalities/<id>/edit` | Edit traits, tone, sample phrases | `admin/personality_edit.html` |
| `/admin/personalities/new` | Create profile for new person | `admin/personality_edit.html` (reuse) |
| `/admin/riders/<id>/gear` | View/edit gear preferences for one rider | `admin/gear_preferences.html` |
| `/admin/coaches` | Coach assignments by topic domain | `admin/coaches.html` |
| `/admin/coaches/<id>/guardrails` | Guardrail rules for one coach | `admin/coach_guardrails.html` |
| `/admin/knowledge` | Trigger resource crawl, view embedding status | `admin/knowledge.html` |

**Template architecture** — all new admin templates should `{% extends 'admin/base.html' %}`. Check if a base admin template exists; if not, create one by extracting the common header/nav from `admin/dashboard.html`.

**Form patterns:** Use Jinja2 `{% for %}` to render JSONB arrays (tone_patterns, sample_phrases) as textarea inputs with newline-separated values. On POST, `request.form.get('tone_patterns').splitlines()` converts back to list before saving as JSONB.

---

### Component 6: Resource Crawling and Embedding Pipeline

**Lives in:** `scripts/embed_resources.py` (new script, follows `import_whatsapp.py` pattern)

**Pipeline:**

```
Input: List of URLs (from spreadsheet or hardcoded config)
  ↓
Step 1: Fetch each URL (requests + BeautifulSoup for HTML, pypdf for PDF)
  ↓
Step 2: Extract main content (strip nav, ads, boilerplate)
  ↓
Step 3: Chunk by paragraph/section (500-1000 tokens per chunk)
  ↓
Step 4: Filter for cycling relevance (rule-based keyword check, same as WhatsApp pipeline)
  ↓
Step 5: Embed with text-embedding-3-small (same model, same batch_size=100 pattern)
  ↓
Step 6: Insert into existing whatsapp_chunk table using a new source name
         source = 'web_resource' or URL-derived slug
         ON CONFLICT DO NOTHING for idempotent re-runs
```

**Why reuse `whatsapp_chunk`?** The table already has the right schema: source, content, embedding vector(1536), senders (unused/null for web), timestamps (use crawl date). The RAG retrieval in `chat_service.py` queries `whatsapp_chunk` without filtering by source — web resources will automatically be retrieved alongside WhatsApp content. The HNSW index covers all rows regardless of source.

**Source naming convention:** Use a URL slug as the source, e.g., `web_unexpectedathlete` for Mihir's blog. This allows partial re-import by source and allows the admin page to show per-source chunk counts.

**Vercel constraint:** This pipeline runs locally (developer machine) or in a CI script — not as a Vercel function. Vercel's 60-second function timeout and stateless environment make it unsuitable for crawling. The script outputs a summary and commits to the DB directly. Same approach as `scripts/import_whatsapp.py`.

**Admin page role:** The `/admin/knowledge` page shows existing sources and chunk counts (SELECT source, COUNT(*) FROM whatsapp_chunk GROUP BY source), allows triggering a crawl is out of scope for Vercel — instead, it shows instructions to run the script locally and displays current embedding status.

---

### Component 7: Braintrust Evals for Guardrail Validation

**Lives in:** `evals/eval_guardrail_dynamic.py` (new eval file, extends existing pattern)

**Current guardrail eval** (`eval_guardrail.py`) tests off-topic blocking with hardcoded patterns. The new eval validates that admin-configured guardrails are respected.

**New eval approach:**

```python
# evals/eval_guardrail_dynamic.py

# 1. Load active guardrails from DB at eval run time
guardrails = models.get_all_guardrails()

# 2. Generate test cases from guardrails
#    For each DENY rule: create a test message that should be blocked
#    For each ALLOW rule: create a test message that should be answered

# 3. Use GPT-4o as LLM judge scorer (not keyword matching)
#    Scorer prompt: "Given this guardrail rule and this response,
#    did the chatbot correctly respect the guardrail? Score 1 (respected) or 0 (violated)."
```

**Why LLM-as-judge for guardrails?** Keyword matching can't verify tone compliance ("coach won't give medical advice" requires reading the response, not checking for a keyword). GPT-4o-mini as judge is fast and cheap for this.

**Integration with Braintrust** — follow the exact pattern in `eval_guardrail.py`:

```python
Eval(
    "Team Asha",
    experiment_name="guardrail_dynamic",
    data=lambda: generated_test_cases,
    task=guardrail_task,          # calls the real chat pipeline
    scores=[guardrail_llm_scorer], # LLM judge
)
```

**Dataset seeding:** Each guardrail rule becomes a dataset record in Braintrust `guardrail_rules` dataset. This lets you track guardrail violation rates over time as you add new rules.

---

## Data Flow Diagrams

### Flow 1: Chat Request with Dynamic Persona

```
User message (SSE)
  |
routes/chat.py → process_message()
  |
  ├─ moderate_input()               [OpenAI Moderation API]
  ├─ assemble_rider_context()       [models: Strava + brevet data]
  ├─ assemble_team_context()        [models: upcoming rides]
  ├─ assemble_coach_context()       [NEW: models: personality_profile + coaching_guardrail]
  |     └── select_coach_for_message()  [NEW: models: coach_assignment]
  |
  └─ run_agent_loop()
        ├─ classify_intent()        [OpenAI structured output]
        ├─ retrieve_knowledge_context()  [pgvector RAG → whatsapp_chunk]
        ├─ execute tool if needed   [models: DB queries or web search]
        └─ _stream_completion()     [OpenAI GPT-4o-mini streaming]
```

### Flow 2: Personality Extraction (Offline)

```
WhatsApp export files (on disk)
  |
scripts/import_whatsapp.py → whatsapp_chunk table (already done)
  |
scripts/extract_personality_whatsapp.py
  ├─ SELECT messages grouped by sender from whatsapp_chunk
  ├─ GPT-4o structured output → personality trait JSON per sender
  └─ UPSERT into personality_profile table

Blog URLs / PDFs
  |
scripts/extract_personality_blog.py
  ├─ Fetch/parse content
  ├─ GPT-4o structured output → personality trait JSON
  └─ UPSERT into personality_profile table
```

### Flow 3: Resource Crawling (Offline)

```
URL list (hardcoded in script or from config)
  |
scripts/embed_resources.py
  ├─ requests.get(url) + BeautifulSoup → raw text
  ├─ Chunk by paragraph
  ├─ Rule-based cycling filter
  ├─ text-embedding-3-small → embeddings
  └─ INSERT INTO whatsapp_chunk (source='web_*') ON CONFLICT DO NOTHING
```

### Flow 4: Admin Guardrail Edit → Eval Validation

```
Admin edits guardrail in browser
  |
routes/admin.py → POST /admin/coaches/<id>/guardrails
  |
models.upsert_guardrail()
  |
PostgreSQL: coaching_guardrail table updated
  |
  [later, manually triggered]
  |
evals/eval_guardrail_dynamic.py
  ├─ Load guardrails from DB
  ├─ Generate test cases
  └─ Run Braintrust Eval → scores visible in Braintrust dashboard
```

---

## Component Boundaries

| Component | Owned By | Input | Output | Does NOT touch |
|-----------|----------|-------|--------|----------------|
| `scripts/extract_personality_*.py` | Offline / dev | WhatsApp chunks, blog text | `personality_profile` rows | Flask app, HTTP |
| `scripts/embed_resources.py` | Offline / dev | URLs | `whatsapp_chunk` rows | HTTP, admin UI |
| `services/chat_service.py` | Request path | User message, session | SSE stream | File system, offline scripts |
| `services/openai_coach.py` | Request path | Rider context | System prompt string | DB directly |
| `routes/admin.py` | HTTP / admin | Form POST | Flash + redirect | OpenAI, Braintrust |
| `models.py` | Data layer | SQL params | dicts / lists | OpenAI, HTTP |
| `evals/eval_guardrail_dynamic.py` | Offline / CI | DB guardrails | Braintrust scores | Production request path |

---

## Suggested Build Order (Dependencies)

### Phase 1: Data Foundation (no visible feature yet, but everything depends on it)
1. Write `schema/personality_schema.sql` — creates `personality_profile`, `gear_preference`, `coach_assignment`, `coaching_guardrail` tables
2. Write `migrations/apply_migration_007.py` — applies schema to Supabase
3. Add model functions to `models.py` for all four tables
4. Seed two initial personality profiles for Shriram and Venki manually (SQL), based on what's currently hardcoded in `CHAT_SYSTEM_PROMPT` — this makes the system functional before extraction scripts are done

Depends on: Nothing. Enables: Everything else.

### Phase 2: Extraction Scripts (offline, no UI needed)
1. `scripts/extract_personality_whatsapp.py`
2. `scripts/extract_personality_blog.py`

Depends on: Phase 1 schema. Enables: Real personality data in DB.

### Phase 3: Chat Integration (replaces hardcoded personas)
1. New `assemble_coach_context()` function in `services/chat_service.py`
2. New `select_coach_for_message()` function replacing `_BIKE_KEYWORDS` inline code
3. Wire both into `process_message()`

Depends on: Phase 1 model functions. Enables: Dynamic persona in chatbot.

### Phase 4: Admin UI (makes personality data editable)
1. Admin routes in `routes/admin.py` for personalities, gear, coach assignments, guardrails
2. Jinja2 templates for each admin page

Depends on: Phase 1 model functions, Phase 3 (so admins can see effect of edits). Enables: Guardrail eval.

### Phase 5: Braintrust Eval for Guardrails
1. `evals/eval_guardrail_dynamic.py` with LLM-as-judge scorer

Depends on: Phase 1 guardrail table, Phase 4 admin UI (need guardrails to evaluate).

### Phase 6: Resource Embedding (knowledge expansion)
1. `scripts/embed_resources.py`

Depends on: Nothing new (reuses `whatsapp_chunk` table). Can run in parallel with any phase.

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Coaching Guardrails in the System Prompt String
**What:** Hardcoding guardrail rules directly into `CHAT_SYSTEM_PROMPT` string.
**Why bad:** Already done once (the `SCOPE` block and `DENIED` rules in `CHAT_SYSTEM_PROMPT`). It requires code deploys to change rules, can't be admin-edited, and can't be evaluated systematically.
**Instead:** Load guardrails from `coaching_guardrail` table at request time via `assemble_coach_context()`. Keep the base `CHAT_SYSTEM_PROMPT` for structural/domain knowledge only.

### Anti-Pattern 2: New Blueprint for Admin Pages
**What:** Creating `routes/admin_personality.py` as a separate blueprint.
**Why bad:** Adds complexity without benefit. Admin auth (`_require_admin()`), URL prefix (`/admin/`), and existing template inheritance are already established. Splitting into two blueprints creates duplication and makes URL routing harder to follow.
**Instead:** Add all new admin routes directly to `admin_bp` in `routes/admin.py`. If the file gets large, use logical comment sections (`# --- PERSONALITY MANAGEMENT ---`).

### Anti-Pattern 3: Embedding Resources into a New Table
**What:** Creating a `web_resource_chunk` table separate from `whatsapp_chunk`.
**Why bad:** The RAG retrieval function (`retrieve_knowledge_context()`) queries `whatsapp_chunk` with a single cosine similarity search. Splitting sources requires a UNION query or two calls, complicates the retrieval, and provides no benefit since both are 1536-dimension vectors.
**Instead:** Reuse `whatsapp_chunk` with a distinctive `source` prefix like `web_*`. The existing HNSW index covers all rows.

### Anti-Pattern 4: Personality Extraction as a Flask Route
**What:** Adding a `/admin/extract-personalities` POST route that runs GPT-4o extraction inline.
**Why bad:** Vercel serverless has a 60-second function timeout. GPT-4o extraction over a large WhatsApp history (thousands of messages per sender) will time out. Additionally, this is a one-time operation that shouldn't be in the request path.
**Instead:** CLI script in `scripts/` that developers run locally. Admin page shows extraction status (last extraction date from `personality_profile.extracted_from`), not a trigger button.

### Anti-Pattern 5: Caching Personality Data
**What:** Adding `@cache.memoize()` to personality/guardrail model functions.
**Why bad:** Admin edits need to take effect immediately on the next chat request. Cache TTL would mask updates, causing confusion (admin changes guardrail, chatbot still ignores it for N minutes).
**Instead:** No caching on `personality_profile`, `coaching_guardrail`, `coach_assignment` queries. These tables are small (< 100 rows total) — uncached queries will be sub-millisecond.

### Anti-Pattern 6: LLM Extraction Inside models.py
**What:** Calling OpenAI from inside a model function to compute personality traits.
**Why bad:** models.py is the data access layer — it runs SQL and returns dicts. Mixing OpenAI calls violates the service/model boundary that the rest of the codebase follows (all OpenAI calls are in `services/`).
**Instead:** Extraction scripts call OpenAI (or a new `services/personality_extractor.py`), then call `models.upsert_personality_profile()` to save results.

---

## Scalability Notes

This app serves 15-40 riders. No scalability concerns for this milestone.

The most token-heavy change is injecting coach persona into every chat request. A typical personality profile + guardrail block is ~300 tokens. With GPT-4o-mini at $0.15/1M input tokens, this adds ~$0.000045 per message — negligible.

The one-time cost concern is resource embedding. Crawling the resources spreadsheet URLs (assume 20-50 URLs, 5000 tokens/URL average) = ~100K-250K tokens for embedding. At text-embedding-3-small pricing ($0.02/1M tokens) that's $0.002-0.005 total. Monitor the token count in the script output before doing a full run.

---

## Integration Points with Existing Code

| New Component | Existing Code It Touches | Touch Type |
|---------------|--------------------------|------------|
| `assemble_coach_context()` | `services/chat_service.py:process_message()` | Add one function call after existing context assembly |
| `select_coach_for_message()` | `services/chat_service.py:run_agent_loop()` | Replace `_BIKE_KEYWORDS` inline block |
| Admin personality routes | `routes/admin.py` — append to file | Non-breaking addition |
| `models.py` additions | `models.py` — append functions | Non-breaking addition |
| `eval_guardrail_dynamic.py` | `evals/__init__.py` — may need import | Additive only |
| `embed_resources.py` | `schema/whatsapp_schema.sql` (reuse table) | No schema change |
| New tables | `schema/` + `migrations/` | Additive schema migration |

No existing routes, templates, or services need to be modified (only extended). The existing chatbot continues to work before Phase 3 is done — the hardcoded `CHAT_SYSTEM_PROMPT` stays in place until `assemble_coach_context()` replaces it.

---

## Sources

All findings are HIGH confidence — derived from direct code inspection:

- `services/chat_service.py` — SSE pipeline, agent loop, system prompt assembly pattern
- `services/openai_coach.py` — Existing hardcoded `CHAT_SYSTEM_PROMPT` and `CHAT_SYSTEM_PROMPT_CHAT`
- `routes/admin.py` — Blueprint pattern, `_require_admin()`, existing route structure
- `models.py` — `_execute()` helper, caching patterns, data access layer conventions
- `scripts/import_whatsapp.py` — Offline pipeline pattern (parse → filter → embed → insert)
- `evals/eval_guardrail.py` — Braintrust eval structure, scorer function pattern
- `evals/eval_e2e.py` — Dataset seeding, task function, multi-scorer pattern
- `schema/whatsapp_schema.sql` — `whatsapp_chunk` table structure, HNSW index configuration
- `schema/schema.sql` — Existing table conventions, foreign key patterns
- `CLAUDE.md` — Tech stack constraints, deployment target (Vercel serverless)
- `.planning/PROJECT.md` — Feature requirements and out-of-scope boundaries
