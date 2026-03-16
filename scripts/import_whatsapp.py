#!/usr/bin/env python3
"""
Import WhatsApp group chat exports into Supabase pgvector.

Pipeline: parse -> incremental filter -> chunk -> two-stage filter -> embed -> insert

Usage:
    # Dry-run (no API key or DB needed with --skip-llm-filter):
    python scripts/import_whatsapp.py --source fresh_start --path data/whatsapp/fresh_start/_chat.txt --dry-run --skip-llm-filter

    # Full import:
    DATABASE_URL='postgresql://...' OPENAI_API_KEY='sk-...' python scripts/import_whatsapp.py --source fresh_start --path data/whatsapp/fresh_start/_chat.txt

    # Incremental re-import (only new messages after last import):
    python scripts/import_whatsapp.py --source fresh_start --path data/whatsapp/fresh_start/_chat.txt
"""

import argparse
import os
import sys
from pathlib import Path

# Ensure scripts package is importable when run directly
_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts.whatsapp_parser import (
    parse_export,
    chunk_by_time_window,
    is_cycling_chunk_rule,
    classify_chunks_llm,
    format_chunk_content,
)

# Max characters per chunk text sent to embeddings API.
# Safety margin: ~6000 tokens * 4 chars/token = 24000 chars.
MAX_CHUNK_CHARS = 24000


def resolve_database_url():
    """Resolve DATABASE_URL from environment or .env file."""
    url = os.environ.get('DATABASE_URL')
    if url:
        return url
    env_path = _project_root / '.env'
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith('DATABASE_URL='):
                return line.split('=', 1)[1].strip()
    return None


def get_last_imported_timestamp(conn, source):
    """Query the last imported chunk_end for a given source.

    Returns None if no prior import exists (full import mode).
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT MAX(chunk_end) FROM whatsapp_chunk WHERE source = %s",
        (source,),
    )
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def extract_chunk_metadata(chunk_messages):
    """Extract metadata from a list of message dicts for one chunk.

    Returns dict with keys: senders, chunk_start, chunk_end, message_count.
    """
    senders = list(dict.fromkeys(m['sender'] for m in chunk_messages))
    return {
        'senders': senders,
        'chunk_start': chunk_messages[0]['ts'],
        'chunk_end': chunk_messages[-1]['ts'],
        'message_count': len(chunk_messages),
    }


def embed_texts(client, texts, batch_size=100):
    """Generate embeddings for a list of texts using OpenAI API.

    Returns list of embedding vectors (numpy arrays).
    """
    import numpy as np

    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        # Truncate any oversized chunks
        batch = [t[:MAX_CHUNK_CHARS] for t in batch]
        response = client.embeddings.create(
            input=batch,
            model='text-embedding-3-small',
        )
        for item in response.data:
            all_embeddings.append(np.array(item.embedding))
        print(f"  Embedded batch {i // batch_size + 1}/{(len(texts) - 1) // batch_size + 1} ({len(batch)} chunks)")
    return all_embeddings


def bulk_insert_chunks(conn, records, source):
    """Bulk insert chunk records into whatsapp_chunk table.

    Uses ON CONFLICT DO NOTHING for idempotent re-import.

    Args:
        conn: psycopg2 connection (with register_vector already called).
        records: List of dicts with keys: content, embedding, senders,
                 chunk_start, chunk_end, message_count.
        source: Source name string.

    Returns:
        Tuple of (inserted_count, skipped_count).
    """
    from psycopg2.extras import execute_values

    cur = conn.cursor()

    rows = [
        (
            source,
            r['chunk_start'],
            r['chunk_end'],
            r['senders'],
            r['message_count'],
            r['content'],
            r['embedding'],
        )
        for r in records
    ]

    # Use execute_values with ON CONFLICT for idempotent inserts
    sql = """
        INSERT INTO whatsapp_chunk
            (source, chunk_start, chunk_end, senders, message_count, content, embedding)
        VALUES %s
        ON CONFLICT (source, chunk_start, chunk_end) DO NOTHING
    """
    # Insert in sub-batches to avoid oversized queries
    total_inserted = 0
    batch_size = 100
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        cur_before = _get_table_count(conn, source)
        execute_values(cur, sql, batch)
        conn.commit()
        cur_after = _get_table_count(conn, source)
        batch_inserted = cur_after - cur_before
        total_inserted += batch_inserted
        print(f"  Inserted batch {i // batch_size + 1} ({batch_inserted}/{len(batch)} new)")

    skipped = len(rows) - total_inserted
    return total_inserted, skipped


def _get_table_count(conn, source):
    """Get current row count for a source."""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM whatsapp_chunk WHERE source = %s", (source,))
    return cur.fetchone()[0]


def main():
    parser = argparse.ArgumentParser(
        description='Import WhatsApp group chat exports into pgvector.'
    )
    parser.add_argument(
        '--path',
        default='data/whatsapp/',
        help='Path to WhatsApp .txt export file (default: data/whatsapp/)',
    )
    parser.add_argument(
        '--source',
        required=True,
        help='Source name, e.g. "fresh_start" or "brevets"',
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=100,
        help='Embedding batch size (default: 100)',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Parse and filter only -- skip embedding and DB insert',
    )
    parser.add_argument(
        '--skip-llm-filter',
        action='store_true',
        help='Skip Stage 2 LLM classification (useful for testing)',
    )
    args = parser.parse_args()

    # --- Validate inputs ---
    if not os.path.exists(args.path):
        print(f"Error: File not found: {args.path}")
        sys.exit(1)

    # --- Resolve API key (needed unless dry-run + skip-llm-filter) ---
    openai_client = None
    need_api_key = not (args.dry_run and args.skip_llm_filter)
    if need_api_key:
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            # Try .env
            env_path = _project_root / '.env'
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith('OPENAI_API_KEY='):
                        api_key = line.split('=', 1)[1].strip()
                        break
        if not api_key:
            print("Error: OPENAI_API_KEY not set (required unless --dry-run AND --skip-llm-filter)")
            sys.exit(1)
        from openai import OpenAI
        openai_client = OpenAI(api_key=api_key)

    # --- Resolve DB connection (needed unless dry-run) ---
    conn = None
    last_imported_ts = None
    if not args.dry_run:
        database_url = resolve_database_url()
        if not database_url:
            print("Error: DATABASE_URL not set")
            sys.exit(1)
        import psycopg2
        from pgvector.psycopg2 import register_vector
        conn = psycopg2.connect(database_url)
        register_vector(conn)

        # Check for incremental append
        last_imported_ts = get_last_imported_timestamp(conn, args.source)
        if last_imported_ts:
            print(f"Incremental mode: only processing messages after {last_imported_ts}")
        else:
            print("Full import mode: no prior import found for this source")

    # ====================================================================
    # PIPELINE
    # ====================================================================

    # Step 1: Parse export file
    print(f"\n--- Step 1: Parse export file ---")
    messages = parse_export(args.path)
    total_parsed = len(messages)
    print(f"Parsed {total_parsed} messages from {args.path}")

    # Step 2: Apply incremental filter
    print(f"\n--- Step 2: Incremental filter ---")
    if last_imported_ts:
        # Make last_imported_ts offset-naive for comparison if needed
        if last_imported_ts.tzinfo is not None:
            last_imported_ts = last_imported_ts.replace(tzinfo=None)
        new_messages = [m for m in messages if m['ts'] > last_imported_ts]
        print(f"New messages: {len(new_messages)} / {total_parsed} total")
        if not new_messages:
            print("No new messages to import")
            if conn:
                conn.close()
            return
        messages = new_messages
    elif args.dry_run:
        # In dry-run without DB, simulate: check if user specified
        # incremental but there is nothing to filter against
        print("Dry-run: processing all messages (no DB to check incremental state)")
    else:
        print(f"Processing all {len(messages)} messages (full import)")

    # Step 3: Chunk by 30-min time window
    print(f"\n--- Step 3: Chunk by time window ---")
    chunks = chunk_by_time_window(messages, window_minutes=30)
    print(f"Created {len(chunks)} chunks (30-min time windows)")

    if not chunks:
        print("No chunks created -- nothing to import")
        if conn:
            conn.close()
        return

    # Step 4: Format each chunk
    print(f"\n--- Step 4: Format chunks ---")
    formatted = []
    for chunk_msgs in chunks:
        content = format_chunk_content(chunk_msgs)
        meta = extract_chunk_metadata(chunk_msgs)
        formatted.append({
            'content': content,
            'messages': chunk_msgs,
            **meta,
        })
    print(f"Formatted {len(formatted)} chunks")

    # Step 5: Stage 1 filter -- rule-based cycling keyword match
    print(f"\n--- Step 5: Two-stage filtering ---")
    stage1_passed = []
    stage1_failed = []
    for item in formatted:
        if is_cycling_chunk_rule(item['content']):
            stage1_passed.append(item)
        else:
            stage1_failed.append(item)
    print(f"Stage 1 (rule-based): {len(stage1_passed)} retained, {len(stage1_failed)} did not match keywords")

    # Step 6: Stage 2 filter -- LLM classification for non-keyword chunks
    stage2_recovered = []
    if args.skip_llm_filter:
        print("Stage 2 (LLM): skipped (--skip-llm-filter)")
    elif stage1_failed:
        print(f"Stage 2 (LLM): classifying {len(stage1_failed)} non-keyword chunks...")
        failed_texts = [item['content'] for item in stage1_failed]
        recovered_texts = classify_chunks_llm(failed_texts, openai_client)
        # Map recovered texts back to their full records
        recovered_set = set(recovered_texts)
        for item in stage1_failed:
            if item['content'] in recovered_set:
                stage2_recovered.append(item)
        print(f"Stage 2 (LLM): recovered {len(stage2_recovered)} additional cycling chunks")
    else:
        print("Stage 2 (LLM): no non-keyword chunks to classify")

    # Combine both stages
    cycling_chunks = stage1_passed + stage2_recovered
    discarded = len(formatted) - len(cycling_chunks)

    print(f"\nFiltering summary:")
    print(f"  Stage 1 retained: {len(stage1_passed)}")
    print(f"  Stage 2 recovered: {len(stage2_recovered)}")
    print(f"  Total cycling chunks: {len(cycling_chunks)}")
    print(f"  Discarded: {discarded}")

    if not cycling_chunks:
        print("No cycling-relevant chunks found -- nothing to import")
        if conn:
            conn.close()
        return

    # Step 7: Dry-run stops here
    if args.dry_run:
        print(f"\n=== DRY-RUN SUMMARY ===")
        print(f"Total parsed messages: {total_parsed}")
        print(f"Messages after incremental filter: {len(messages)}")
        print(f"Chunks created: {len(chunks)}")
        print(f"Stage 1 retained: {len(stage1_passed)}")
        print(f"Stage 2 recovered: {len(stage2_recovered)}")
        print(f"Total cycling chunks: {len(cycling_chunks)}")
        print(f"Discarded: {discarded}")
        print(f"(Skipped embedding and DB insert in dry-run mode)")
        return

    # Step 8: Generate embeddings
    print(f"\n--- Step 6: Generate embeddings ---")
    texts_to_embed = [item['content'] for item in cycling_chunks]
    embeddings = embed_texts(openai_client, texts_to_embed, batch_size=args.batch_size)

    # Attach embeddings to records
    for item, emb in zip(cycling_chunks, embeddings):
        item['embedding'] = emb

    # Step 9: Bulk insert
    print(f"\n--- Step 7: Bulk insert into whatsapp_chunk ---")
    inserted, skipped = bulk_insert_chunks(conn, cycling_chunks, args.source)

    conn.close()

    # Final summary
    print(f"\n=== IMPORT SUMMARY ===")
    print(f"Source: {args.source}")
    print(f"Total parsed messages: {total_parsed}")
    print(f"Messages after incremental filter: {len(messages)}")
    print(f"Chunks created: {len(chunks)}")
    print(f"Stage 1 retained: {len(stage1_passed)}")
    print(f"Stage 2 recovered: {len(stage2_recovered)}")
    print(f"Total cycling chunks: {len(cycling_chunks)}")
    print(f"Chunks inserted: {inserted}")
    print(f"Chunks skipped (duplicates): {skipped}")


if __name__ == '__main__':
    main()
