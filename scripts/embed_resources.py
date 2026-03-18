#!/usr/bin/env python3
"""Embed external resource URLs into pgvector knowledge base.

Crawls URLs from the Team Asha resources Google Sheets spreadsheet,
extracts content with trafilatura, chunks at paragraph boundaries,
embeds with text-embedding-3-small, and stores in whatsapp_chunk table
with web_ source prefixes and SHA-256 content hash deduplication.

Usage:
    DATABASE_URL=... OPENAI_API_KEY=... python scripts/embed_resources.py
    python scripts/embed_resources.py --dry-run
    python scripts/embed_resources.py --url https://rusa.org/rules
    python scripts/embed_resources.py --source web_rusa.org
"""

import argparse
import csv
import hashlib
import io
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

try:
    import trafilatura
except ImportError:
    trafilatura = None  # Lazy: only needed at runtime, tests mock it

# Ensure project root is importable
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts.import_whatsapp import embed_texts, resolve_database_url

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RESOURCES_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1UHgJyigNRnOG6J4pZe7LL5mpzxNbipSkTZ-TYs-O3WU"
    "/export?format=csv&gid=856968589"
)

MIN_CONTENT_CHARS = 200
SOFT_CHUNK_CHARS = 2000
FETCH_DELAY_SECONDS = 1.5

# Known URL column names to probe in order
_URL_COLUMN_NAMES = ["URL", "Link", "url", "Resource", "link"]


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def url_to_source_name(url: str) -> str:
    """Convert URL to source name with web_ prefix.

    >>> url_to_source_name("https://www.rusa.org/rules")
    'web_rusa.org'
    """
    hostname = urlparse(url).hostname or ""
    return f"web_{hostname.removeprefix('www.')}"


def content_hash(text: str) -> str:
    """SHA-256 hex digest of UTF-8 encoded text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_text(text: str, soft_limit: int = SOFT_CHUNK_CHARS) -> list[str]:
    """Split text into chunks at paragraph boundaries."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []
    chunks: list[str] = []
    current: list[str] = []
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


def extract_url_content(url: str) -> str | None:
    """Fetch URL and extract main body text.

    Returns None if fetch fails or content is below MIN_CONTENT_CHARS.
    """
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return None
    text = trafilatura.extract(downloaded, include_comments=False, include_tables=True)
    if not text or len(text) < MIN_CONTENT_CHARS:
        return None
    return text


def fetch_sheet_urls(sheet_url: str, url_column: str | None = None) -> list[str]:
    """Download Google Sheet as CSV, return all non-empty URL-column values.

    Column detection order:
    1. Explicit url_column argument
    2. Probe known column names (URL, Link, url, Resource, link)
    3. Auto-detect: first column whose values start with 'http'
    """
    response = requests.get(sheet_url, timeout=30)
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.text))
    rows = list(reader)
    if not rows:
        return []

    fieldnames = reader.fieldnames or []

    # 1. Explicit column
    if url_column and url_column in fieldnames:
        return [r[url_column].strip() for r in rows if r.get(url_column, "").strip().startswith("http")]

    # 2. Probe known names
    for name in _URL_COLUMN_NAMES:
        if name in fieldnames:
            return [r[name].strip() for r in rows if r.get(name, "").strip().startswith("http")]

    # 3. Auto-detect column with http values
    for col in fieldnames:
        values = [r.get(col, "").strip() for r in rows]
        if any(v.startswith("http") for v in values):
            return [v for v in values if v.startswith("http")]

    return []


# ---------------------------------------------------------------------------
# DB functions
# ---------------------------------------------------------------------------


def chunk_already_exists(conn, source: str, chash: str) -> bool:
    """Check if a chunk with this content hash already exists for the source."""
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM whatsapp_chunk WHERE source = %s AND content_hash = %s LIMIT 1",
        (source, chash),
    )
    return cur.fetchone() is not None


def bulk_insert_web_chunks(conn, chunks: list[dict], source: str) -> tuple[int, int]:
    """Insert web content chunks into whatsapp_chunk table.

    Each chunk dict has keys: content, embedding, content_hash.
    Uses microsecond offsets on chunk_start/chunk_end to avoid UNIQUE collisions.

    Returns (inserted_count, skipped_count).
    """
    from psycopg2.extras import execute_values

    now = datetime.now(timezone.utc)
    cur = conn.cursor()

    rows = []
    for i, chunk in enumerate(chunks):
        # Microsecond offset per chunk to avoid UNIQUE(source, chunk_start, chunk_end) collision
        ts = now + timedelta(microseconds=i)
        rows.append((
            source,
            ts,
            ts,
            [],  # senders: empty for web chunks
            0,   # message_count: 0 for web chunks
            chunk["content"],
            chunk["embedding"],
            chunk["content_hash"],
        ))

    sql = """
        INSERT INTO whatsapp_chunk
            (source, chunk_start, chunk_end, senders, message_count, content, embedding, content_hash)
        VALUES %s
        ON CONFLICT (source, chunk_start, chunk_end) DO NOTHING
    """
    execute_values(cur, sql, rows)
    inserted = cur.rowcount
    skipped = len(rows) - inserted
    conn.commit()
    return inserted, skipped


# ---------------------------------------------------------------------------
# CLI pipeline
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Embed external resource URLs into pgvector knowledge base."
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch and extract but do not embed or insert")
    parser.add_argument("--source", help="Process only this source name (e.g., web_rusa.org)")
    parser.add_argument("--url", help="Process a single URL instead of the spreadsheet")
    parser.add_argument("--url-column", help="Override URL column name in spreadsheet")
    args = parser.parse_args()

    # Gather URLs
    if args.url:
        urls = [args.url]
        print(f"Single URL mode: {args.url}")
    else:
        print(f"Fetching URL list from spreadsheet...")
        urls = fetch_sheet_urls(RESOURCES_SHEET_URL, url_column=args.url_column)
        print(f"Found {len(urls)} URLs in spreadsheet.")

    if not urls:
        print("No URLs to process.")
        return

    # Filter by source if specified
    if args.source:
        urls = [u for u in urls if url_to_source_name(u) == args.source]
        print(f"Filtered to {len(urls)} URLs matching source '{args.source}'.")

    # Set up DB + OpenAI client (unless dry-run)
    conn = None
    openai_client = None
    if not args.dry_run:
        db_url = resolve_database_url()
        if not db_url:
            print("ERROR: DATABASE_URL not set. Use --dry-run or set DATABASE_URL.")
            sys.exit(1)

        import psycopg2
        from pgvector.psycopg2 import register_vector
        from openai import OpenAI

        conn = psycopg2.connect(db_url)
        register_vector(conn)
        openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    total_inserted = 0
    total_skipped = 0
    total_chunks = 0

    for i, url in enumerate(urls):
        source = url_to_source_name(url)
        print(f"\n[{i + 1}/{len(urls)}] {url}")
        print(f"  Source: {source}")

        try:
            text = extract_url_content(url)
            if not text:
                print(f"  Skipped: no usable content (< {MIN_CONTENT_CHARS} chars)")
                continue

            text_chunks = chunk_text(text)
            print(f"  Extracted {len(text)} chars -> {len(text_chunks)} chunks")
            total_chunks += len(text_chunks)

            if args.dry_run:
                for j, chunk in enumerate(text_chunks):
                    print(f"    Chunk {j + 1}: {len(chunk)} chars, hash={content_hash(chunk)[:12]}...")
                continue

            # Check for existing chunks by content hash
            new_chunks = []
            for chunk in text_chunks:
                chash = content_hash(chunk)
                if chunk_already_exists(conn, source, chash):
                    total_skipped += 1
                    print(f"    Skipped chunk (already embedded): {chash[:12]}...")
                else:
                    new_chunks.append({"content": chunk, "content_hash": chash})

            if not new_chunks:
                print(f"  All {len(text_chunks)} chunks already embedded.")
                continue

            # Embed new chunks
            texts_to_embed = [c["content"] for c in new_chunks]
            print(f"  Embedding {len(new_chunks)} new chunks...")
            embeddings = embed_texts(openai_client, texts_to_embed)

            for chunk, emb in zip(new_chunks, embeddings):
                chunk["embedding"] = emb

            inserted, skipped = bulk_insert_web_chunks(conn, new_chunks, source)
            total_inserted += inserted
            total_skipped += skipped
            print(f"  Inserted: {inserted}, skipped: {skipped}")

        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        # Polite crawling delay between URLs
        if i < len(urls) - 1:
            time.sleep(FETCH_DELAY_SECONDS)

    # Summary
    print(f"\n{'=' * 50}")
    print(f"Done. URLs processed: {len(urls)}")
    print(f"Total chunks: {total_chunks}")
    if not args.dry_run:
        print(f"Inserted: {total_inserted}, Skipped (dedup): {total_skipped}")

    if conn:
        conn.close()


if __name__ == "__main__":
    main()
