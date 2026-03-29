#!/usr/bin/env python3
"""Extract personality traits from blog posts or PDF documents.

Fetches text from a WordPress URL (via trafilatura) or a local PDF
(via pdfplumber), then calls GPT-4o structured output to extract
personality traits. Results are stored in the personality_profile table.

Usage:
    # Blog URL extraction (dry-run)
    python scripts/extract_personality_blog.py \
        --url https://venki.example.com/brevet-report \
        --rider-name Venki \
        --dry-run

    # PDF extraction (dry-run)
    python scripts/extract_personality_blog.py \
        --pdf-path data/venki_blog.pdf \
        --rider-name Venki \
        --dry-run

    # Full extraction — stores to DB
    python scripts/extract_personality_blog.py \
        --url https://venki.example.com/brevet-report \
        --rider-name Venki

Required environment variables:
    OPENAI_API_KEY   — GPT-4o API key
    DATABASE_URL     — PostgreSQL connection string (not needed for --dry-run)
"""

import argparse
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path for scripts.* imports
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv

load_dotenv()

try:
    import trafilatura
except ImportError:
    # Create a placeholder module with stub attributes so unittest.mock.patch
    # can patch them in tests even when the library is not installed.
    from types import ModuleType as _ModuleType

    def _trafilatura_not_installed(*args, **kwargs):
        raise ImportError("trafilatura is required: pip install trafilatura==2.0.0")

    _trafilatura_mod = _ModuleType('trafilatura')
    _trafilatura_mod.fetch_url = _trafilatura_not_installed  # type: ignore
    _trafilatura_mod.extract = _trafilatura_not_installed  # type: ignore
    trafilatura = _trafilatura_mod  # type: ignore

try:
    import pdfplumber
except ImportError:
    # Create a placeholder module with stub attributes so unittest.mock.patch
    # can patch pdfplumber.open in tests even when the library is not installed.
    from types import ModuleType as _ModuleType

    def _pdfplumber_not_installed(*args, **kwargs):
        raise ImportError("pdfplumber is required: pip install pdfplumber==0.11.9")

    _pdfplumber_mod = _ModuleType('pdfplumber')
    _pdfplumber_mod.open = _pdfplumber_not_installed  # type: ignore
    pdfplumber = _pdfplumber_mod  # type: ignore

from scripts.personality_helpers import (
    PersonalityExtraction,
    compute_confidence,
    store_evidence,
    store_extraction_results,
)


# ---------------------------------------------------------------------------
# Text extraction functions
# ---------------------------------------------------------------------------


def fetch_blog_text(url: str) -> str:
    """Fetch and extract article text from a blog URL using trafilatura.

    Args:
        url: Full URL to a blog post or article page.

    Returns:
        Extracted plain text content of the article.

    Raises:
        ValueError: If the URL cannot be fetched or no text is extractable.
    """
    downloaded = trafilatura.fetch_url(url)
    if downloaded is None:
        raise ValueError(f"Failed to fetch: {url}")
    text = trafilatura.extract(downloaded)
    if not text:
        raise ValueError(f"No extractable text from: {url}")
    return text


def extract_pdf_text(pdf_path: str) -> str:
    """Extract text from a local PDF file using pdfplumber.

    Args:
        pdf_path: Local filesystem path to a PDF file.

    Returns:
        Concatenated plain text of all pages.

    Raises:
        ValueError: If no text can be extracted (e.g. scanned PDF).
    """
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


# ---------------------------------------------------------------------------
# Blog-specific extraction prompt
# ---------------------------------------------------------------------------

BLOG_EXTRACTION_PROMPT = """\
You are a personality analyst. Given a blog post or article written by one person, \
classify their communication style using ONLY the information visible in their writing. \
Do not infer traits that aren't evidenced in the text.

For each trait field, you MUST also provide 3-5 verbatim quotes from the text that \
justify your classification. Quotes must be exact text from the provided content, \
under 200 characters each.

Classification rules:
- tone: how they come across emotionally (direct=gets to point fast, warm=nurturing, \
  playful=jokes/puns, serious=formal, sarcastic=ironic)
- humor_type: the kind of humor they use (none, dry, sarcastic, gentle, self-deprecating)
- directness: how quickly they get to the point (low=lots of context, medium=balanced, high=blunt)
- response_length_tendency: typical paragraph length (brief=short sentences, moderate=paragraph, verbose=multi-paragraph)
- question_asking_behavior: how often they pose questions (rarely, sometimes, frequently)
- signature_phrases: recurring phrases, expressions, or words unique to this person (up to 5)
- domain_bias: the topic area they write about most (one short phrase, e.g. "randonneuring")
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Extract personality traits from blog posts or PDF documents.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--url',
        help='WordPress or blog URL to extract from (uses trafilatura)',
    )
    parser.add_argument(
        '--pdf-path',
        help='Path to a local PDF file (uses pdfplumber)',
    )
    parser.add_argument(
        '--rider-name',
        required=True,
        help='Rider name to look up in DB (case-insensitive prefix)',
    )
    parser.add_argument(
        '--profile-type',
        default='coach',
        choices=['coach', 'rider'],
        help="Profile type to upsert: 'coach' or 'rider' (default: coach)",
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Output extracted JSON to stdout; do not write to DB',
    )
    args = parser.parse_args()

    # --- Validate: exactly one source ---
    if args.url and args.pdf_path:
        print("Error: Provide exactly one of --url or --pdf-path, not both.")
        sys.exit(1)
    if not args.url and not args.pdf_path:
        print("Error: Provide exactly one of --url or --pdf-path.")
        sys.exit(1)

    # --- Validate API key ---
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        print("Error: OPENAI_API_KEY not set. Export it or add to .env file.")
        sys.exit(1)

    # --- Validate DB URL early if not dry-run ---
    database_url = os.environ.get('DATABASE_URL')
    if not args.dry_run and not database_url:
        print("Error: DATABASE_URL not set (required unless --dry-run).")
        sys.exit(1)

    # --- Google Drive warning ---
    if args.url and args.url.startswith('https://drive.google.com'):
        print(
            "Warning: Google Drive URLs may require authentication. "
            "If extraction fails, download the PDF manually and use --pdf-path instead."
        )

    # --- Extract text ---
    try:
        if args.url:
            print(f"Fetching blog text from: {args.url}")
            text = fetch_blog_text(args.url)
            source_label = f"URL: {args.url}"
        else:
            print(f"Extracting text from PDF: {args.pdf_path}")
            text = extract_pdf_text(args.pdf_path)
            source_label = f"PDF: {args.pdf_path}"
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    word_count = len(text.split())
    # Estimate message-equivalent count (~50 words per message)
    message_equiv = max(1, word_count // 50)
    confidence = compute_confidence(message_equiv)

    print(f"  Source: {source_label}")
    print(f"  Word count: {word_count}")
    print(f"  Message equivalent: {message_equiv}")
    print(f"  Confidence: {confidence}")

    # --- Call GPT-4o for extraction ---
    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    print("  Sending text to GPT-4o for personality extraction...")
    try:
        response = client.chat.completions.parse(
            model='gpt-4o',
            messages=[
                {'role': 'system', 'content': BLOG_EXTRACTION_PROMPT},
                {'role': 'user', 'content': f"Analyze this blog post by {args.rider_name}:\n\n{text}"},
            ],
            response_format=PersonalityExtraction,
            temperature=0,
        )
        extraction = response.choices[0].message.parsed
    except Exception as e:
        print(f"Error: OpenAI API call failed — {e}")
        sys.exit(1)

    # --- Dry-run: print JSON and exit ---
    if args.dry_run:
        print("\n=== DRY-RUN RESULT ===")
        print(extraction.model_dump_json(indent=2))
        print("\n(Skipped DB write — dry-run mode)")
        return

    # --- Full extraction: store to DB ---
    import psycopg2
    import psycopg2.extras

    try:
        conn = psycopg2.connect(database_url)
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        from scripts.seed_coaching_profiles import lookup_rider
        rider_id = lookup_rider(cur, f'{args.rider_name}%')
        if rider_id is None:
            print(f"Error: Rider '{args.rider_name}' not found in DB.")
            print("  Ensure the rider exists in the rider table.")
            conn.rollback()
            conn.close()
            sys.exit(1)

        store_extraction_results(
            cur,
            rider_id=rider_id,
            extraction=extraction,
            extraction_source='blog',
            message_count=message_equiv,
            profile_type=args.profile_type,
        )

        evidence_count = store_evidence(
            cur,
            rider_id=rider_id,
            extraction=extraction,
            extraction_source='blog',
        )

        conn.commit()

    except psycopg2.Error as e:
        print(f"Error: Database error — {e}")
        if 'conn' in dir():
            conn.rollback()
            conn.close()
        sys.exit(1)
    finally:
        if 'cur' in dir() and cur:
            cur.close()
        if 'conn' in dir() and conn:
            conn.close()

    # --- Print summary ---
    print(f"\n=== EXTRACTION SUMMARY ===")
    print(f"Extracted personality for {args.rider_name}:")
    print(f"  Source: {source_label}")
    print(f"  Word count: {word_count}")
    print(f"  Confidence: {confidence}")
    print(f"  Tone: {extraction.tone}")
    print(f"  Humor: {extraction.humor_type}")
    print(f"  Directness: {extraction.directness}")
    print(f"  Encouragement style: {extraction.encouragement_style}")
    print(f"  Technical depth: {extraction.technical_depth}")
    print(f"  Domain bias: {extraction.domain_bias or '(none)'}")
    print(f"  Response length: {extraction.response_length_tendency}")
    print(f"  Question asking: {extraction.question_asking_behavior}")
    print(f"  Signature phrases: {extraction.signature_phrases}")
    print(f"  Evidence quotes stored: {evidence_count}")


if __name__ == '__main__':
    main()
