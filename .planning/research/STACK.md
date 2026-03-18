# Technology Stack: Personality-Driven AI Coaching (Milestone 2)

**Project:** Team Asha Randonneuring — Milestone 2
**Researched:** 2026-03-17
**Scope:** New libraries only — does not re-document Flask 3.0, PostgreSQL/Supabase, OpenAI, pgvector, Tailwind, or Braintrust observability (already in `.planning/codebase/STACK.md`)

---

## Context

This milestone adds five capability domains to an existing Flask 3.0 / psycopg2 / OpenAI /
Braintrust app. Each domain requires specific library choices. The existing stack is frozen — new
libraries must slot in without requiring framework changes, background workers, or new
infrastructure.

**Hard constraints from Vercel serverless:**
- No persistent background workers (Celery, RQ, Dramatiq are ALL disqualified)
- Max function duration: 300s on Hobby plan (fluid compute), 800s on Pro
- All heavy operations (extraction, embedding, crawling) must run as admin-triggered HTTP
  requests or offline CLI scripts — not inline with user requests
- Bundle size: 500MB uncompressed limit (heavy ML libraries like PyTorch are out)

---

## Domain 1: Personality Trait Extraction from Chat Logs

### Recommended: `instructor` 1.14.5 + `pydantic` 2.12.5

**What they do:** `instructor` wraps the OpenAI client so you can pass a Pydantic model as
`response_model` and get back a validated, typed Python object instead of raw JSON. The library
handles retry on validation failure, schema generation, and streaming partial objects.

**Why this, not raw JSON mode:**

The existing codebase already uses raw `response_format={"type": "json_object"}` in
`whatsapp_parser.py` and `openai_coach.py`. That pattern works for simple outputs but becomes
brittle for nested, enumerated personality trait schemas. Instructor eliminates the manual
`json.loads()` + key normalization pattern seen in `openai_coach.py` lines 499-512 and provides
automatic retry when the model returns an invalid structure.

**Why not LangChain or LlamaIndex:**

Both are multi-hundred-MB frameworks that add abstraction layers over functionality this codebase
already has (OpenAI client, pgvector retrieval, chat loop). They would conflict with the existing
agentic loop in `services/chat_service.py` and violate the established pattern of calling OpenAI
directly. The Vercel bundle size limit makes them risky regardless.

**How it fits the existing code:**

```python
import instructor
from pydantic import BaseModel, Field
from typing import Literal
from openai import OpenAI

class PersonalityTraits(BaseModel):
    tone_register: Literal["formal", "informal", "mixed"]
    humor_type: Literal["dry", "sarcastic", "self_deprecating", "teasing", "none"]
    directness: Literal["blunt", "qualifying", "verbose"]
    encouragement_style: Literal["tough_love", "validating", "neutral", "pushes_hard"]
    signature_phrases: list[str] = Field(max_length=5)
    example_quotes: list[str] = Field(max_length=3, description="Direct quotes supporting traits")
    confidence: Literal["high", "medium", "low"]
    source_message_count: int

client = instructor.from_provider("openai/gpt-4o")  # GPT-4o, not mini, for extraction quality
traits = client.chat.completions.create(
    response_model=PersonalityTraits,
    messages=[{"role": "user", "content": f"Extract personality traits:\n\n{messages_text}"}],
)
```

**Model choice for extraction:** GPT-4o (not GPT-4o-mini). The PROJECT.md explicitly flags this:
"personality extraction may need GPT-4o for quality." Trait extraction from noisy WhatsApp data
requires stronger reasoning to distinguish consistent personality signals from one-off reactions.
GPT-4o-mini is appropriate for the admin UI interactions and guardrail evals.

**Installation:**
```bash
pip install instructor==1.14.5
```

Pydantic 2.12.5 is already pulled in transitively by the OpenAI SDK. Confirm with
`pip show pydantic` before pinning explicitly.

**Confidence:** HIGH — instructor is the standard pattern for structured LLM extraction as of
2025/2026; 3M+ monthly downloads; built on Pydantic which the OpenAI SDK already requires.

---

## Domain 2: Blog Content Extraction

### 2a. WordPress Blog: `trafilatura` 2.0.0

**What it does:** Fetches a URL, strips navigation, ads, footers, comments, and cookie banners
using a heuristic trained on millions of web pages, and returns clean main text content. Outputs
Markdown, plain text, or structured JSON. Used by HuggingFace, IBM, and Microsoft Research for
large-scale text extraction.

**Why this, not BeautifulSoup:**

BeautifulSoup (already in `requirements.txt`) can parse HTML but has no concept of "main content."
You would need to manually identify WordPress post containers (`div.entry-content`,
`article.post`, etc.) and strip sidebars, headers, related posts widgets. This is brittle across
WordPress themes and will break if Mihir changes themes. Trafilatura handles this automatically —
it's specifically trained for blog-style content.

**Why not newspaper4k (0.9.5):**

Newspaper4k is good for news article extraction but has a heavier dependency footprint (NLTK data
files, spaCy optional). Trafilatura is lighter, faster, has no data file downloads, and
produces cleaner output from long-form blog posts (which Mihir's 4000-word PBP ride report is).

**Minimal usage pattern:**

```python
import trafilatura

def extract_wordpress_post(url: str) -> str:
    html = trafilatura.fetch_url(url)
    text = trafilatura.extract(html, output_format="markdown", include_comments=False)
    return text or ""
```

**Note on WordPress.com REST API:** WordPress.com sites (like `unexpectedathlete.wordpress.com`)
do expose a public REST API at `https://public-api.wordpress.com/rest/v1.1/sites/{site}/posts`.
This returns structured JSON with post content, date, and author. However, the content field
contains raw HTML — you still need an extraction step. Trafilatura can process the HTML field
directly. The REST API approach is more reliable than URL-based fetching (no rate limits from the
host) but requires knowing the site slug. For a one-time extraction of a handful of posts,
URL-based trafilatura is simpler and sufficient.

**Installation:**
```bash
pip install trafilatura==2.0.0
```

**Confidence:** HIGH — current version verified via PyPI JSON API; widely adopted in academic
text extraction pipelines.

---

### 2b. Google Drive PDF: `pdfplumber` 0.11.4+

**What it does:** Extracts text from PDF files with precise character-level positioning. Better
than pypdf at handling complex layouts, multi-column text, and tables. Returns plain text
preserving reading order.

**Why pdfplumber, not pypdf 6.9.1:**

pypdf is fast and handles simple PDFs well, but it struggles with PDFs that have non-standard
character encoding, mixed fonts, or complex layout (all common in Google Docs exports to PDF).
pdfplumber uses pdfminer.six under the hood and gives character-level control. For Venki's
Google Drive PDF — which is almost certainly a narrative blog post exported from Google Docs —
pdfplumber is more reliable.

**Why not PyMuPDF (fitz):**

PyMuPDF is fast and handles scanned PDFs with OCR (via Tesseract), but it requires a compiled C
extension and a larger Vercel bundle. For a text-based Google Docs PDF (no scanned images),
pdfplumber is sufficient and leaner.

**Google Drive PDF download pattern:**

Google Drive public links require URL transformation to get the download URL. The file ID can be
extracted from the share link:

```python
import re
import requests
import pdfplumber
import io

def download_google_drive_pdf(share_url: str) -> bytes:
    """Convert Google Drive share URL to direct download."""
    match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', share_url)
    if not match:
        raise ValueError("Not a valid Google Drive file URL")
    file_id = match.group(1)
    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    response = requests.get(download_url, timeout=30)
    response.raise_for_status()
    return response.content

def extract_pdf_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    return "\n\n".join(pages)
```

**Note on Google Drive authentication:** If Venki's PDF is publicly shared ("Anyone with the
link can view"), no authentication is needed. The `requests` library (already in
`requirements.txt`) handles the download. If the file requires authentication (shared only with
specific users), the `google-api-python-client` 2.193.0 + `google-auth` 2.49.1 approach is
needed — but this introduces OAuth credentials management complexity. Confirm with Venki that the
file is publicly accessible before building authentication support.

**Installation:**
```bash
pip install pdfplumber==0.11.9
```

**Confidence:** MEDIUM — pdfplumber version verified via PyPI JSON API. Google Drive download
URL pattern is stable but Google occasionally changes it; verify against the actual file before
finalizing the implementation.

---

## Domain 3: Structured Personality Profile Storage and Admin Interfaces

### Storage: Raw psycopg2 (already in stack)

**What to use:** The existing pattern in `models.py` — raw SQL via psycopg2 with
`RealDictCursor`. No new ORM needed. Personality profiles are a new set of tables following the
same pattern as `whatsapp_chunk` and existing rider tables.

**Why not SQLAlchemy 2.0.48:**

SQLAlchemy would require rewriting all existing queries (currently raw psycopg2) or maintaining
two database access patterns. The existing codebase has made a deliberate choice to use raw SQL,
and the personality profile tables are simple enough that ORM benefits don't justify the
introduction of a second database pattern.

**Why not Flask-Admin 2.0.2:**

Flask-Admin generates generic CRUD UIs from SQLAlchemy models. It requires SQLAlchemy (ruled out
above) and produces Bootstrap-based UI that would clash with the existing Tailwind CSS design
system. The existing admin panel (`routes/admin.py`) uses Jinja2 templates + Tailwind, which
gives full control over layout and UX. Custom admin pages following this established pattern are
the right choice — and the admin views for personality traits have specific UX requirements
(source quote display, confidence badges, per-field save) that Flask-Admin can't express without
extensive customization.

**Admin UI: Existing pattern (Flask blueprints + Jinja2 + Tailwind)**

Add a new `admin_personality` blueprint following the pattern in `routes/admin.py`. All new
admin pages (personality traits, gear preferences, coach assignment, guardrails) are standard
Flask route handlers rendering Jinja2 templates with Tailwind styling.

**For per-field AJAX saves** (recommended in FEATURES.md to avoid full-page reloads):

Use `fetch()` in vanilla JavaScript — no new JS library needed. The existing codebase has no
frontend framework (React, Vue, etc.) and the admin is a single user (Mihir). Vanilla `fetch`
against a Flask endpoint that accepts JSON and returns `{"success": true}` is sufficient and
consistent with the existing approach.

**No new libraries required for this domain.** The existing psycopg2 + Flask + Jinja2 + Tailwind
stack handles it completely.

**Confidence:** HIGH — this is a deliberate non-recommendation based on direct analysis of the
existing codebase patterns.

---

## Domain 4: Coaching Guardrail Configuration and Eval Validation

### Guardrail storage: Raw psycopg2 (existing pattern)

Same rationale as Domain 3. Guardrails are stored as rows in a `coaching_guardrails` table,
loaded at conversation start via `models.py`, and injected into the system prompt. No new library.

### Eval validation: `braintrust` 0.9.0 + `autoevals` 0.0.130 (already in dev dependencies)

**The existing eval pattern is the right pattern.** `evals/eval_guardrail.py` already shows the
correct approach: define a `task` function, a custom scorer, and call `Eval()` from the
`braintrust` package. The existing `Eval` + `init_dataset` pattern from `braintrust` 0.9.0 is
what all new guardrail evals should follow.

**For guardrail compliance evals specifically**, use `autoevals` LLM-as-judge:

```python
from autoevals import LLMClassifier

guardrail_judge = LLMClassifier(
    name="guardrail_compliance",
    prompt_template="""
You are evaluating whether a coaching response respects these guardrails:
{guardrails}

Message: {input}
Response: {output}

Did the response comply with all active guardrails? Answer YES or NO with one sentence reason.
""",
    choice_scores={"YES": 1, "NO": 0},
)
```

**Why autoevals for guardrail evals, not a custom scorer:**

Custom scorers (as in `eval_guardrail.py`) are appropriate when you can determine correctness
programmatically (e.g., checking `intent == "off_topic"`). Guardrail compliance — "did the
response respect the tone guardrail for a rider with humor_sensitivity=low?" — requires semantic
understanding. `autoevals.LLMClassifier` is the right tool for semantic yes/no judgments. The
`autoevals` package is already in `requirements-dev.txt` (unversioned — pin to `0.0.130`).

**No new libraries required for this domain.** Both packages are already present.

**Confidence:** HIGH — direct analysis of existing eval files; autoevals version verified via
PyPI JSON API.

---

## Domain 5: Web Scraping and Embedding External Resource Links

### Crawling: `trafilatura` 2.0.0 (same as Domain 2)

Trafilatura handles both single-URL extraction and crawling of site URLs via its `sitemaps` and
`feeds` discovery features. For the resources spreadsheet use case — a list of URLs to fetch,
extract, and embed — trafilatura's batch URL processing is sufficient.

**Important: robots.txt compliance.** Trafilatura respects `robots.txt` by default in its
crawling mode. For the admin-triggered one-time embedding of external resources, call
`trafilatura.fetch_url()` per URL with a reasonable delay between requests (1-2 seconds). Do
not implement parallel crawling against external sites.

### Spreadsheet URL extraction: Google Sheets API via `google-api-python-client` 2.193.0

The resources spreadsheet URL in PROJECT.md points to a Google Sheets document. The simplest
extraction path:

1. **If the sheet is publicly readable:** Use the Sheets API with an API key (no OAuth needed).
   `google-api-python-client` 2.193.0 + `google-auth` 2.49.1 handles this.

2. **Even simpler:** Export the sheet as CSV via the Google Sheets export URL
   (`/export?format=csv&gid={sheet_id}`) and parse with Python's built-in `csv` module. No
   Google API client needed for public sheets.

**Recommended approach (simpler):**

```python
import csv
import io
import requests

def fetch_sheet_urls(spreadsheet_id: str, gid: str) -> list[str]:
    export_url = (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        f"/export?format=csv&gid={gid}"
    )
    response = requests.get(export_url, timeout=30)
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.text))
    # Extract URL column — column name TBD from actual sheet structure
    return [row.get("URL") or row.get("Link") or "" for row in reader if row]
```

This requires no additional libraries — `requests` is already in `requirements.txt`.

### Embedding: Existing OpenAI `text-embedding-3-small` pattern

The `scripts/import_whatsapp.py` pipeline already has the correct embedding pattern:
`embed_texts()` calls `client.embeddings.create()` in batches of 100, returning numpy arrays
for bulk insert into pgvector. New resource embeddings should use the identical pattern.

**Store in a new `knowledge_source` table** with columns: `url`, `source_type` (community |
authoritative), `last_crawled`, `chunk_count`. Link chunks to this table via `source_url` FK.
This enables the admin UI controls described in FEATURES.md (list, re-embed, remove per source).

### HTML parsing for content filtering: `beautifulsoup4` 4.14.3 + `lxml` 6.0.2

Both are already in `requirements.txt` (older versions). For external resource crawling, upgrade
to current versions to get bug fixes — the existing scraping in the codebase (RUSA results in
`services/rusa.py`) uses the same libraries and the upgrade should be non-breaking.

**Why not Scrapy 2.14.2:**

Scrapy is a full crawling framework designed for persistent workers that traverse entire sites.
The use case here is fetching a bounded list of URLs from an admin-provided spreadsheet — not
open-ended spidering. Scrapy's overhead (project structure, settings, pipelines, middleware) is
disproportionate. The `trafilatura.fetch_url()` + `beautifulsoup4` approach is already in the
codebase and handles this use case in a dozen lines.

**Why not Playwright 1.58.0:**

JavaScript-rendered pages are not expected in the target resource set (RUSA rules pages,
randonneuring guides, equipment review sites). Playwright requires a Chromium binary which would
blow past Vercel's 500MB bundle limit. If a specific target URL requires JS rendering, it's an
exception — fetch it manually, not in the automated pipeline.

**Installation (upgrades only — not new dependencies):**
```bash
pip install trafilatura==2.0.0
pip install beautifulsoup4==4.14.3 lxml==6.0.2  # upgrades from existing older versions
pip install pdfplumber==0.11.9  # new
pip install instructor==1.14.5  # new
```

---

## Full Additions to requirements.txt

```
# Milestone 2 additions
trafilatura==2.0.0      # Blog and web content extraction (Domain 2, 5)
pdfplumber==0.11.9      # Google Drive PDF extraction (Domain 2)
instructor==1.14.5      # Structured LLM output extraction (Domain 1)

# Version upgrades (existing libs — verify non-breaking before deploying)
beautifulsoup4==4.14.3  # was 4.12.3
lxml==6.0.2             # was 5.1.0
openai==2.29.0          # was 2.24.0 — needed for instructor compatibility
```

Dev dependencies (`requirements-dev.txt`) — pin existing unversioned entry:
```
autoevals==0.0.130      # was unversioned
```

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Structured LLM extraction | instructor 1.14.5 | Raw JSON mode (existing) | Brittle for nested schemas; no retry on malformed output |
| Structured LLM extraction | instructor 1.14.5 | LangChain | 200MB+ framework; conflicts with existing chat loop; overkill |
| WordPress extraction | trafilatura 2.0.0 | BeautifulSoup (existing) | No concept of "main content"; breaks across themes |
| WordPress extraction | trafilatura 2.0.0 | newspaper4k 0.9.5 | Heavier deps (NLTK); worse on long-form posts vs. news articles |
| PDF extraction | pdfplumber 0.11.9 | pypdf 6.9.1 | Less reliable on Google Docs PDF exports; layout issues |
| PDF extraction | pdfplumber 0.11.9 | PyMuPDF | C extension; larger Vercel bundle; OCR not needed |
| Admin CRUD UI | Custom Flask + Jinja2 | Flask-Admin 2.0.2 | Requires SQLAlchemy; Bootstrap conflicts with Tailwind; can't express custom UX requirements |
| Admin CRUD UI | Custom Flask + Jinja2 | SQLAlchemy 2.0.48 | Would require rewriting all existing raw psycopg2 queries |
| Web crawling | trafilatura 2.0.0 | Scrapy 2.14.2 | Full framework for open-ended spidering; bounded URL list doesn't need it |
| JS rendering | Not needed | Playwright 1.58.0 | Chromium binary exceeds Vercel 500MB bundle limit |
| Background jobs | Admin-triggered HTTP | Celery 5.6.2 | Requires persistent workers; incompatible with Vercel serverless |
| Background jobs | Admin-triggered HTTP | RQ 2.7.0 | Requires Redis + persistent workers; same Vercel incompatibility |
| Eval scoring | autoevals LLMClassifier | Custom scorer | Semantic compliance checks require LLM judgment; custom scorers are for programmatic checks |

---

## Architecture Notes

**Where new code lives:**

| Component | Location | Pattern |
|-----------|----------|---------|
| Personality extraction script | `scripts/extract_personality.py` | CLI script, same pattern as `scripts/import_whatsapp.py` |
| Blog/PDF extraction | `scripts/extract_blog.py` | CLI script |
| Personality models (DB) | `models.py` | New functions, same raw SQL pattern |
| Guardrail loader | `services/openai_coach.py` | Replaces hardcoded strings with DB-loaded rules |
| Admin routes | `routes/admin_personality.py` | New blueprint, registered in `app.py` |
| Guardrail eval | `evals/eval_guardrails_v2.py` | Follows `eval_guardrail.py` pattern + autoevals LLM judge |
| Resource embedding script | `scripts/import_resources.py` | CLI script, extends `import_whatsapp.py` pattern |

**Execution model for heavy operations:**

All extraction, crawling, and embedding operations run as:
1. CLI scripts (preferred for one-time runs: `python scripts/extract_personality.py`)
2. Admin-triggered HTTP endpoints that stream progress as SSE (for in-browser triggering)

Never inline these operations into request handlers that serve user-facing pages.

---

## Vercel Deployment Constraints Summary

| Concern | Limit | Impact |
|---------|-------|--------|
| Function duration (Hobby) | 300s max (fluid compute) | Extraction jobs must complete under 5 min or run as CLI scripts |
| Bundle size | 500MB uncompressed | Playwright, PyMuPDF, PyTorch are out; all recommended libs are safe |
| Persistent processes | None | Celery, RQ, any background worker pattern is incompatible |
| Stateful file system | None | Extracted data must go to DB immediately; can't write temp files |

**On the 300s limit for extraction:** Personality extraction from a full WhatsApp export (hundreds
of messages to an LLM) may approach this limit if triggered via HTTP. The CLI script path
(`python scripts/extract_personality.py`) is the safe fallback — it runs outside Vercel with no
time limit. The admin HTTP trigger is a convenience for short re-extractions.

---

## Sources

| Source | Confidence | Used For |
|--------|------------|----------|
| PyPI JSON API (`/pypi/{package}/json`) | HIGH | All version numbers |
| GitHub instructor README (instructor-ai/instructor) | HIGH | instructor usage pattern |
| Vercel docs (`/docs/functions/runtimes/python`, `/docs/functions/configuring-functions/duration`) | HIGH | Serverless constraints |
| Direct codebase analysis (`scripts/import_whatsapp.py`, `evals/eval_guardrail.py`, `routes/admin.py`, `services/openai_coach.py`) | HIGH | Existing patterns to follow |
| PROJECT.md | HIGH | Requirements and constraints |
| `.planning/codebase/STACK.md` | HIGH | Existing stack versions |
| trafilatura readthedocs (denied) | MEDIUM (from PyPI description + GitHub README) | trafilatura capabilities |
| LangChain/SQLAlchemy bundle sizes | MEDIUM | Rationale for exclusion — verify before dismissing if needs change |
