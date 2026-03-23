# Phase 12: Knowledge Base Expansion - Research

**Researched:** 2026-03-18
**Domain:** Web crawling, content extraction, pgvector embedding, Flask admin UI
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| KB-01 | System crawls URLs from the resources Google Sheets spreadsheet | Google Sheets CSV export pattern already used in `update_rusa_events.py`; no new library needed |
| KB-02 | Crawled content extracted (main text only), chunked, and embedded using text-embedding-3-small | trafilatura 2.0.0 already in requirements-dev.txt; embed_texts() pattern in import_whatsapp.py reusable |
| KB-03 | Embedded content stored in existing pgvector table with `web_*` source prefix | whatsapp_chunk table accepts any `source` TEXT value; HNSW index already covers web_ rows automatically |
| KB-04 | Admin can view list of embedded sources with URL, embed date, and chunk count | Requires SQL GROUP BY source query + new admin route + template; pattern established by guardrails/gear pages |
| KB-05 | Admin can trigger re-embed per source (refresh stale content) | POST endpoint that deletes existing chunks for source + re-runs embed pipeline; same admin auth pattern |
| KB-06 | Admin can remove all embeddings from a specific source | DELETE FROM whatsapp_chunk WHERE source = %s; POST endpoint with confirm step |
</phase_requirements>

---

## Summary

Phase 12 builds two things: a CLI script (`scripts/embed_resources.py`) that crawls URLs from the resources Google Sheets spreadsheet and stores embeddings in the existing `whatsapp_chunk` table, and an admin UI page (`/admin/knowledge`) that lets the admin view, re-embed, and remove embedded sources.

All critical infrastructure already exists. The `whatsapp_chunk` table, its HNSW index, the `embed_texts()` function, the Google Sheets CSV export pattern (`update_rusa_events.py`), and the trafilatura 2.0.0 library (`requirements-dev.txt`) are already in the codebase. Phase 12 is primarily a wiring phase — adapting existing patterns for a new data source.

The duplicate-detection strategy uses SHA-256 content hashing stored as a column in `whatsapp_chunk` (or a separate metadata table). Web chunks differ from WhatsApp chunks: they lack `chunk_start`/`chunk_end` timestamps, so the existing UNIQUE constraint `(source, chunk_start, chunk_end)` does not prevent re-embedding. A content hash check resolves this without schema ambiguity.

**Primary recommendation:** Reuse `embed_texts()` from `import_whatsapp.py` verbatim. Add a `content_hash` column to `whatsapp_chunk` via migration, use SHA-256 of extracted text as the idempotency key for web chunks, and set `chunk_start = chunk_end = embed_time` as sentinel values to satisfy the existing NOT NULL constraints without changing them.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| trafilatura | 2.0.0 | Fetch URL + extract main body text (strips nav, footer, ads) | Already in requirements-dev.txt; PROJECT.md decision |
| openai | 2.24.0 | text-embedding-3-small embeddings | Already in use throughout codebase |
| psycopg2-binary | 2.9.9 | pgvector bulk insert | Already in use; pgvector integration tested |
| requests | 2.31.0 | Google Sheets CSV export download | Already in requirements.txt; same usage as update_rusa_events.py |
| hashlib | stdlib | SHA-256 content fingerprinting | No install needed; prevents re-embedding duplicates |
| csv | stdlib | Parse Google Sheets CSV export | No install needed; same pattern as update_rusa_events.py |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pgvector | 0.4.2 | register_vector for psycopg2 | Required for embedding insert; already in requirements-dev.txt |
| numpy | latest | Embedding array conversion | Already used in embed_texts() |
| tqdm | latest | Progress bar for multi-URL runs | Already in requirements-dev.txt; optional |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| trafilatura fetch_url + extract | beautifulsoup4 + requests | BS4 requires manual nav/footer/ad stripping; trafilatura's heuristics do this automatically and are already the project choice |
| SHA-256 content hash for dedup | New UNIQUE constraint on (source, url_slug) | Hash covers identical content regardless of URL; protects against re-scraping same content at different URLs |
| Google Sheets CSV export URL | google-api-python-client | CSV export needs no OAuth and no new library; PROJECT.md specifies "no Google API client" |

**Installation** (no new production deps needed — trafilatura is already in requirements-dev.txt):
```bash
# No new installs required — all libraries already present
# trafilatura==2.0.0 is in requirements-dev.txt (scripts only, never deployed)
```

---

## Architecture Patterns

### Recommended Project Structure

New files for this phase:
```
scripts/
└── embed_resources.py       # CLI embed script (KB-01, KB-02, KB-03)

routes/
└── admin.py                 # Add knowledge admin routes (KB-04, KB-05, KB-06)

templates/admin/
└── knowledge.html           # Source list with re-embed/remove controls

migrations/
└── 013_add_content_hash.sql # Add content_hash column to whatsapp_chunk

tests/
└── test_embed_resources.py  # Unit tests for new script functions
```

### Pattern 1: Google Sheets CSV Export (No Google API)

**What:** Download Google Sheets as CSV using the `/export?format=csv` URL, parse with stdlib `csv`.
**When to use:** Always — the spreadsheet is public; no OAuth or API key needed.

The resources spreadsheet URL from PROJECT.md:
`https://docs.google.com/spreadsheets/d/1UHgJyigNRnOG6J4pZe7LL5mpzxNbipSkTZ-TYs-O3WU/edit?gid=856968589#gid=856968589`

Converted to CSV export URL: `https://docs.google.com/spreadsheets/d/1UHgJyigNRnOG6J4pZe7LL5mpzxNbipSkTZ-TYs-O3WU/export?format=csv&gid=856968589`

**Example (Source: update_rusa_events.py pattern + STACK.md research):**
```python
# Source: scripts/update_rusa_events.py (SFR_SHEET_URL pattern)
import csv, io, requests

RESOURCES_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1UHgJyigNRnOG6J4pZe7LL5mpzxNbipSkTZ-TYs-O3WU"
    "/export?format=csv&gid=856968589"
)

def fetch_sheet_urls(sheet_url: str) -> list[str]:
    """Download Google Sheet as CSV, return all non-empty URL-column values."""
    response = requests.get(sheet_url, timeout=30)
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.text))
    urls = []
    for row in reader:
        url = row.get("URL") or row.get("Link") or row.get("url") or ""
        url = url.strip()
        if url.startswith("http"):
            urls.append(url)
    return urls
```

**Note:** Column name in the actual sheet is unknown until runtime. Script should accept a `--url-column` argument or probe the first row.

### Pattern 2: trafilatura Content Extraction

**What:** Single-URL fetch + main-body extraction in two calls.
**When to use:** For every URL in the resources list.

```python
# Source: trafilatura 2.0.0 docs (https://trafilatura.readthedocs.io/en/latest/usage-python.html)
import trafilatura

def extract_url_content(url: str) -> str | None:
    """Fetch URL and extract main body text. Returns None if extraction fails."""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return None
    text = trafilatura.extract(downloaded, include_comments=False, include_tables=True)
    return text  # None if extraction yielded no useful content
```

`trafilatura.fetch_url()` returns the raw HTML string (or None on fetch failure).
`trafilatura.extract()` returns the clean main-body text (or None if content is too short/low-quality).

### Pattern 3: Source Naming Convention

**What:** Every web chunk's `source` column gets a `web_` prefix derived from the URL's hostname.

```python
from urllib.parse import urlparse

def url_to_source_name(url: str) -> str:
    """Convert URL to source name with web_ prefix.

    'https://randonneuring.org/guide' -> 'web_randonneuring.org'
    'https://www.rusa.org/resources/rules' -> 'web_rusa.org'
    """
    hostname = urlparse(url).hostname or url
    # Strip www. prefix for cleaner names
    hostname = hostname.removeprefix("www.")
    return f"web_{hostname}"
```

### Pattern 4: SHA-256 Deduplication

**What:** Hash extracted text content; skip embedding if hash already exists for this source.
**When to use:** On every chunk before calling OpenAI — prevents double-embedding on re-runs.

```python
import hashlib

def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def chunk_already_exists(conn, source: str, chash: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM whatsapp_chunk WHERE source = %s AND content_hash = %s LIMIT 1",
        (source, chash)
    )
    return cur.fetchone() is not None
```

This requires adding a `content_hash TEXT` column to `whatsapp_chunk` (nullable, to avoid breaking existing WhatsApp rows).

### Pattern 5: Text Chunking for Web Content

**What:** Split long extracted text into chunks before embedding (same 24000-char cap as WhatsApp pipeline).
**When to use:** Any extracted text over ~24000 characters.

Web content chunking differs from WhatsApp: there are no timestamps or senders. Use sentence/paragraph boundary splitting. A simple approach: split on double newlines (`\n\n`), then group until reaching 2000-char soft limit per chunk. This aligns with text-embedding-3-small's ~8191 token window.

```python
MAX_CHUNK_CHARS = 24000  # From import_whatsapp.py — consistent cap
SOFT_CHUNK_CHARS = 2000  # Target chunk size for web content

def chunk_text(text: str, soft_limit: int = SOFT_CHUNK_CHARS) -> list[str]:
    """Split text into chunks at paragraph boundaries."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, current = [], []
    current_len = 0
    for para in paragraphs:
        if current_len + len(para) > soft_limit and current:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(para)
        current_len += len(para)
    if current:
        chunks.append("\n\n".join(current))
    return chunks
```

### Pattern 6: Admin Route Pattern (KB-04, KB-05, KB-06)

**What:** Admin route queries `whatsapp_chunk` grouped by `source` to show per-source stats.
**When to use:** GET `/admin/knowledge` for the list view.

```python
# Source: existing admin route patterns in routes/admin.py
@admin_bp.route('/knowledge')
@user_login_required
def knowledge():
    _require_admin()
    # Query per-source stats from whatsapp_chunk
    # Returns: source, count, min(created_at) as first_embedded, max(created_at) as last_embedded
    sources = get_knowledge_sources()
    return render_template('admin/knowledge.html', sources=sources)
```

Model function (in `models.py`):
```python
def get_knowledge_sources():
    """Return web_* sources with chunk count and embed date from whatsapp_chunk."""
    return _execute("""
        SELECT source,
               COUNT(*) AS chunk_count,
               MIN(created_at) AS first_embedded,
               MAX(created_at) AS last_embedded
        FROM whatsapp_chunk
        WHERE source LIKE 'web_%'
        GROUP BY source
        ORDER BY last_embedded DESC
    """).fetchall()

def delete_knowledge_source(source: str) -> int:
    """Delete all chunks for a web_* source. Returns deleted count."""
    cur = _execute("DELETE FROM whatsapp_chunk WHERE source = %s RETURNING id", (source,))
    return len(cur.fetchall())
```

### Anti-Patterns to Avoid

- **Using a timestamp-based UNIQUE key for web chunks:** The existing UNIQUE constraint `(source, chunk_start, chunk_end)` uses meaningful timestamps for WhatsApp. Web chunks have no timestamps — using `NOW()` at embed time would make every re-run appear unique. Use `content_hash` dedup instead.
- **Parallel HTTP fetches against external sites:** Single-threaded with 1-2 second sleep between fetches. No `asyncio` or `concurrent.futures` for the external requests.
- **Deploying trafilatura to Vercel:** trafilatura is a heavy library — it belongs in `requirements-dev.txt` only, running exclusively as a local CLI script. Vercel already bans it implicitly via bundle size.
- **Storing the full URL in `source`:** Source must be `web_{hostname}` (not the full URL). Multiple pages from the same domain share a source prefix, enabling domain-level remove/re-embed operations.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTML content extraction | Custom BS4 nav/footer stripping | trafilatura.extract() | Trafilatura's heuristics handle pagination, sidebars, cookie banners, ads — hand-rolled strippers miss new patterns |
| Embedding batching | Loop over one-at-a-time API calls | embed_texts() from import_whatsapp.py | Already battle-tested; handles batching, truncation, progress printing |
| Google Sheets reading | google-api-python-client OAuth flow | requests + csv on export URL | Export URL works for public sheets with zero auth complexity; already the project pattern |
| robots.txt compliance | Custom robots.txt checker | trafilatura respects it by default | trafilatura.fetch_url() honours robots.txt automatically in fetch mode |

**Key insight:** The entire embedding pipeline (`embed_texts()`, `bulk_insert_chunks()`, DB connection resolution) already exists in `scripts/import_whatsapp.py`. The new `embed_resources.py` script imports and reuses these functions directly — it is not a parallel implementation.

---

## Common Pitfalls

### Pitfall 1: whatsapp_chunk NOT NULL Constraints on chunk_start/chunk_end

**What goes wrong:** Web chunks have no meaningful timestamps. Attempting to insert without `chunk_start`/`chunk_end` fails on NOT NULL constraints.
**Why it happens:** The table was designed for WhatsApp messages with real timestamps.
**How to avoid:** Use the embed timestamp as a sentinel — `chunk_start = chunk_end = NOW()` at insertion time. The HNSW similarity index is timestamp-agnostic; RAG retrieval already works correctly with these sentinel values (existing `ORDER BY embedding <=> ..., chunk_start DESC` tiebreaker is harmless).
**Warning signs:** `psycopg2.errors.NotNullViolation` on insert.

### Pitfall 2: Existing UNIQUE Constraint Blocks Web Chunks or Causes Collisions

**What goes wrong:** `UNIQUE (source, chunk_start, chunk_end)` was designed for WhatsApp; if two web chunks from the same source happen to be inserted at the same second, a collision occurs.
**Why it happens:** NOW() is the same for bulk inserts.
**How to avoid:** Use `ON CONFLICT DO NOTHING` (already the pattern) AND use SHA-256 pre-check to skip already-embedded content. Alternatively: set chunk_start to a hash-derived sentinel timestamp (e.g., use epoch seconds from the URL hash). Simplest: add `content_hash` column, skip insert if hash exists before reaching the INSERT.

### Pitfall 3: trafilatura Returns None for Some Pages

**What goes wrong:** `trafilatura.extract()` returns `None` when the extracted content is too short, too repetitive, or all boilerplate. The script crashes on `None.split()` or similar.
**Why it happens:** Some cycling resource pages are heavily JavaScript-driven, or have minimal text content.
**How to avoid:** Quality gate — skip any URL where `extract()` returns None OR where `len(text) < 200`. Log skipped URLs clearly.

### Pitfall 4: Source Column Collision with Existing WhatsApp Sources

**What goes wrong:** A new web source named `web_fresh_start` or similar accidentally matches an existing WhatsApp source query.
**Why it happens:** The `web_` prefix is the only separator.
**How to avoid:** All existing WhatsApp sources (`fresh_start`, `brevets`) have no `web_` prefix. The admin knowledge page filters `WHERE source LIKE 'web_%'`. This is safe as long as the naming convention is enforced in the script.

### Pitfall 5: Google Sheets Column Name Is Unknown

**What goes wrong:** The actual column header in the resources spreadsheet may not be "URL" or "Link". Parsing fails silently.
**Why it happens:** We cannot inspect the sheet until runtime.
**How to avoid:** Script accepts `--url-column` argument with a fallback probe: print all column names found and warn if none match known URL patterns. Alternatively, detect by content: any column whose values start with `http` is the URL column.

### Pitfall 6: Re-embed Trigger Runs in Flask Request Handler

**What goes wrong:** Embedding 20+ URLs inside a Flask request handler exceeds Vercel's 60-second timeout (Hobby tier) or 300-second timeout (Pro tier). Request times out mid-operation.
**Why it happens:** The re-embed operation is I/O-heavy — fetching URLs + OpenAI API calls.
**How to avoid:** For Phase 12, the admin re-embed trigger should invoke the script as a **subprocess** OR store a "re-embed pending" flag and require the admin to run `scripts/embed_resources.py --source web_foo.com` manually. The cleanest Vercel-compatible pattern: the Flask route only deletes existing chunks and returns a message telling the admin to re-run the script. The script handles the actual embedding. This matches the existing pattern where all heavy CLI scripts run locally.

---

## Code Examples

### Complete embed_resources.py Pipeline Skeleton

```python
# Source: patterns from scripts/import_whatsapp.py
#!/usr/bin/env python3
"""Embed external resource URLs into pgvector knowledge base.

Usage:
    DATABASE_URL=... OPENAI_API_KEY=... python scripts/embed_resources.py
    python scripts/embed_resources.py --dry-run
    python scripts/embed_resources.py --source web_rusa.org --url https://rusa.org/rules
"""
import argparse, csv, hashlib, io, os, sys, time
from pathlib import Path
from urllib.parse import urlparse

import requests
import trafilatura

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from scripts.import_whatsapp import embed_texts, resolve_database_url

RESOURCES_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1UHgJyigNRnOG6J4pZe7LL5mpzxNbipSkTZ-TYs-O3WU"
    "/export?format=csv&gid=856968589"
)
MIN_CONTENT_CHARS = 200
SOFT_CHUNK_CHARS = 2000
FETCH_DELAY_SECONDS = 1.5


def url_to_source_name(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    return f"web_{hostname.removeprefix('www.')}"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

### Migration 013: Add content_hash to whatsapp_chunk

```sql
-- migrations/013_add_content_hash.sql
-- Add nullable content_hash for web chunk deduplication
-- Existing WhatsApp chunks are unaffected (NULL hash value)

ALTER TABLE whatsapp_chunk
    ADD COLUMN IF NOT EXISTS content_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_whatsapp_chunk_content_hash
    ON whatsapp_chunk(content_hash)
    WHERE content_hash IS NOT NULL;
```

### Admin Route: Knowledge Source List

```python
# In routes/admin.py
@admin_bp.route('/knowledge')
@user_login_required
def knowledge():
    _require_admin()
    from models import get_knowledge_sources
    sources = get_knowledge_sources()
    return render_template('admin/knowledge.html', sources=sources)

@admin_bp.route('/knowledge/<path:source>/remove', methods=['POST'])
@user_login_required
def knowledge_remove(source):
    _require_admin()
    if not source.startswith('web_'):
        flash('Can only remove web_ sources.', 'error')
        return redirect(url_for('admin.knowledge'))
    from models import delete_knowledge_source
    count = delete_knowledge_source(source)
    flash(f'Removed {count} chunks from {source}.', 'success')
    return redirect(url_for('admin.knowledge'))
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| WhatsApp-only knowledge base | Web URL knowledge base also supported | Phase 12 | RAG now retrieves from both WhatsApp conversations and crawled external cycling resources |
| Manual content curation | Admin-driven URL crawl from spreadsheet | Phase 12 | Admin can add new sources by adding URLs to spreadsheet + running script |
| Timestamp-based dedup (WhatsApp) | Content hash dedup (web chunks) | Phase 12 | Idempotent re-runs without double-embedding |

---

## Open Questions

1. **Google Sheets column name for URLs**
   - What we know: Sheet ID is `1UHgJyigNRnOG6J4pZe7LL5mpzxNbipSkTZ-TYs-O3WU`, tab GID `856968589`
   - What's unclear: The exact column header ("URL", "Link", "Resource", etc.)
   - Recommendation: Script probes all columns for `http`-prefixed values; add `--url-column` CLI arg as override

2. **Re-embed trigger via Flask vs CLI-only**
   - What we know: Vercel timeout limits make in-request embedding risky for >5 URLs
   - What's unclear: How many URLs are in the resources spreadsheet (likely 20-50)
   - Recommendation: Flask route handles DELETE (fast DB operation), shows instruction to re-run script manually; this matches Vercel serverless constraint already documented in STATE.md

3. **whatsapp_chunk UNIQUE constraint for web chunks**
   - What we know: UNIQUE (source, chunk_start, chunk_end) — web chunks use NOW() as sentinel
   - What's unclear: Collision risk at millisecond granularity for bulk inserts
   - Recommendation: Use `content_hash` pre-check to skip already-embedded content; set `ON CONFLICT DO NOTHING` as fallback; add microsecond offset to chunk_start per chunk index if needed (chunk_start = NOW() + interval '1 microsecond' * i)

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.3.0 |
| Config file | pytest.ini (testpaths = tests) |
| Quick run command | `pytest tests/test_embed_resources.py -x` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| KB-01 | fetch_sheet_urls() returns list of URLs from CSV | unit | `pytest tests/test_embed_resources.py::test_fetch_sheet_urls -x` | Wave 0 |
| KB-01 | fetch_sheet_urls() handles missing URL column gracefully | unit | `pytest tests/test_embed_resources.py::test_fetch_sheet_urls_missing_column -x` | Wave 0 |
| KB-02 | extract_url_content() returns None for failed fetch | unit | `pytest tests/test_embed_resources.py::test_extract_url_content_failure -x` | Wave 0 |
| KB-02 | chunk_text() splits long content at paragraph boundaries | unit | `pytest tests/test_embed_resources.py::test_chunk_text -x` | Wave 0 |
| KB-02 | content below MIN_CONTENT_CHARS is skipped | unit | `pytest tests/test_embed_resources.py::test_quality_filter -x` | Wave 0 |
| KB-03 | url_to_source_name() produces web_ prefix | unit | `pytest tests/test_embed_resources.py::test_url_to_source_name -x` | Wave 0 |
| KB-03 | chunk_already_exists() returns True for known hash | unit | `pytest tests/test_embed_resources.py::test_chunk_already_exists -x` | Wave 0 |
| KB-04 | get_knowledge_sources() returns source/count/date rows | unit (mock DB) | `pytest tests/test_embed_resources.py::test_get_knowledge_sources -x` | Wave 0 |
| KB-05 | /admin/knowledge/{source}/re-embed returns 200 and instruction | unit (Flask test client) | `pytest tests/test_embed_resources.py::test_admin_re_embed -x` | Wave 0 |
| KB-06 | delete_knowledge_source() issues correct DELETE SQL | unit (mock DB) | `pytest tests/test_embed_resources.py::test_delete_knowledge_source -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_embed_resources.py -x`
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_embed_resources.py` — covers KB-01 through KB-06 (10 tests)
- [ ] `migrations/013_add_content_hash.sql` — content_hash column + index

---

## Sources

### Primary (HIGH confidence)
- `/scripts/import_whatsapp.py` — embed_texts(), bulk_insert_chunks(), resolve_database_url() patterns confirmed by reading source
- `/schema/whatsapp_schema.sql` — table structure, UNIQUE constraint, HNSW index confirmed by reading source
- `/scripts/update_rusa_events.py` — Google Sheets CSV export pattern confirmed (SFR_SHEET_URL at line 30)
- `/requirements-dev.txt` — trafilatura==2.0.0, pgvector==0.4.2 confirmed present
- `/requirements.txt` — requests==2.31.0 confirmed present
- [trafilatura 2.0.0 official docs](https://trafilatura.readthedocs.io/en/latest/usage-python.html) — fetch_url() + extract() API verified

### Secondary (MEDIUM confidence)
- `/.planning/research/STACK.md` (lines 304-388) — prior research decision: trafilatura for web content, CSV export for sheets, no Scrapy/Playwright
- `/routes/admin.py` — admin route patterns, _require_admin(), flash(), redirect() confirmed by reading source

### Tertiary (LOW confidence)
- Actual column names in resources Google Sheet — not verified; requires runtime inspection

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries confirmed present in codebase; trafilatura API verified against official docs
- Architecture: HIGH — existing patterns directly reusable; whatsapp_chunk table structure confirmed
- Pitfalls: HIGH — UNIQUE constraint pitfall confirmed by reading actual schema; Vercel timeout concern from STATE.md blockers section

**Research date:** 2026-03-18
**Valid until:** 2026-04-18 (trafilatura API stable; pgvector schema stable; no moving parts)
