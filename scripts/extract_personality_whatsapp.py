#!/usr/bin/env python3
"""Extract personality traits per sender from WhatsApp chat export.

Parses a WhatsApp .txt or .zip export, groups messages by sender, filters
noise (system messages, media, short reactions, URL-only), samples up to
--max-sample messages spread across the full date range, calls GPT-4o for
structured personality extraction, and stores results in the DB.

Usage:
    # Dry-run — prints extracted JSON to stdout, no DB writes
    python scripts/extract_personality_whatsapp.py \
        --path data/whatsapp/fresh_start/_chat.txt \
        --sender "Venki" \
        --dry-run

    # Full extraction — stores to personality_profile and personality_trait_evidence
    python scripts/extract_personality_whatsapp.py \
        --path data/whatsapp/chat.zip \
        --sender "Shriram" \
        --profile-type coach

Required environment variables:
    OPENAI_API_KEY   — GPT-4o API key
    DATABASE_URL     — PostgreSQL connection string (not needed for --dry-run)
"""

import argparse
import os
import sys
import tempfile
import zipfile
from pathlib import Path

# Ensure project root is on sys.path for scripts.* imports
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv

load_dotenv()

from scripts.personality_helpers import (
    compute_confidence,
    extract_from_messages,
    group_by_sender,
    sample_messages,
    store_evidence,
    store_extraction_results,
)


# ---------------------------------------------------------------------------
# ZIP handling
# ---------------------------------------------------------------------------


def resolve_txt_path(input_path: str) -> tuple[str, tempfile.TemporaryDirectory | None]:
    """Resolve the .txt chat file path from a plain .txt or .zip input.

    Args:
        input_path: Path to either a .txt WhatsApp export or a .zip file.

    Returns:
        Tuple of (resolved_txt_path, tmp_dir_or_None).
        If a temp dir is returned, caller must clean it up.

    Raises:
        SystemExit: If ZIP contains no .txt file.
    """
    if not input_path.lower().endswith('.zip'):
        return input_path, None

    tmp_dir = tempfile.TemporaryDirectory()
    try:
        with zipfile.ZipFile(input_path, 'r') as zf:
            # Find the .txt file inside — handles Unicode and variant names
            txt_name = next(
                (n for n in zf.namelist() if n.endswith('.txt')),
                None,
            )
            if txt_name is None:
                print("Error: No .txt file found inside ZIP archive.")
                sys.exit(1)
            zf.extract(txt_name, tmp_dir.name)
            resolved = os.path.join(tmp_dir.name, txt_name)
            return resolved, tmp_dir
    except zipfile.BadZipFile as e:
        print(f"Error: Invalid ZIP file — {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Sender matching
# ---------------------------------------------------------------------------


def find_sender(
    by_sender: dict[str, list],
    sender_arg: str,
) -> str | None:
    """Find the sender key matching a case-insensitive prefix.

    Args:
        by_sender: Dict mapping sender names to their message lists.
        sender_arg: Prefix string from --sender argument.

    Returns:
        Matched sender name, or None if no unique match.
    """
    prefix_lower = sender_arg.lower()
    matches = [name for name in by_sender if name.lower().startswith(prefix_lower)]

    if len(matches) == 1:
        return matches[0]

    if len(matches) == 0:
        print(f"Error: No sender matching '{sender_arg}' found.")
        print("Available senders:")
        for name in sorted(by_sender.keys()):
            print(f"  {name} ({len(by_sender[name])} qualifying messages)")
        return None

    # Multiple matches — ambiguous
    print(f"Error: Multiple senders match '{sender_arg}':")
    for name in sorted(matches):
        print(f"  {name} ({len(by_sender[name])} qualifying messages)")
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Extract personality traits per sender from WhatsApp chat export.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--path',
        required=True,
        help='Path to WhatsApp chat export (.txt file or .zip file)',
    )
    parser.add_argument(
        '--sender',
        required=True,
        help='Sender name to extract (case-insensitive prefix match)',
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
    parser.add_argument(
        '--max-sample',
        type=int,
        default=200,
        help='Max messages to sample for LLM (default: 200)',
    )
    args = parser.parse_args()

    # --- Validate inputs ---
    if not os.path.exists(args.path):
        print(f"Error: File not found: {args.path}")
        sys.exit(1)

    # --- Resolve API key ---
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        print("Error: OPENAI_API_KEY not set. Export it or add to .env file.")
        sys.exit(1)

    # --- Validate DB URL early if not dry-run ---
    database_url = os.environ.get('DATABASE_URL')
    if not args.dry_run and not database_url:
        print("Error: DATABASE_URL not set (required unless --dry-run).")
        sys.exit(1)

    # --- Set up OpenAI client ---
    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    # --- Resolve .txt path (handles ZIP) ---
    tmp_dir = None
    try:
        txt_path, tmp_dir = resolve_txt_path(args.path)

        # --- Group messages by sender ---
        print(f"Parsing export: {args.path}")
        by_sender = group_by_sender(txt_path)
        total_senders = len(by_sender)
        print(f"Found {total_senders} sender(s) with qualifying messages.")

        # --- Match sender ---
        matched_sender = find_sender(by_sender, args.sender)
        if matched_sender is None:
            sys.exit(1)

        messages = by_sender[matched_sender]
        qualifying_count = len(messages)
        confidence = compute_confidence(qualifying_count)

        print(f"Sender: {matched_sender}")
        print(f"  Qualifying messages: {qualifying_count}")
        print(f"  Confidence: {confidence}")

        if qualifying_count == 0:
            print("Warning: Zero qualifying messages for this sender.")
            sys.exit(1)

        # --- Sample and extract ---
        print(f"  Sampling up to {args.max_sample} messages for GPT-4o...")
        sampled = sample_messages(messages, max_count=args.max_sample)
        print(f"  Sending {len(sampled)} messages to GPT-4o...")

        try:
            extraction = extract_from_messages(client, sampled, matched_sender)
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

            # Look up rider by sender name prefix
            from scripts.seed_coaching_profiles import lookup_rider
            rider_id = lookup_rider(cur, f'{matched_sender}%')
            if rider_id is None:
                # Try the first word of the sender name as a fallback
                first_name = matched_sender.split()[0]
                rider_id = lookup_rider(cur, f'{first_name}%')
            if rider_id is None:
                print(f"Error: Rider '{matched_sender}' not found in DB.")
                print("  Use scripts/seed_coaching_profiles.py to seed rider first,")
                print("  or ensure the rider exists in the rider table.")
                conn.rollback()
                conn.close()
                sys.exit(1)

            store_extraction_results(
                cur,
                rider_id=rider_id,
                extraction=extraction,
                extraction_source='whatsapp',
                message_count=qualifying_count,
                profile_type=args.profile_type,
            )

            evidence_count = store_evidence(
                cur,
                rider_id=rider_id,
                extraction=extraction,
                extraction_source='whatsapp',
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
        print(f"Extracted personality for {matched_sender}:")
        print(f"  Qualifying messages: {qualifying_count}")
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

    finally:
        if tmp_dir is not None:
            tmp_dir.cleanup()


if __name__ == '__main__':
    main()
