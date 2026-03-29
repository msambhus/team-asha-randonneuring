# Phase 7: Data Foundation - Research

**Researched:** 2026-03-17
**Domain:** PostgreSQL schema design (psycopg2/Supabase), Python data access layer (models.py), seed data migration
**Confidence:** HIGH

---

## Summary

Phase 7 creates four new PostgreSQL tables (`personality_profile`, `gear_preference`, `coach_assignment`, `coaching_guardrail`) and seeds them with manually-entered data for Shriram and Venki. This is a pure database/data-access phase — no Flask routes, no UI, no LLM calls. The work is entirely: schema SQL, Python CRUD functions in models.py, and a seed script.

The project has a clear, consistent migration pattern: numbered SQL files under `migrations/` applied via standalone Python scripts that read `DATABASE_URL` from `.env` or the environment. Model functions use `_execute(sql, params)` returning `psycopg2.extras.RealDictCursor` results. The existing `test_system_prompt.py` tests currently import `CHAT_SYSTEM_PROMPT` directly from `services/openai_coach.py` — those tests must still pass after seeding, since seeding does not remove the constant; that happens only in a later phase.

The most important design constraint from prior research (logged in STATE.md): personality traits must be stored as **structured typed fields with character limits**, not free-text blobs. This is a prompt injection defense (OWASP LLM01). Column-level `CHECK` constraints or `VARCHAR(N)` are the enforcement mechanism in PostgreSQL.

**Primary recommendation:** Write one migration SQL file (`011_personality_coaching_tables.sql`) + one apply script + one seed script. All four tables, all indexes, FK constraints, soft-delete columns, and `rule_version` in a single atomic migration. Seed separately so it is repeatable and idempotent.

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| PROF-01 | Database stores personality profiles with structured, queryable fields (not free-form text blobs) | VARCHAR(N) column-level limits + enumeration constraints enforce this at the DB layer |
| PROF-02 | Coach profiles include tone, humor type, directness, signature phrases, topic biases, and topics allowed | All six attributes map to typed columns; arrays use `TEXT[]`, enums use `VARCHAR` with CHECK |
| PROF-03 | Rider profiles include preferred formality, humor sensitivity, encouragement preference, and technical depth | Same table with `profile_type = 'rider'` discriminator OR separate table; discriminator approach reduces joins |
| PROF-04 | Each profile tracks extraction source (whatsapp/blog/manual), extraction date, source message count, and confidence | Four metadata columns on `personality_profile`; confidence is a `VARCHAR CHECK IN ('high','medium','low')` |
| PROF-05 | Profile changes are auditable (last_modified_by, timestamp) | `updated_at TIMESTAMPTZ`, `updated_by TEXT` columns; no separate audit log needed for Phase 7 |
| GUARD-01 | Guardrails stored as structured database rows (rule_type, rule_value, is_active), not hardcoded in prompts | `coaching_guardrail` table with typed columns; Phase 7 creates the table, Phase 9 wires it into prompts |
| GUARD-06 | Guardrails are version-stamped so Braintrust evals correlate to specific rule sets | `rule_version INTEGER DEFAULT 1` column with a trigger or application-level increment on UPDATE |
</phase_requirements>

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| psycopg2 | existing | All PostgreSQL queries | Already the project's only DB driver |
| PostgreSQL (Supabase) | existing | Storage | Existing production DB |

No new libraries needed for Phase 7. Everything is plain SQL + psycopg2.

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| psycopg2.extras.RealDictCursor | existing | Dict-style row access | Used in every model function via `_execute()` |

**Installation:** None required — no new dependencies.

---

## Architecture Patterns

### Recommended Project Structure

```
migrations/
├── 011_personality_coaching_tables.sql   # New: all 4 tables + indexes + FKs
├── apply_migration_011.py                # New: standalone apply script
scripts/
├── seed_coaching_profiles.py             # New: idempotent seed for Shriram + Venki
models.py                                 # Extend: CRUD functions for all 4 tables
tests/
├── test_coaching_models.py               # New: unit tests for new CRUD functions
```

### Pattern 1: Numbered Migration File

**What:** A single SQL file per schema change, named `NNN_description.sql`, using `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` so it is idempotent.

**When to use:** Every time new tables are added. This project uses this pattern exclusively — see `009_add_strava_ride_analysis.sql` for the reference implementation.

**Example (from existing migrations/009_add_strava_ride_analysis.sql):**
```sql
-- Migration 011: Personality profiles and coaching config tables
CREATE TABLE IF NOT EXISTS personality_profile (
    id SERIAL PRIMARY KEY,
    rider_id INTEGER REFERENCES rider(id) ON DELETE CASCADE,
    profile_type VARCHAR(10) NOT NULL CHECK (profile_type IN ('coach', 'rider')),
    -- structured typed fields (VARCHAR limits enforce PROF-01, PROF-02, PROF-03)
    tone VARCHAR(20) CHECK (tone IN ('direct','warm','playful','serious','sarcastic')),
    humor_type VARCHAR(20) CHECK (humor_type IN ('none','dry','sarcastic','gentle','self-deprecating')),
    directness VARCHAR(10) CHECK (directness IN ('low','medium','high')),
    preferred_formality VARCHAR(10) CHECK (preferred_formality IN ('casual','mixed','formal')),
    humor_sensitivity VARCHAR(10) CHECK (humor_sensitivity IN ('low','medium','high')),
    encouragement_style VARCHAR(20) CHECK (encouragement_style IN ('data-driven','emotional','balanced','tough-love')),
    technical_depth VARCHAR(10) CHECK (technical_depth IN ('beginner','intermediate','expert')),
    signature_phrases TEXT[],           -- array of short phrases, checked at app layer
    topic_biases TEXT[],                -- array of topic strings
    topics_allowed TEXT[],              -- array of topic strings
    -- metadata (PROF-04)
    extraction_source VARCHAR(10) CHECK (extraction_source IN ('whatsapp','blog','manual')),
    extraction_date DATE,
    source_message_count INTEGER,
    extraction_confidence VARCHAR(10) CHECK (extraction_confidence IN ('high','medium','low')),
    -- audit (PROF-05)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by TEXT,
    deleted_at TIMESTAMPTZ             -- soft-delete
);
```

### Pattern 2: Standalone Apply Script

**What:** A Python script that reads `DATABASE_URL` from `.env` or environment, connects via psycopg2, and runs the SQL file. No Flask context needed.

**When to use:** Every migration. Reference: `migrations/apply_migration_standalone.py`.

The apply script for 011 should follow this exact pattern — load `.env`, connect, execute SQL with `conn.autocommit = True`, print success/error per statement, handle `psycopg2.Error` with `already exists` check.

### Pattern 3: Idempotent Seed Script

**What:** A standalone Python script under `scripts/` that inserts seed rows using `INSERT ... ON CONFLICT (rider_id, profile_type) DO UPDATE SET ...`. This means it can be run multiple times safely and acts as the canonical source of truth for initial data.

**When to use:** Any time initial data must be loaded before automation (extraction pipeline) exists. This is the correct pattern for Phase 7 because extraction does not exist yet.

**Seed data source:** The content currently in `CHAT_SYSTEM_PROMPT` (lines 159-248 of `services/openai_coach.py`) is the authoritative description of Shriram and Venki's personalities. The seed script translates that prose into the structured column values.

Shriram (from CHAT_SYSTEM_PROMPT context):
- `profile_type = 'coach'`
- `tone = 'direct'`
- `humor_type = 'dry'`
- `directness = 'high'`
- `topics_allowed = ['bike', 'gear', 'maintenance', 'components', 'fit']`
- `topic_biases = ['bikes', 'accessories', 'gear upgrades']`
- `extraction_source = 'manual'`
- `extraction_confidence = 'high'`

Venki (from CHAT_SYSTEM_PROMPT context):
- `profile_type = 'coach'`
- `tone = 'playful'`
- `humor_type = 'sarcastic'`
- `directness = 'medium'`
- `topics_allowed` = broad (training, nutrition, randonneuring rules, strategy)
- `extraction_source = 'manual'`
- `extraction_confidence = 'high'`

### Pattern 4: Model Functions in models.py

**What:** Plain functions using `_execute(sql, params)`. No ORM. Follows the exact pattern of every other function in models.py.

**Key rules from existing code:**
- Use `RealDictCursor` via `_execute()` (already handles this)
- Use `%s` placeholders — never f-strings for SQL (parameterized query defense)
- `@cache.memoize(CACHE_TIMEOUT)` for read-heavy lookups; NOT cached for write/admin functions
- Do not cache profile data — admin edits must be immediately visible
- Commit with `conn.commit()` from the caller (models.py uses `conn.autocommit = False`)

**Example CRUD pattern (from existing models.py style):**
```python
# Source: models.py _execute() pattern
def get_personality_profile(rider_id, profile_type):
    return _execute(
        "SELECT * FROM personality_profile WHERE rider_id = %s AND profile_type = %s AND deleted_at IS NULL",
        (rider_id, profile_type)
    ).fetchone()

def upsert_personality_profile(rider_id, profile_type, fields, updated_by):
    """Insert or update a personality profile. fields is a dict of column->value."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Build parameterized upsert
    columns = list(fields.keys()) + ['rider_id', 'profile_type', 'updated_by', 'updated_at']
    values = list(fields.values()) + [rider_id, profile_type, updated_by, 'NOW()']
    # Use explicit INSERT ... ON CONFLICT pattern
    ...
    conn.commit()
```

### Pattern 5: coaching_guardrail with rule_version

**What:** `rule_version` is an `INTEGER DEFAULT 1` column. It increments when the row is edited. In PostgreSQL this is best done with a trigger on `coaching_guardrail` that sets `NEW.rule_version = OLD.rule_version + 1` on UPDATE.

**Why trigger, not application code:** Ensures version always increments even if a future admin script bypasses the model layer. This satisfies GUARD-06 ("a specific version is queryable without looking at audit history").

```sql
CREATE OR REPLACE FUNCTION increment_guardrail_version()
RETURNS TRIGGER AS $$
BEGIN
    NEW.rule_version = OLD.rule_version + 1;
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_guardrail_version
    BEFORE UPDATE ON coaching_guardrail
    FOR EACH ROW
    EXECUTE FUNCTION increment_guardrail_version();
```

### Anti-Patterns to Avoid

- **Free-text personality blobs:** Never store personality as a single TEXT column. Phase 7 must use typed columns with CHECK constraints or VARCHAR(N) — this is the OWASP LLM01 defense.
- **Caching admin data:** Do not apply `@cache.memoize` to personality_profile or coaching_guardrail reads. Admin edits must be immediately visible.
- **Application-layer version increment for guardrails:** Don't rely on Python code to increment rule_version — use a DB trigger so it's guaranteed.
- **Separate audit tables:** Out of scope for Phase 7. `updated_at` + `updated_by` + `deleted_at` (soft delete) is the spec. A full audit log is a later enhancement.
- **Flask context in migration scripts:** All apply scripts must be standalone (import psycopg2 directly, read DATABASE_URL from env) — same pattern as `apply_migration_standalone.py`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Soft delete | Custom archive table | `deleted_at TIMESTAMPTZ NULL` column | Standard PostgreSQL pattern; all queries add `WHERE deleted_at IS NULL` |
| Audit trail | Separate audit_log table | `updated_at` + `updated_by` columns | Sufficient for Phase 7; full audit log is a v3 feature |
| Enum enforcement | Application-level if/else | PostgreSQL `CHECK (col IN (...))` constraint | DB enforces it regardless of which code path writes the row |
| Upsert | DELETE+INSERT | `INSERT ... ON CONFLICT DO UPDATE` | Atomic, safe under concurrent writes |
| Version stamping | App code counter | PostgreSQL trigger | Guaranteed even for direct SQL edits; precedent set by `custom_ride_plan` trigger in schema.sql |

**Key insight:** PostgreSQL's constraint system (CHECK, FK, UNIQUE) is the right layer for data integrity. Application code validates for UX; DB constraints enforce correctness.

---

## Common Pitfalls

### Pitfall 1: TEXT[] Arrays in psycopg2

**What goes wrong:** Passing a Python list to a `TEXT[]` column works with psycopg2, but reading it back returns a Python list only when using `psycopg2.extras.RealDictCursor`. If code elsewhere uses a plain cursor, the array comes back as a string `{item1,item2}`.

**Why it happens:** psycopg2 has built-in array adaptation, but only when the cursor type is set correctly.

**How to avoid:** Always use `_execute()` (which uses `RealDictCursor`) for all queries on these tables. Document that `signature_phrases`, `topic_biases`, and `topics_allowed` are Python lists when fetched.

**Warning signs:** Code that does `row['topics_allowed'].split(',')` — this means the cursor was wrong.

### Pitfall 2: Supabase Connection During Migration

**What goes wrong:** Running `psycopg2.connect(db_url)` works locally but times out in CI or when DATABASE_URL is the Supabase pooler URL (port 6543).

**Why it happens:** The Supabase transaction pooler (port 6543) does not support `CONCURRENTLY` DDL. `CREATE INDEX CONCURRENTLY` requires a non-pooled connection (port 5432, direct connection).

**How to avoid:** Use `CREATE INDEX IF NOT EXISTS` (not `CONCURRENTLY`) in migration 011 — the table will be empty at creation time so index creation is instant. Only use `CONCURRENTLY` for adding indexes to large existing tables.

**Warning signs:** `psycopg2.OperationalError: CREATE INDEX CONCURRENTLY cannot run inside a transaction block`

### Pitfall 3: Forgetting `deleted_at IS NULL` in Queries

**What goes wrong:** After soft-deleting a row (setting `deleted_at`), old queries that don't filter on `deleted_at` return the deleted row.

**Why it happens:** Soft delete is invisible to existing queries.

**How to avoid:** Every SELECT query in the CRUD layer MUST include `WHERE deleted_at IS NULL`. Consider a DB view as an alias if the pattern becomes repetitive.

### Pitfall 4: test_system_prompt.py Will Break If CHAT_SYSTEM_PROMPT Is Removed

**What goes wrong:** Existing tests in `tests/test_system_prompt.py` import `CHAT_SYSTEM_PROMPT` from `services/openai_coach.py`. If a seed script or migration removes this constant, those tests fail.

**Why it happens:** Phase 7 seeds the DB but does NOT change `openai_coach.py`. The constant stays in place until Phase 9 (dynamic prompt assembly).

**How to avoid:** Phase 7 must NOT modify `services/openai_coach.py`. The seed data goes into the database. The constant remains as-is. These two coexist until Phase 9.

### Pitfall 5: Missing commit() After DML in models.py

**What goes wrong:** An insert or update appears to succeed (no exception), but changes are not persisted because `conn.autocommit = False` (see `db.py`).

**Why it happens:** Flask's `g.db` connection has `autocommit = False`. DML requires explicit `conn.commit()`.

**How to avoid:** Every write function in models.py must call `get_db().commit()` after executing DML. Check existing write patterns in models.py for the reference pattern.

---

## Code Examples

### Table: personality_profile

```sql
-- Source: derived from schema.sql patterns in this project
CREATE TABLE IF NOT EXISTS personality_profile (
    id SERIAL PRIMARY KEY,
    rider_id INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    profile_type VARCHAR(10) NOT NULL CHECK (profile_type IN ('coach', 'rider')),
    -- Coach-specific typed fields (PROF-02)
    tone VARCHAR(20) CHECK (tone IN ('direct', 'warm', 'playful', 'serious', 'sarcastic')),
    humor_type VARCHAR(20) CHECK (humor_type IN ('none', 'dry', 'sarcastic', 'gentle', 'self-deprecating')),
    directness VARCHAR(10) CHECK (directness IN ('low', 'medium', 'high')),
    signature_phrases TEXT[],           -- SHORT phrases only; app layer enforces max 5 items, 80 chars each
    topic_biases TEXT[],
    topics_allowed TEXT[],
    -- Rider-specific typed fields (PROF-03)
    preferred_formality VARCHAR(10) CHECK (preferred_formality IN ('casual', 'mixed', 'formal')),
    humor_sensitivity VARCHAR(10) CHECK (humor_sensitivity IN ('low', 'medium', 'high')),
    encouragement_style VARCHAR(20) CHECK (encouragement_style IN ('data-driven', 'emotional', 'balanced', 'tough-love')),
    technical_depth VARCHAR(10) CHECK (technical_depth IN ('beginner', 'intermediate', 'expert')),
    -- Extraction metadata (PROF-04)
    extraction_source VARCHAR(10) CHECK (extraction_source IN ('whatsapp', 'blog', 'manual')),
    extraction_date DATE,
    source_message_count INTEGER,
    extraction_confidence VARCHAR(10) CHECK (extraction_confidence IN ('high', 'medium', 'low')),
    -- Audit (PROF-05)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by TEXT,
    deleted_at TIMESTAMPTZ,
    UNIQUE (rider_id, profile_type)
);

CREATE INDEX IF NOT EXISTS idx_personality_profile_rider ON personality_profile(rider_id);
CREATE INDEX IF NOT EXISTS idx_personality_profile_type ON personality_profile(profile_type);
```

### Table: gear_preference

```sql
-- Source: derived from schema.sql patterns in this project
CREATE TABLE IF NOT EXISTS gear_preference (
    id SERIAL PRIMARY KEY,
    rider_id INTEGER NOT NULL UNIQUE REFERENCES rider(id) ON DELETE CASCADE,
    -- Bike details
    bike_make VARCHAR(100),
    bike_model VARCHAR(100),
    bike_year INTEGER,
    bike_material VARCHAR(20) CHECK (bike_material IN ('aluminum', 'steel', 'titanium', 'carbon', 'other')),
    -- Categories as structured text (not free blobs)
    wheels_tires TEXT,
    lighting TEXT,
    bags TEXT,
    navigation TEXT,
    kit TEXT,
    -- Value orientation (GEAR-02 scoped here for table creation, admin UI in Phase 10)
    value_orientation VARCHAR(20) CHECK (value_orientation IN ('budget', 'mid-range', 'premium', 'buy-once-buy-right')),
    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by TEXT,
    deleted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_gear_preference_rider ON gear_preference(rider_id);
```

### Table: coach_assignment

```sql
-- Source: derived from schema.sql patterns in this project
CREATE TABLE IF NOT EXISTS coach_assignment (
    id SERIAL PRIMARY KEY,
    coach_rider_id INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    topic_domain VARCHAR(50) NOT NULL,     -- e.g. 'bikes', 'training', 'nutrition', 'general'
    is_default BOOLEAN DEFAULT FALSE,      -- TRUE for the fallback coach
    is_active BOOLEAN DEFAULT TRUE,
    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by TEXT,
    deleted_at TIMESTAMPTZ,
    UNIQUE (coach_rider_id, topic_domain)
);

CREATE INDEX IF NOT EXISTS idx_coach_assignment_coach ON coach_assignment(coach_rider_id);
CREATE INDEX IF NOT EXISTS idx_coach_assignment_domain ON coach_assignment(topic_domain);
CREATE INDEX IF NOT EXISTS idx_coach_assignment_active ON coach_assignment(is_active) WHERE is_active = TRUE;
```

### Table: coaching_guardrail

```sql
-- Source: derived from schema.sql patterns in this project
CREATE TABLE IF NOT EXISTS coaching_guardrail (
    id SERIAL PRIMARY KEY,
    rule_type VARCHAR(30) NOT NULL CHECK (rule_type IN ('topic_block', 'tone_limit', 'escalation', 'scope')),
    rule_value TEXT NOT NULL,              -- The guardrail content (e.g., 'never_shame_fitness')
    applies_to VARCHAR(10) DEFAULT 'all' CHECK (applies_to IN ('all', 'shriram', 'venki')),
    is_active BOOLEAN DEFAULT TRUE,
    rule_version INTEGER DEFAULT 1,        -- Incremented by trigger on UPDATE (GUARD-06)
    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by TEXT,
    deleted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_coaching_guardrail_type ON coaching_guardrail(rule_type);
CREATE INDEX IF NOT EXISTS idx_coaching_guardrail_active ON coaching_guardrail(is_active) WHERE is_active = TRUE;
```

### rule_version Trigger

```sql
-- Source: modeled on custom_ride_plan trigger in schema.sql
CREATE OR REPLACE FUNCTION increment_guardrail_rule_version()
RETURNS TRIGGER AS $$
BEGIN
    NEW.rule_version = OLD.rule_version + 1;
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_increment_guardrail_rule_version
    BEFORE UPDATE ON coaching_guardrail
    FOR EACH ROW
    EXECUTE FUNCTION increment_guardrail_rule_version();
```

### Model Function Pattern (from models.py conventions)

```python
# Source: models.py _execute() pattern — do NOT cache write functions
def get_personality_profile(rider_id, profile_type='coach'):
    """Get active personality profile for a rider. NOT CACHED — admin edits must be immediate."""
    return _execute(
        """SELECT * FROM personality_profile
           WHERE rider_id = %s AND profile_type = %s AND deleted_at IS NULL""",
        (rider_id, profile_type)
    ).fetchone()


def upsert_personality_profile(rider_id, profile_type, fields, updated_by='system'):
    """Insert or update personality profile. fields dict maps column names to values."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Build column list excluding id, created_at (set at insert only)
    set_clauses = ', '.join(f"{k} = %s" for k in fields.keys())
    values = list(fields.values())
    cur.execute(
        f"""INSERT INTO personality_profile (rider_id, profile_type, updated_by, {', '.join(fields.keys())})
            VALUES (%s, %s, %s, {', '.join(['%s'] * len(fields))})
            ON CONFLICT (rider_id, profile_type) DO UPDATE SET
            {set_clauses}, updated_by = %s, updated_at = NOW()""",
        [rider_id, profile_type, updated_by] + values + values + [updated_by]
    )
    conn.commit()
```

### Seed Script Pattern

```python
#!/usr/bin/env python3
"""Seed personality profiles for Shriram and Venki from CHAT_SYSTEM_PROMPT content.

Run: python scripts/seed_coaching_profiles.py
Idempotent: safe to run multiple times (uses ON CONFLICT DO UPDATE).
"""
import os
import psycopg2
import psycopg2.extras
from pathlib import Path

def get_db_url():
    env_file = Path(__file__).parent.parent / '.env'
    if env_file.exists():
        for line in open(env_file):
            if line.startswith('DATABASE_URL='):
                return line.strip().split('=', 1)[1]
    return os.getenv('DATABASE_URL')

SHRIRAM_PROFILE = {
    'profile_type': 'coach',
    'tone': 'direct',
    'humor_type': 'dry',
    'directness': 'high',
    'signature_phrases': ['recognizes riders by their bikes', 'loves gear upgrades'],
    'topic_biases': ['bikes', 'accessories', 'components', 'gear'],
    'topics_allowed': ['bike', 'gear', 'maintenance', 'components', 'fit', 'wheels'],
    'extraction_source': 'manual',
    'extraction_confidence': 'high',
    'updated_by': 'seed_script',
}

VENKI_PROFILE = {
    'profile_type': 'coach',
    'tone': 'playful',
    'humor_type': 'sarcastic',
    'directness': 'medium',
    'signature_phrases': ['tongue-in-cheek', 'guide figure'],
    'topic_biases': ['training philosophy', 'mental game', 'nutrition'],
    'topics_allowed': ['training', 'nutrition', 'randonneuring', 'strategy', 'general'],
    'extraction_source': 'manual',
    'extraction_confidence': 'high',
    'updated_by': 'seed_script',
}
# Rider lookup by name, then upsert personality_profile
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Hardcoded `CHAT_SYSTEM_PROMPT` string | Seeded `personality_profile` rows | Phase 7 | DB is now the source; code constant survives until Phase 9 |
| No version tracking on guardrails | `rule_version` INTEGER with trigger | Phase 7 | Braintrust evals can correlate eval results to specific rule set versions |
| All migrations via ad hoc Supabase SQL editor | Numbered migration files + standalone apply scripts | Phase 6 history | Repeatable, reviewable, version-controlled |

**Deprecated/outdated for Phase 7:**
- Do not use `CREATE INDEX CONCURRENTLY` in migration 011. Tables are new and empty; `CONCURRENTLY` is only needed for large live tables.

---

## Open Questions

1. **Which rider.id values correspond to Shriram and Venki?**
   - What we know: The `rider` table has `first_name`, `last_name`, `rusa_id`. Shriram and Venki are team members.
   - What's unclear: Their exact `rider.id` values are not in the codebase — must be queried from the live DB before seed script runs.
   - Recommendation: Seed script should look up by first name/last name (e.g., `SELECT id FROM rider WHERE first_name = 'Shriram'`) rather than hardcoding IDs, so it works regardless of row order.

2. **Should `coach_assignment` rows for Shriram/Venki also be seeded in Phase 7?**
   - What we know: Phase 7 plans include `coach_assignment` table creation. The success criteria say "manually seeded Shriram and Venki profiles" but don't explicitly call out coach_assignment seed rows.
   - What's unclear: Phase 9 plan (`COACH-02`, `COACH-03`) uses this table for routing. Phase 7 could pre-seed the domains to avoid a later data migration.
   - Recommendation: Seed basic `coach_assignment` rows for Shriram (domain: 'bikes') and Venki (domain: 'general', is_default: TRUE) in Phase 7 seed script. This preserves existing routing behavior.

3. **Does `gear_preference` need seed data for Shriram and Venki in Phase 7?**
   - What we know: GEAR-01/GEAR-02 are Phase 10 (admin UI). Phase 7 success criteria do not mention gear seed data.
   - What's unclear: Whether having the table empty is acceptable for testability.
   - Recommendation: Create the table, but do not seed it in Phase 7. Admin UI (Phase 10) is the right point of entry.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (configured in pytest.ini) |
| Config file | `pytest.ini` — `testpaths = tests`, `python_files = test_*.py` |
| Quick run command | `pytest tests/test_coaching_models.py -x` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PROF-01 | personality_profile table exists with typed columns | integration | `pytest tests/test_coaching_models.py::test_personality_profile_schema -x` | Wave 0 |
| PROF-02 | Coach fields (tone, humor_type, directness, signature_phrases, topic_biases, topics_allowed) present and typed | integration | `pytest tests/test_coaching_models.py::test_coach_profile_fields -x` | Wave 0 |
| PROF-03 | Rider fields (preferred_formality, humor_sensitivity, encouragement_style, technical_depth) present | integration | `pytest tests/test_coaching_models.py::test_rider_profile_fields -x` | Wave 0 |
| PROF-04 | Extraction metadata columns present (source, date, count, confidence) | integration | `pytest tests/test_coaching_models.py::test_extraction_metadata -x` | Wave 0 |
| PROF-05 | updated_at and updated_by columns present; update propagates | integration | `pytest tests/test_coaching_models.py::test_profile_audit_columns -x` | Wave 0 |
| GUARD-01 | coaching_guardrail table exists with rule_type, rule_value, is_active | integration | `pytest tests/test_coaching_models.py::test_guardrail_schema -x` | Wave 0 |
| GUARD-06 | rule_version increments on UPDATE | integration | `pytest tests/test_coaching_models.py::test_guardrail_version_increment -x` | Wave 0 |
| (preservation) | CHAT_SYSTEM_PROMPT unchanged; existing test_system_prompt.py still passes | unit | `pytest tests/test_system_prompt.py -x` | Yes (exists) |

**Note:** Integration tests require `DATABASE_URL` env var pointing to a test DB or the live Supabase instance. They use the `db_conn` fixture from `conftest.py` which rolls back after each test — safe for live DB testing.

### Sampling Rate

- **Per task commit:** `pytest tests/test_system_prompt.py -x` (existing, no DB needed, confirms no regression)
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_coaching_models.py` — all schema + CRUD tests listed above (covers PROF-01 through PROF-05, GUARD-01, GUARD-06)
- [ ] Wave 0 must create this file before plans 07-01 / 07-02 begin implementation

---

## Sources

### Primary (HIGH confidence)

- `/Users/msambhus/LocalDocuments/Personal/TeamAshaRandonneuring/schema/schema.sql` — Full existing DB schema; all table/index/FK/trigger patterns
- `/Users/msambhus/LocalDocuments/Personal/TeamAshaRandonneuring/models.py` — `_execute()` pattern, `RealDictCursor`, cache conventions, commit handling
- `/Users/msambhus/LocalDocuments/Personal/TeamAshaRandonneuring/migrations/apply_migration_standalone.py` — Canonical migration apply script pattern
- `/Users/msambhus/LocalDocuments/Personal/TeamAshaRandonneuring/migrations/009_add_strava_ride_analysis.sql` — `CREATE TABLE IF NOT EXISTS` migration file pattern
- `/Users/msambhus/LocalDocuments/Personal/TeamAshaRandonneuring/services/openai_coach.py` — `CHAT_SYSTEM_PROMPT` and `SYSTEM_PROMPT` source of truth for seed content

### Secondary (MEDIUM confidence)

- `/Users/msambhus/LocalDocuments/Personal/TeamAshaRandonneuring/.planning/STATE.md` — Confirmed: structured typed fields for personality data is a locked design decision (OWASP LLM01 defense)
- `/Users/msambhus/LocalDocuments/Personal/TeamAshaRandonneuring/tests/test_system_prompt.py` — Confirms test_system_prompt.py must not break; CHAT_SYSTEM_PROMPT constant must remain in place

### Tertiary (LOW confidence)

- psycopg2 TEXT[] array handling behavior — based on project convention + training knowledge; verify with a local test if array columns cause any cursor type issues

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new libraries, all existing patterns
- Architecture: HIGH — direct translation of existing migration and model patterns
- Pitfalls: HIGH for project-specific (commit(), soft delete filter) / MEDIUM for psycopg2 array behavior (training knowledge)
- Seed content: HIGH — content is directly readable from CHAT_SYSTEM_PROMPT in openai_coach.py

**Research date:** 2026-03-17
**Valid until:** 2026-09-17 (stable PostgreSQL/psycopg2 — no version movement expected)
