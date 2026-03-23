#!/usr/bin/env python3
"""Merge multi-source personality profiles (WhatsApp + blog) into a single merged row.

Reads the 'whatsapp' and 'blog' extraction rows for a rider, applies merge rules
(blog wins on enum conflicts, phrases unioned, lower confidence), and writes a
'merged' row to personality_profile. Evidence from both sources is copied to the
merged extraction_source.

Usage:
    # Dry-run — prints merged JSON, no DB writes
    python scripts/merge_personality.py --rider-name Venki --dry-run

    # Full merge — stores merged profile to DB
    python scripts/merge_personality.py --rider-name Venki

Required environment variables:
    DATABASE_URL — PostgreSQL connection string (always required, even for --dry-run reads)
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

# Ensure project root is on sys.path for scripts.* imports
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv

load_dotenv()

from scripts.personality_helpers import merge_profiles


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Merge WhatsApp + blog personality profiles into a single merged row.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
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
        help="Profile type to merge: 'coach' or 'rider' (default: coach)",
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Output merged JSON to stdout; do not write to DB',
    )
    args = parser.parse_args()

    # --- Validate DB URL ---
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("Error: DATABASE_URL not set.")
        sys.exit(1)

    # --- Connect to DB ---
    import psycopg2
    import psycopg2.extras

    try:
        conn = psycopg2.connect(database_url)
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # --- Look up rider ---
        from scripts.seed_coaching_profiles import lookup_rider
        rider_id = lookup_rider(cur, f'{args.rider_name}%')
        if rider_id is None:
            print(f"Error: Rider '{args.rider_name}' not found in DB.")
            conn.close()
            sys.exit(1)

        # --- Fetch source profiles ---
        cur.execute(
            """SELECT * FROM personality_profile
               WHERE rider_id = %s AND profile_type = %s
                 AND extraction_source IN ('whatsapp', 'blog')
                 AND deleted_at IS NULL""",
            (rider_id, args.profile_type),
        )
        rows = cur.fetchall()

        sources = {row['extraction_source']: dict(row) for row in rows}

        if len(sources) < 2:
            found = list(sources.keys())
            missing = [s for s in ('whatsapp', 'blog') if s not in found]
            print(
                f"Only {found} profile(s) found for {args.rider_name}. "
                f"Merge requires both WhatsApp and blog profiles. "
                f"Missing: {missing}. Run extraction scripts first."
            )
            conn.close()
            sys.exit(0)  # Not an error — just incomplete data

        whatsapp_row = sources['whatsapp']
        blog_row = sources['blog']

        # --- Merge ---
        merged = merge_profiles(whatsapp_row, blog_row)

        # --- Dry-run: print and exit ---
        if args.dry_run:
            print("\n=== DRY-RUN MERGED RESULT ===")
            # Make JSON-serializable (handle date objects, lists)
            printable = {k: (str(v) if isinstance(v, date) else v) for k, v in merged.items()}
            print(json.dumps(printable, indent=2))
            print("\n(Skipped DB write — dry-run mode)")
            conn.close()
            return

        # --- Upsert merged profile ---
        # Build column list from merged dict (excluding extraction_source which goes in the constraint)
        col_names = list(merged.keys())
        col_placeholders = ['%s'] * len(col_names)
        col_values = list(merged.values())

        all_cols = ['rider_id', 'profile_type', 'updated_by', 'extraction_date'] + col_names
        all_placeholders = ['%s', '%s', '%s', '%s'] + col_placeholders
        all_values = [rider_id, args.profile_type, 'merge_script', date.today()] + col_values

        set_parts = [f"{c} = EXCLUDED.{c}" for c in col_names]
        set_parts.append("updated_by = EXCLUDED.updated_by")
        set_parts.append("extraction_date = EXCLUDED.extraction_date")
        set_parts.append("updated_at = NOW()")

        cur.execute(
            f"""INSERT INTO personality_profile ({', '.join(all_cols)})
                VALUES ({', '.join(all_placeholders)})
                ON CONFLICT (rider_id, profile_type, extraction_source) DO UPDATE SET
                {', '.join(set_parts)}""",
            all_values,
        )

        # --- Merge evidence: copy from both sources ---
        cur.execute(
            "DELETE FROM personality_trait_evidence WHERE rider_id = %s AND extraction_source = 'merged'",
            (rider_id,),
        )

        cur.execute(
            """INSERT INTO personality_trait_evidence (rider_id, trait_name, source_quote, extraction_source)
               SELECT rider_id, trait_name, source_quote, 'merged'
               FROM personality_trait_evidence
               WHERE rider_id = %s AND extraction_source IN ('whatsapp', 'blog')""",
            (rider_id,),
        )
        evidence_count = cur.rowcount

        conn.commit()

        # --- Print summary ---
        wa_count = whatsapp_row.get('source_message_count', 0)
        blog_count = blog_row.get('source_message_count', 0)

        # Count evidence per source
        cur.execute(
            """SELECT extraction_source, COUNT(*) as cnt
               FROM personality_trait_evidence
               WHERE rider_id = %s AND extraction_source IN ('whatsapp', 'blog')
               GROUP BY extraction_source""",
            (rider_id,),
        )
        evidence_by_source = {row['extraction_source']: row['cnt'] for row in cur.fetchall()}
        wa_evidence = evidence_by_source.get('whatsapp', 0)
        blog_evidence = evidence_by_source.get('blog', 0)

        print(f"\n=== MERGE SUMMARY ===")
        print(f"Merged personality for {args.rider_name}:")
        print(f"  Sources: whatsapp ({wa_count} messages) + blog ({blog_count} words)")
        print(f"  Merged confidence: {merged['extraction_confidence']}")
        print(f"  Tone: {merged['tone']} (from blog, whatsapp had {whatsapp_row.get('tone')})")
        print(f"  Humor: {merged['humor_type']} (from blog, whatsapp had {whatsapp_row.get('humor_type')})")
        print(f"  Directness: {merged['directness']} (from blog, whatsapp had {whatsapp_row.get('directness')})")
        print(f"  Signature phrases: {merged['signature_phrases']}")
        print(f"  Evidence quotes: {evidence_count} total ({wa_evidence} from WhatsApp, {blog_evidence} from blog)")

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


if __name__ == '__main__':
    main()
