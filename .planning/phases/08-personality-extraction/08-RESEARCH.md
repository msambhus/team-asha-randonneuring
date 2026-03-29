# Phase 8: Personality Extraction - Research

**Researched:** 2026-03-17
**Domain:** OpenAI structured outputs (pydantic), WhatsApp text parsing, web/PDF extraction, PostgreSQL schema extension
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| EXTR-01 | Extract personality traits per person from WhatsApp exported chat logs using GPT-4o | OpenAI `client.chat.completions.parse()` with Pydantic models — already the project pattern (chat_service.py line 117). GPT-4o required for quality over noisy group chat data (STATE.md decision). |
| EXTR-02 | Extract personality traits from blog posts (WordPress URL and Google Drive PDF) | `trafilatura 2.0.0` for WordPress URL text extraction; `pdfplumber 0.11.9` for Google Drive PDF. Both are offline-friendly and Vercel-safe (run locally as scripts). |
| EXTR-03 | Capture tone, humor type, directness, encouragement style, domain bias, signature phrases, response length tendency, question-asking behavior | `personality_profile` table (Phase 7) has most fields. Missing: `response_length_tendency`, `question_asking_behavior`, `domain_bias`. Phase 8 must add these via a migration (`012_personality_extraction_fields.sql`). |
| EXTR-04 | Store 3-5 source example quotes per trait as evidence for admin verification | `personality_profile` has no evidence table. Phase 8 must create `personality_trait_evidence` table (trait_name, source_quote, extraction_source, rider_id). |
| EXTR-05 | Pre-filter WhatsApp noise before trait analysis | `scripts/whatsapp_parser.py` already has `MEDIA_SKIP_PATTERNS` and `is_cycling_chunk_rule()`. Extraction scripts reuse this parser; add per-sender message grouping and short-reaction filter (< 3 words). |
| EXTR-06 | Assign confidence level per trait based on source message volume (high/medium/low) | Thresholds: ≥50 qualifying messages = high, 20-49 = medium, <20 = low. Computed in Python before the LLM call; stored in existing `extraction_confidence` column on `personality_profile`. |
| EXTR-07 | Merge blog-derived traits with chat-derived traits, weighting blog more heavily | Merge script reads both source rows (extraction_source = 'whatsapp' and 'blog'), produces a merged row (extraction_source = 'merged'). Blog wins on enumeration conflicts; chat adds signature_phrases from real messages. |

</phase_requirements>

---

## Summary

Phase 8 is three offline CLI scripts plus one database migration. There is no Flask, no Vercel deployment, and no UI — all heavy LLM operations run locally as standalone Python scripts under `scripts/`.

The project already has: the `personality_profile` table (Phase 7), the WhatsApp parser (`scripts/whatsapp_parser.py`), and the OpenAI structured output pattern (`client.chat.completions.parse()` with Pydantic models in `services/chat_service.py`). Phase 8 extends the existing table with three missing columns and adds a new `personality_trait_evidence` table for source quote storage. The extraction scripts follow the same standalone pattern as `scripts/import_whatsapp.py` and `scripts/seed_coaching_profiles.py`.

The highest-uncertainty item is prompt quality — the LLM prompt that maps conversation text to structured personality fields will require 2-3 test runs with admin review before it is considered stable (logged in STATE.md blockers). Plan for this by making the extraction script output human-readable JSON before DB commit (`--dry-run` flag).

**Primary recommendation:** Three scripts (`extract_personality_whatsapp.py`, `extract_personality_blog.py`, `merge_personality.py`) + one migration (`012_personality_extraction_fields.sql`) using GPT-4o structured outputs, existing WhatsApp parser reuse, trafilatura for WordPress URL, and pdfplumber for Google Drive PDF.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| openai | 2.24.0 (in requirements.txt) | GPT-4o structured output extraction | Already the project's AI client; `client.chat.completions.parse()` is the established pattern |
| pydantic | v2 (already pulled in by openai) | Structured output schema definitions | Already used in chat_service.py for IntentResult; `client.chat.completions.parse()` requires Pydantic models |
| psycopg2-binary | 2.9.9 | Write extracted traits to PostgreSQL | Existing DB driver; standalone script pattern same as seed_coaching_profiles.py |
| trafilatura | 2.0.0 | WordPress URL text extraction (EXTR-02) | STATE.md decision; strips nav/footer/ads; outputs clean text; stdlib-only fallback |
| pdfplumber | 0.11.9 | Google Drive PDF text extraction (EXTR-02) | STATE.md decision; reliable for text PDFs; minimal deps |
| scripts/whatsapp_parser.py | internal | Parse WhatsApp .txt exports | Already handles U+202F timestamps, multiline messages, system message filtering |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| python-dotenv | 1.2.1 | Load DATABASE_URL / OPENAI_API_KEY from .env | Existing env loading pattern for all scripts |
| zipfile | stdlib | Unzip WhatsApp .zip exports | WhatsApp exports are zipped; need to extract _chat.txt before parsing |
| pathlib | stdlib | File path handling | Consistent with all existing scripts |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| trafilatura | beautifulsoup4 | bs4 already in requirements.txt but requires more custom strip logic; trafilatura is purpose-built for article extraction |
| pdfplumber | pypdf / PyPDF2 | pdfplumber is more reliable for layout-sensitive text; STATE.md chose it explicitly |
| client.chat.completions.parse() | instructor library | Instructor adds 50MB+ deps; project already uses parse() natively in chat_service.py — no additional library needed |
| GPT-4o | GPT-4o-mini | STATE.md explicitly requires GPT-4o for noisy WhatsApp data quality |

**Installation (new libraries only):**
```bash
pip install trafilatura==2.0.0 pdfplumber==0.11.9
# Add to requirements-dev.txt (local scripts, not deployed to Vercel)
```

These belong in `requirements-dev.txt`, not `requirements.txt` — extraction scripts never run on Vercel.

---

## Architecture Patterns

### Recommended Project Structure

```
migrations/
├── 012_personality_extraction_fields.sql   # New: 3 missing columns + personality_trait_evidence table
├── apply_migration_012.py                  # New: standalone apply script
scripts/
├── extract_personality_whatsapp.py         # New: WhatsApp extraction script (EXTR-01, 03, 05, 06)
├── extract_personality_blog.py             # New: Blog extraction script (EXTR-02, 03, 04)
├── merge_personality.py                    # New: Merge WhatsApp + blog profiles (EXTR-07)
├── whatsapp_parser.py                      # Existing: reused as-is
tests/
├── test_personality_extraction.py          # New: unit tests for extraction logic
```

### Pattern 1: Per-Sender Message Grouping (WhatsApp extraction)

**What:** After parsing the full export with `parse_export()`, group messages by `sender` field to get each person's message corpus. Apply noise filters at message level (not chunk level) since personality extraction is person-scoped not topic-scoped.

**When to use:** WhatsApp personality extraction only. Topic-chunking is for RAG/knowledge retrieval (import_whatsapp.py); personality extraction needs per-person message lists.

**Noise filters to apply before LLM call (EXTR-05):**
1. `is_system = True` — already flagged by parser
2. `content` matches any `MEDIA_SKIP_PATTERNS` from whatsapp_parser.py
3. `len(content.split()) < 3` — short reactions ("ok", "yes", "lol", thumbs up reactions)
4. Content is a URL only (starts with `https://` and has no other words)

**Confidence calculation (EXTR-06):**
```python
def compute_confidence(qualifying_message_count: int) -> str:
    if qualifying_message_count >= 50:
        return 'high'
    elif qualifying_message_count >= 20:
        return 'medium'
    else:
        return 'low'
```

**Example:**
```python
# Source: derived from scripts/whatsapp_parser.py parse_export() pattern
from scripts.whatsapp_parser import parse_export, MEDIA_SKIP_PATTERNS

def group_by_sender(filepath: str) -> dict[str, list[dict]]:
    """Parse WhatsApp export and group qualifying messages by sender name."""
    all_messages = parse_export(filepath)
    by_sender: dict[str, list[dict]] = {}
    for msg in all_messages:
        if msg['is_system']:
            continue
        content = msg['content'].strip()
        if any(p in content.lower() for p in MEDIA_SKIP_PATTERNS):
            continue
        if len(content.split()) < 3:
            continue
        if content.startswith('https://') and ' ' not in content:
            continue
        by_sender.setdefault(msg['sender'], []).append(msg)
    return by_sender
```

### Pattern 2: GPT-4o Structured Output with Pydantic (EXTR-01, 03, 04)

**What:** Define a Pydantic model for the full personality profile + evidence quotes. Call `client.chat.completions.parse()` — the same approach already used in `chat_service.py` for `IntentResult`. Send a sample of the person's messages (up to ~200 qualifying messages, sampled across time) as the user prompt.

**When to use:** Both WhatsApp and blog extraction. The schema is the same Pydantic model — only the user prompt changes.

**Critical: send only a MESSAGE SAMPLE, not all messages.** With GPT-4o's 128K context and average WhatsApp message length ~20 words, 200 messages ≈ 4000 tokens. This keeps cost reasonable and avoids context limits.

**Example:**
```python
# Source: derived from chat_service.py IntentResult pattern (line 62-123)
from pydantic import BaseModel
from typing import Literal, Optional
from openai import OpenAI

class TraitEvidence(BaseModel):
    """3-5 verbatim quotes that justify the trait value."""
    quotes: list[str]  # max 5 items, each max 200 chars

class PersonalityExtraction(BaseModel):
    tone: Literal['direct', 'warm', 'playful', 'serious', 'sarcastic']
    humor_type: Literal['none', 'dry', 'sarcastic', 'gentle', 'self-deprecating']
    directness: Literal['low', 'medium', 'high']
    encouragement_style: Literal['data-driven', 'emotional', 'balanced', 'tough-love']
    technical_depth: Literal['beginner', 'intermediate', 'expert']
    domain_bias: Optional[str] = None          # e.g. "gear and components"
    response_length_tendency: Literal['brief', 'moderate', 'verbose']
    question_asking_behavior: Literal['rarely', 'sometimes', 'frequently']
    signature_phrases: list[str]               # max 5 items
    # Evidence: 3-5 quotes per trait category
    tone_evidence: TraitEvidence
    humor_evidence: TraitEvidence
    directness_evidence: TraitEvidence
    signature_phrase_evidence: TraitEvidence

def extract_from_messages(client: OpenAI, messages: list[dict], sender_name: str) -> PersonalityExtraction:
    sample = messages[:200]  # cap context
    formatted = '\n'.join(
        f"[{m['ts'].strftime('%Y-%m-%d')}] {m['content']}"
        for m in sample
    )
    response = client.chat.completions.parse(
        model='gpt-4o',
        messages=[
            {'role': 'system', 'content': EXTRACTION_SYSTEM_PROMPT},
            {'role': 'user', 'content': f"Analyze {sender_name}'s messages:\n\n{formatted}"},
        ],
        response_format=PersonalityExtraction,
        temperature=0,  # deterministic for consistency
    )
    return response.choices[0].message.parsed
```

### Pattern 3: WordPress URL Extraction with trafilatura (EXTR-02)

**What:** `trafilatura.fetch_url()` + `trafilatura.extract()` strips nav, sidebars, ads from WordPress HTML and returns article text only. No BeautifulSoup needed.

**When to use:** Blog extraction from any URL. The output is a plain text string ready to pass directly to the LLM.

**Example:**
```python
# Source: trafilatura 2.0.0 docs — https://trafilatura.readthedocs.io/en/latest/usage-python.html
import trafilatura

def fetch_blog_text(url: str) -> str:
    downloaded = trafilatura.fetch_url(url)
    if downloaded is None:
        raise ValueError(f"Failed to fetch: {url}")
    text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
    if not text:
        raise ValueError(f"No extractable text from: {url}")
    return text
```

### Pattern 4: Google Drive PDF Extraction with pdfplumber (EXTR-02)

**What:** `pdfplumber.open()` + `page.extract_text()` per page. Handles text PDFs reliably. Does NOT handle scanned PDFs (requires OCR). Venki's Google Drive PDF is assumed to be text-based (machine-generated, not scanned).

**Important pre-check:** The PDF must be downloaded locally first. If the Google Drive PDF is private, the user must manually download it. The script accepts a local file path. The STATE.md blocker notes this: "Confirm Venki's Google Drive PDF is publicly accessible before building blog extraction."

**Example:**
```python
# Source: pdfplumber 0.11.9 docs — https://github.com/jsvine/pdfplumber
import pdfplumber

def extract_pdf_text(pdf_path: str) -> str:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text.strip())
    full_text = '\n\n'.join(pages)
    if not full_text.strip():
        raise ValueError(f"No text extracted from PDF: {pdf_path}. May be scanned.")
    return full_text
```

### Pattern 5: Merge Logic (EXTR-07)

**What:** A standalone script reads both `extraction_source = 'whatsapp'` and `extraction_source = 'blog'` rows for the same `rider_id`, combines them into one merged row, and upserts as `extraction_source = 'merged'`. Blog wins on enumeration conflicts; WhatsApp contributes `signature_phrases` from real conversation.

**Merge rules:**
1. For `Literal` enum fields (tone, humor_type, directness, etc.): use blog value if both sources have it; otherwise use whichever source has it
2. For `signature_phrases`: union of both lists, deduplicated, capped at 5 items
3. For `domain_bias`, `response_length_tendency`, `question_asking_behavior`: blog wins if present; fall back to WhatsApp
4. `extraction_confidence`: use the LOWER of the two source confidence values (conservative merge)
5. `source_message_count`: sum of both sources

**Example:**
```python
# Source: project pattern — models.py upsert_personality_profile()
def merge_profiles(whatsapp_row: dict, blog_row: dict) -> dict:
    """Merge blog (priority) with WhatsApp profile. Returns merged field dict."""
    enum_fields = ['tone', 'humor_type', 'directness', 'encouragement_style',
                   'technical_depth', 'response_length_tendency',
                   'question_asking_behavior', 'domain_bias']
    merged = {}
    for field in enum_fields:
        # Blog wins if present; fall back to WhatsApp
        merged[field] = blog_row.get(field) or whatsapp_row.get(field)

    # Signature phrases: union, capped at 5
    wa_phrases = whatsapp_row.get('signature_phrases') or []
    blog_phrases = blog_row.get('signature_phrases') or []
    seen = set()
    combined = []
    for p in blog_phrases + wa_phrases:
        if p not in seen:
            seen.add(p)
            combined.append(p)
    merged['signature_phrases'] = combined[:5]

    # Confidence: take the lower
    confidence_rank = {'high': 2, 'medium': 1, 'low': 0}
    wa_conf = whatsapp_row.get('extraction_confidence', 'low')
    blog_conf = blog_row.get('extraction_confidence', 'low')
    merged['extraction_confidence'] = wa_conf if confidence_rank[wa_conf] <= confidence_rank[blog_conf] else blog_conf

    merged['source_message_count'] = (whatsapp_row.get('source_message_count') or 0) + (blog_row.get('source_message_count') or 0)
    merged['extraction_source'] = 'merged'
    return merged
```

### Pattern 6: personality_trait_evidence Table (EXTR-04)

**What:** A new table that stores 3-5 verbatim source quotes per person per trait. Keyed by `rider_id + trait_name + extraction_source`. The admin UI (Phase 10) queries this table alongside `personality_profile` to display quote evidence.

**Schema to create in migration 012:**
```sql
CREATE TABLE IF NOT EXISTS personality_trait_evidence (
    id SERIAL PRIMARY KEY,
    rider_id INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    trait_name VARCHAR(50) NOT NULL,           -- e.g. 'tone', 'humor_type', 'signature_phrases'
    source_quote TEXT NOT NULL,                -- verbatim message/blog text, max ~500 chars
    extraction_source VARCHAR(10) NOT NULL CHECK (extraction_source IN ('whatsapp', 'blog', 'merged')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trait_evidence_rider ON personality_trait_evidence(rider_id);
CREATE INDEX IF NOT EXISTS idx_trait_evidence_trait ON personality_trait_evidence(rider_id, trait_name);
```

**Important: evidence rows are REPLACED (delete + insert), not merged.** Each extraction run deletes old evidence for `(rider_id, extraction_source)` and inserts fresh rows. This matches the "re-extraction" flow in ADMN-05.

### Anti-Patterns to Avoid

- **Sending all WhatsApp messages to the LLM:** Group chats can have thousands of messages. Cap at 200 qualifying messages per sender, sampled across time (not just the most recent). Sending everything exceeds context limits and adds unnecessary cost.
- **Storing evidence as free-text blobs in personality_profile:** EXTR-04 requires admin-verifiable quotes. These belong in a separate `personality_trait_evidence` table, not jammed into `personality_profile` as TEXT columns.
- **Using instructor library:** The project already uses `client.chat.completions.parse()` natively (chat_service.py line 117). Instructor is redundant and adds 50MB+ of deps that bloat Vercel bundles (even though extraction scripts run locally, the repo size still matters).
- **Writing extraction scripts as Flask routes or request handlers:** Vercel has 60-300s timeouts. Extraction takes minutes. All extraction is offline CLI only (STATE.md decision).
- **Hard-coding rider names as strings for DB lookup:** Use the same `lookup_rider(cur, first_name_pattern)` pattern from `seed_coaching_profiles.py`. Never hardcode rider IDs.
- **Merging into the 'whatsapp' or 'blog' source rows:** The merge creates a NEW row with `extraction_source = 'merged'`. The originals are preserved so re-extraction can start fresh.
- **Extracting the full ZIP inline:** `zipfile.extractall()` to a temp directory, then pass the `_chat.txt` path to `parse_export()`. Don't try to parse inside the zip.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTML article extraction | Custom BeautifulSoup parser | trafilatura 2.0.0 | trafilatura handles nav/sidebar/ad removal; custom parsers break on layout changes |
| PDF text extraction | Custom pdfminer.six calls | pdfplumber 0.11.9 | pdfplumber wraps pdfminer with a clean API; page.extract_text() works reliably |
| Structured LLM output | JSON parsing + validation loops | `client.chat.completions.parse()` with Pydantic | Native SDK support; automatic validation; retry on parse failure built in |
| WhatsApp parsing | New regex-based parser | `scripts/whatsapp_parser.py` | Existing parser handles U+202F timestamps, multiline, system message detection |
| Confidence calculation | LLM-based confidence | Message count thresholds | Message count is objective; LLM-based confidence is circular and adds cost |

**Key insight:** The extraction problem sounds novel but the hard parts (parsing, text extraction, structured LLM output) are already solved. The only genuinely new work is the extraction prompt and evidence storage.

---

## Common Pitfalls

### Pitfall 1: WhatsApp ZIP Exports Contain Unicode Filenames

**What goes wrong:** The WhatsApp exports in `data/whatsapp/` are ZIP files (e.g., `WhatsApp Chat - Asha biking _ a fresh start.zip`). `zipfile.ZipFile` opens them fine, but the inner `_chat.txt` filename may contain Unicode or non-ASCII characters on some export formats.

**Why it happens:** iOS and Android WhatsApp exports differ in inner filename format. The script must handle both.

**How to avoid:** When extracting from ZIP, search for any `.txt` file inside, not just `_chat.txt` by exact name: `next(n for n in z.namelist() if n.endswith('.txt'))`.

**Warning signs:** `KeyError: '_chat.txt'` during ZipFile member access.

### Pitfall 2: GPT-4o Temperature Must Be 0 for Consistency

**What goes wrong:** Using `temperature > 0` for extraction causes different `Literal` field values on successive runs of the same input. This makes re-extraction non-deterministic and confuses admin review.

**Why it happens:** Personality extraction is classification, not generation. High temperature introduces random variation.

**How to avoid:** Always set `temperature=0` for extraction calls. The extraction system prompt should also say "classify, do not infer" to reinforce determinism.

### Pitfall 3: Blog Extraction Fails on Google Drive PDF Accessibility

**What goes wrong:** The script tries to download Venki's Google Drive PDF but the URL returns a login redirect or "Access Denied" HTML page, not a PDF.

**Why it happens:** Google Drive PDFs may require auth if not set to "Anyone with the link can view".

**How to avoid:** Accept a local file path as the primary input (`--pdf-path`). If the user passes a URL starting with `https://drive.google.com`, warn them to download manually first. STATE.md blocker: "Confirm Venki's Google Drive PDF is publicly accessible before building blog extraction."

**Warning signs:** `pdfplumber.open()` raises `pdfminer.high_level.PDFSyntaxError` or the file is 0 bytes.

### Pitfall 4: Pydantic `list[str]` Fields Have No DB-Level Length Enforcement

**What goes wrong:** The `PersonalityExtraction` model allows `signature_phrases: list[str]` with any length. GPT-4o may return 10+ phrases, which when stored as `TEXT[]` in PostgreSQL violates the admin UI expectation of 3-5 items.

**Why it happens:** Pydantic list validation doesn't have a max_length constraint by default. You need `Field(max_length=5)` or post-extraction truncation.

**How to avoid:** Use `from pydantic import Field` and annotate: `signature_phrases: list[str] = Field(max_items=5)`. Also truncate each phrase to 80 chars before DB insert.

### Pitfall 5: sender Name Matching Across Multiple WhatsApp Groups

**What goes wrong:** "Venki" may appear as "Venki Raghu", "Venkataraman Raghunathan", or "Venki R" in different WhatsApp exports. When grouping by sender, the same person gets multiple buckets.

**Why it happens:** WhatsApp sender names come from each user's device contact book, which varies per exporter.

**How to avoid:** The extraction script must accept a `--sender-name` argument (or a name mapping file) rather than auto-discovering all senders. For MVP, run per-person with explicit `--sender Venki` and let the script fuzzy-match (case-insensitive `startswith`).

### Pitfall 6: personality_trait_evidence Accumulates Without Cleanup

**What goes wrong:** Each extraction run inserts new evidence rows. After 3 re-extractions, the same rider has 9-15 evidence rows for the same traits from the same source, confusing admin review.

**Why it happens:** Naive INSERT without cleanup.

**How to avoid:** At the start of each extraction run, DELETE existing evidence rows for `(rider_id, extraction_source)` before inserting new ones. This makes re-extraction idempotent.

```python
# Delete old evidence before insert (makes re-extraction idempotent)
cur.execute(
    "DELETE FROM personality_trait_evidence WHERE rider_id = %s AND extraction_source = %s",
    (rider_id, extraction_source)
)
```

### Pitfall 7: Missing `response_length_tendency`, `question_asking_behavior`, `domain_bias` Columns

**What goes wrong:** The Phase 7 migration (`011_personality_coaching_tables.sql`) does NOT include `response_length_tendency`, `question_asking_behavior`, or `domain_bias` on `personality_profile`. Attempting to upsert these fields fails with `psycopg2.errors.UndefinedColumn`.

**Why it happens:** Phase 7 was scoped to schema scaffolding based on PROF-02/PROF-03 fields. EXTR-03 adds three more personality dimensions not in those requirements.

**How to avoid:** Migration `012_personality_extraction_fields.sql` (Wave 0 of Phase 8) adds the three columns before any extraction scripts run.

---

## Code Examples

### Extraction System Prompt

```python
# Source: project prompt engineering pattern (services/openai_coach.py SYSTEM_PROMPT)
EXTRACTION_SYSTEM_PROMPT = """\
You are a personality analyst. Given a sample of WhatsApp messages from one person, \
classify their communication style using ONLY the information visible in their messages. \
Do not infer traits that aren't evidenced in the text.

For each trait field, you MUST also provide 3-5 verbatim quotes from the messages that \
justify your classification. Quotes must be exact text from the provided messages, \
under 200 characters each.

Classification rules:
- tone: how they come across emotionally (direct=gets to point fast, warm=nurturing, \
  playful=jokes/puns, serious=formal, sarcastic=ironic)
- humor_type: the kind of humor they use (none, dry, sarcastic, gentle, self-deprecating)
- directness: how quickly they get to the point (low=lots of context, medium=balanced, high=blunt)
- response_length_tendency: typical message length (brief=1-2 sentences, moderate=paragraph, verbose=multi-paragraph)
- question_asking_behavior: how often they ask questions (rarely, sometimes, frequently)
- signature_phrases: recurring phrases, expressions, or words unique to this person (up to 5)
- domain_bias: the topic area they talk about most (one short phrase, e.g. "gear and components")

If there are fewer than 20 qualifying messages, still classify but flag LOW confidence in \
your assessment notes.
"""
```

### Migration 012 (adds missing columns + evidence table)

```sql
-- Migration 012: Add personality extraction fields and evidence table
-- Phase 8: Personality Extraction
-- Adds 3 missing columns to personality_profile and creates personality_trait_evidence

-- Add missing EXTR-03 columns to personality_profile
ALTER TABLE personality_profile
    ADD COLUMN IF NOT EXISTS response_length_tendency VARCHAR(10)
        CHECK (response_length_tendency IN ('brief', 'moderate', 'verbose')),
    ADD COLUMN IF NOT EXISTS question_asking_behavior VARCHAR(15)
        CHECK (question_asking_behavior IN ('rarely', 'sometimes', 'frequently')),
    ADD COLUMN IF NOT EXISTS domain_bias VARCHAR(100);  -- free-text short phrase, app-layer sanitized

-- Source quotes table (EXTR-04)
CREATE TABLE IF NOT EXISTS personality_trait_evidence (
    id SERIAL PRIMARY KEY,
    rider_id INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    trait_name VARCHAR(50) NOT NULL,
    source_quote TEXT NOT NULL,
    extraction_source VARCHAR(10) NOT NULL
        CHECK (extraction_source IN ('whatsapp', 'blog', 'merged')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trait_evidence_rider ON personality_trait_evidence(rider_id);
CREATE INDEX IF NOT EXISTS idx_trait_evidence_rider_trait
    ON personality_trait_evidence(rider_id, trait_name);
```

### WhatsApp Extraction Script Skeleton

```python
#!/usr/bin/env python3
"""Extract personality traits per sender from WhatsApp chat export.

Usage:
    python scripts/extract_personality_whatsapp.py \
        --path data/whatsapp/fresh_start/_chat.txt \
        --sender "Venki" \
        --dry-run

    python scripts/extract_personality_whatsapp.py \
        --path data/whatsapp/chat.zip \
        --sender "Shriram"
"""
import argparse
import sys
import zipfile
import tempfile
import os
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from scripts.whatsapp_parser import parse_export, MEDIA_SKIP_PATTERNS
# ... (group_by_sender, compute_confidence, extract_from_messages, store_results)
```

### Blog Extraction Script Skeleton

```python
#!/usr/bin/env python3
"""Extract personality traits from a blog URL or local PDF file.

Usage:
    # WordPress URL (Mihir's blog)
    python scripts/extract_personality_blog.py \
        --url https://unexpectedathlete.wordpress.com/2023/09/06/... \
        --rider-name Mihir

    # Local PDF (Venki's Google Drive PDF, downloaded manually)
    python scripts/extract_personality_blog.py \
        --pdf-path /path/to/venki_blog.pdf \
        --rider-name Venki
"""
```

### Merge Script Skeleton

```python
#!/usr/bin/env python3
"""Merge WhatsApp and blog personality profiles for a rider.

Usage:
    python scripts/merge_personality.py --rider-name Venki
"""
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Hardcoded personality in CHAT_SYSTEM_PROMPT | Seeded DB rows (manual, extraction_source='manual') | Phase 7 | DB is source of truth; code constant coexists until Phase 9 |
| No per-person chat analysis | Offline CLI extraction scripts | Phase 8 | Admin can re-extract after new chat exports without code changes |
| Free-text personality blobs | Structured typed columns + trait evidence table | Phase 7+8 | Prompt injection defense; admin-verifiable |
| GPT-4o-mini for all tasks | GPT-4o for personality extraction only | STATE.md decision | Better quality on noisy short messages; higher cost but offline one-time |

**Deprecated/outdated:**
- Instructor library: was evaluated and rejected (STATE.md) — OpenAI SDK `parse()` is sufficient
- LangChain: explicitly out of scope (50MB deps, Vercel conflicts, in REQUIREMENTS.md Out of Scope table)

---

## Open Questions

1. **Venki's Google Drive PDF accessibility**
   - What we know: The PDF is on Google Drive; URL is not confirmed public (STATE.md blocker)
   - What's unclear: Whether it requires Google auth or is publicly accessible
   - Recommendation: Script must accept `--pdf-path` for local file; document that user must download manually if private. Do not build Google API auth — adds 30MB+ deps.

2. **WhatsApp sender name disambiguation**
   - What we know: Exports are from two group chats; same person may appear under different contact names
   - What's unclear: Whether "Venki" appears consistently or as multiple variants across groups
   - Recommendation: Audit actual ZIP contents before writing extraction script. Script should accept `--sender` with a prefix match (case-insensitive), warn on zero matches, error cleanly.

3. **Prompt quality uncertainty**
   - What we know: Personality extraction prompts require iteration; STATE.md flags this as highest-uncertainty item
   - What's unclear: How reliably GPT-4o classifies 5-message vs 200-message corpora
   - Recommendation: `--dry-run` flag outputs extracted JSON to stdout without DB write. Plan for 2-3 test runs with admin review before committing to prod DB.

4. **extraction_source constraint in personality_profile**
   - What we know: Current `personality_profile.extraction_source` CHECK only allows ('whatsapp', 'blog', 'manual')
   - What's unclear: Merge creates a 'merged' row — but 'merged' is not in the constraint
   - Recommendation: Migration 012 must also ALTER the CHECK on `personality_profile.extraction_source` to add 'merged': `CHECK (extraction_source IN ('whatsapp', 'blog', 'manual', 'merged'))`.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.3.0 (configured in pytest.ini) |
| Config file | `pytest.ini` — `testpaths = tests`, `python_files = test_*.py` |
| Quick run command | `pytest tests/test_personality_extraction.py -x` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EXTR-01 | GPT-4o extraction returns PersonalityExtraction model (mocked LLM) | unit | `pytest tests/test_personality_extraction.py::test_extract_from_messages_returns_model -x` | Wave 0 |
| EXTR-02 (URL) | trafilatura fetches and extracts blog text | unit (mock HTTP) | `pytest tests/test_personality_extraction.py::test_fetch_blog_text_from_url -x` | Wave 0 |
| EXTR-02 (PDF) | pdfplumber extracts text from a test PDF fixture | unit | `pytest tests/test_personality_extraction.py::test_extract_pdf_text -x` | Wave 0 |
| EXTR-03 | PersonalityExtraction model includes all 8 required fields | unit | `pytest tests/test_personality_extraction.py::test_extraction_model_fields -x` | Wave 0 |
| EXTR-04 | Evidence table inserts 3-5 quotes per trait after extraction | integration | `pytest tests/test_personality_extraction.py::test_evidence_quotes_stored -x` | Wave 0 |
| EXTR-05 | group_by_sender filters system, media, and short-reaction messages | unit | `pytest tests/test_personality_extraction.py::test_group_by_sender_filters_noise -x` | Wave 0 |
| EXTR-06 | compute_confidence returns correct level for <20, 20-49, ≥50 messages | unit | `pytest tests/test_personality_extraction.py::test_compute_confidence -x` | Wave 0 |
| EXTR-07 | merge_profiles gives blog priority on enum conflicts; unions phrases | unit | `pytest tests/test_personality_extraction.py::test_merge_profiles_blog_wins -x` | Wave 0 |
| Schema | personality_profile has response_length_tendency, question_asking_behavior, domain_bias | integration | `pytest tests/test_personality_extraction.py::test_migration_012_columns -x` | Wave 0 |
| Schema | personality_trait_evidence table exists with correct columns | integration | `pytest tests/test_personality_extraction.py::test_trait_evidence_schema -x` | Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/test_personality_extraction.py -x` (no DB needed for unit tests)
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_personality_extraction.py` — all unit and integration tests listed above
- [ ] `migrations/012_personality_extraction_fields.sql` — add 3 missing columns + evidence table + fix extraction_source CHECK
- [ ] `migrations/apply_migration_012.py` — standalone apply script (same pattern as apply_migration_011.py)
- [ ] Install new libs: `pip install trafilatura==2.0.0 pdfplumber==0.11.9` + add to `requirements-dev.txt`

---

## Sources

### Primary (HIGH confidence)

- `/Users/msambhus/LocalDocuments/Personal/TeamAshaRandonneuring/services/chat_service.py` — Lines 62-129: `IntentResult` Pydantic model + `client.chat.completions.parse()` — this IS the extraction pattern
- `/Users/msambhus/LocalDocuments/Personal/TeamAshaRandonneuring/scripts/whatsapp_parser.py` — Full parser, MEDIA_SKIP_PATTERNS, parse_export() — reused as-is
- `/Users/msambhus/LocalDocuments/Personal/TeamAshaRandonneuring/scripts/import_whatsapp.py` — Standalone script pattern, DB connection, dry-run flag, argparse structure
- `/Users/msambhus/LocalDocuments/Personal/TeamAshaRandonneuring/scripts/seed_coaching_profiles.py` — `lookup_rider()` by name pattern, upsert pattern, idempotent design
- `/Users/msambhus/LocalDocuments/Personal/TeamAshaRandonneuring/migrations/011_personality_coaching_tables.sql` — Confirmed: personality_profile lacks response_length_tendency, question_asking_behavior, domain_bias columns
- `/Users/msambhus/LocalDocuments/Personal/TeamAshaRandonneuring/.planning/STATE.md` — Locked decisions: GPT-4o required, instructor rejected, trafilatura 2.0.0 + pdfplumber 0.11.9 chosen, local CLI only

### Secondary (MEDIUM confidence)

- [trafilatura 2.0.0 PyPI](https://pypi.org/project/trafilatura/) — Confirmed current version is 2.0.0; `fetch_url()` + `extract()` is the canonical API
- [pdfplumber PyPI](https://pypi.org/project/pdfplumber/) — Confirmed current version is 0.11.9; `page.extract_text()` is the extraction API
- [trafilatura docs](https://trafilatura.readthedocs.io/en/latest/usage-python.html) — Usage verified: fetch_url + extract pattern confirmed

### Tertiary (LOW confidence)

- GPT-4o 200-message sampling budget: derived from 128K context / ~20 words/message average. Exact optimal sample size requires empirical testing during development.
- Confidence thresholds (≥50=high, 20-49=medium, <20=low): reasonable defaults; should be reviewed after first test extraction run.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries confirmed; extraction pattern directly observable in existing code
- Architecture: HIGH — three-script structure, migration pattern, and Pydantic model approach all follow established project conventions
- Pitfalls: HIGH for schema gaps (directly verified in migration SQL) and ZIP handling; MEDIUM for sender name disambiguation (needs actual file audit)
- Prompt quality: LOW — extraction prompts require empirical iteration; plan for test runs

**Research date:** 2026-03-17
**Valid until:** 2026-04-17 (OpenAI SDK and Pydantic stable; trafilatura/pdfplumber versions confirmed current)
