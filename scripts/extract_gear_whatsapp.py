#!/usr/bin/env python3
"""Extract gear preferences per rider from WhatsApp chat exports.

Parses WhatsApp exports, finds messages mentioning bikes, gear, lights, bags,
wheels, accessories, etc., groups by sender, and uses GPT-4o to extract
structured gear preferences. Stores results in gear_preference table.

Usage:
    # Dry-run — prints extracted JSON, no DB writes
    python scripts/extract_gear_whatsapp.py --dry-run

    # Full extraction — stores to gear_preference table
    python scripts/extract_gear_whatsapp.py

Required environment variables:
    OPENAI_API_KEY   — GPT-4o API key
    DATABASE_URL     — PostgreSQL connection string (not needed for --dry-run)
"""

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

# Ensure project root is importable
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from openai import OpenAI

# Gear-related keywords for filtering messages
GEAR_KEYWORDS = [
    'bike', 'bicycle', 'frame', 'carbon', 'steel', 'titanium', 'aluminum',
    'trek', 'specialized', 'cannondale', 'giant', 'cervelo', 'bmc', 'canyon',
    'colnago', 'pinarello', 'surly', 'salsa', 'raleigh', 'fuji',
    'shimano', 'sram', 'campagnolo', 'ultegra', 'dura-ace', 'dura ace',
    '105', 'tiagra', 'red', 'force', 'rival', 'apex',
    'groupset', 'groupo', 'derailleur', 'cassette', 'crankset', 'chainring',
    'wheel', 'wheelset', 'rim', 'hub', 'spoke', 'tubeless', 'clincher', 'tubular',
    'tire', 'tyre', 'gp5000', 'continental', 'schwalbe', 'vittoria', 'pirelli',
    'light', 'headlight', 'taillight', 'dynamo', 'lumen', 'lux',
    'cygolite', 'niterider', 'busch', 'müller', 'b&m', 'sinewave', 'magicshine',
    'exposure', 'fenix', 'supernova',
    'bag', 'handlebar bag', 'saddle bag', 'frame bag', 'top tube',
    'apidura', 'revelate', 'ortlieb', 'carradice', 'swift',
    'garmin', 'wahoo', 'hammerhead', 'gps', 'computer', 'edge', 'elemnt',
    'jersey', 'bibs', 'kit', 'rapha', 'assos', 'castelli', 'pearl izumi',
    'pedal', 'cleat', 'spd', 'look', 'speedplay', 'shoe',
    'saddle', 'brooks', 'fizik', 'specialized power',
    'upgrade', 'bought', 'ordered', 'new bike', 'my bike', 'riding my',
    'n+1', 'nbd', 'new bike day',
]

GEAR_PATTERN = re.compile(
    '|'.join(re.escape(kw) for kw in GEAR_KEYWORDS),
    re.IGNORECASE
)


def parse_whatsapp(path: str) -> dict[str, list[str]]:
    """Parse WhatsApp export, return {sender: [messages]} for gear-related messages only."""
    msg_pattern = re.compile(r'^\[(\d{1,2}/\d{1,2}/\d{2,4}),\s*\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M\]\s+(.+?):\s+(.*)')

    by_sender: dict[str, list[str]] = {}
    current_sender = None
    current_msg = None

    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            m = msg_pattern.match(line)
            if m:
                # Save previous message if gear-related
                if current_sender and current_msg and GEAR_PATTERN.search(current_msg):
                    by_sender.setdefault(current_sender, []).append(current_msg)
                current_sender = m.group(2)
                current_msg = m.group(3)
            elif current_msg is not None:
                current_msg += '\n' + line.rstrip()

        # Last message
        if current_sender and current_msg and GEAR_PATTERN.search(current_msg):
            by_sender.setdefault(current_sender, []).append(current_msg)

    return by_sender


EXTRACTION_PROMPT = """You are analyzing WhatsApp group chat messages from a cycling team.
Extract gear and equipment preferences for this person based on their messages.

Return a JSON object with these fields (use null if not mentioned):
{
  "bike_make": "brand name (Trek, Specialized, Cannondale, etc.)",
  "bike_model": "model name",
  "bike_year": null or integer year,
  "bike_material": "aluminum|steel|titanium|carbon|other",
  "wheels_tires": "description of wheels/tires setup",
  "lighting": "description of lights they use or discuss",
  "bags": "bags/luggage they use or discuss",
  "navigation": "GPS/computer device",
  "kit": "clothing brands/preferences",
  "value_orientation": "budget|mid-range|premium|buy-once-buy-right",
  "gear_notes": "any other notable gear preferences, opinions, or buying patterns"
}

Be specific — use exact brand/model names when mentioned. If they discuss multiple bikes,
capture the primary one they ride most. For value_orientation, infer from their buying
patterns and opinions about spending on gear.

Only extract what is clearly stated or strongly implied. Don't guess."""


def extract_gear(client: OpenAI, sender: str, messages: list[str], max_sample: int = 100) -> dict:
    """Use GPT-4o to extract gear preferences from a person's messages."""
    # Sample messages evenly across the list
    if len(messages) > max_sample:
        step = len(messages) / max_sample
        messages = [messages[int(i * step)] for i in range(max_sample)]

    msg_block = '\n'.join(f'- {m[:300]}' for m in messages)

    response = client.chat.completions.create(
        model='gpt-4o',
        messages=[
            {'role': 'system', 'content': EXTRACTION_PROMPT},
            {'role': 'user', 'content': f'Messages from {sender} ({len(messages)} gear-related messages):\n\n{msg_block}'}
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=800,
    )

    return json.loads(response.choices[0].message.content)


def main():
    parser = argparse.ArgumentParser(description='Extract gear preferences from WhatsApp chat exports.')
    parser.add_argument('--dry-run', action='store_true', help='Print results, no DB writes')
    parser.add_argument('--min-messages', type=int, default=3, help='Min gear messages to extract (default: 3)')
    args = parser.parse_args()

    chat_dir = Path(_project_root) / 'data' / 'whatsapp'
    txt_files = []
    for d in chat_dir.iterdir():
        if d.is_dir():
            for f in d.glob('_chat.txt'):
                txt_files.append(f)
    for f in chat_dir.glob('*.txt'):
        txt_files.append(f)

    if not txt_files:
        print('No WhatsApp chat files found in data/whatsapp/')
        sys.exit(1)

    # Parse all chats and merge gear messages
    all_gear: dict[str, list[str]] = {}
    for f in txt_files:
        print(f'Parsing: {f.name}')
        by_sender = parse_whatsapp(str(f))
        for sender, msgs in by_sender.items():
            all_gear.setdefault(sender, []).extend(msgs)

    print(f'\nFound gear messages for {len(all_gear)} senders')
    for sender, msgs in sorted(all_gear.items(), key=lambda x: -len(x[1])):
        print(f'  {sender}: {len(msgs)} gear messages')

    # Filter to senders with enough messages
    eligible = {s: m for s, m in all_gear.items() if len(m) >= args.min_messages}
    print(f'\n{len(eligible)} senders with >= {args.min_messages} gear messages')

    # Extract
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        for line in Path(_project_root / '.env').read_text().splitlines():
            if line.startswith('OPENAI_API_KEY='):
                api_key = line.split('=', 1)[1]
                break
    if not api_key:
        print('Error: OPENAI_API_KEY not set')
        sys.exit(1)

    client = OpenAI(api_key=api_key)
    results = {}

    for sender, msgs in sorted(eligible.items(), key=lambda x: -len(x[1])):
        print(f'\nExtracting gear for {sender} ({len(msgs)} messages)...')
        try:
            gear = extract_gear(client, sender, msgs)
            results[sender] = gear
            # Show summary
            bike = f"{gear.get('bike_make', '?')} {gear.get('bike_model', '?')}"
            val = gear.get('value_orientation', '?')
            print(f'  Bike: {bike} | Value: {val}')
            if gear.get('lighting'):
                print(f'  Lights: {gear["lighting"]}')
            if gear.get('bags'):
                print(f'  Bags: {gear["bags"]}')
        except Exception as e:
            print(f'  ERROR: {e}')

    if args.dry_run:
        print('\n=== DRY RUN — Full results ===')
        print(json.dumps(results, indent=2))
        return

    # Store to DB
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        for line in Path(_project_root / '.env').read_text().splitlines():
            if line.startswith('DATABASE_URL='):
                db_url = line.split('=', 1)[1]
                break

    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    stored = 0
    skipped = 0
    for sender, gear in results.items():
        # Look up rider
        first_name = sender.split()[0]
        last_name = sender.split()[-1] if len(sender.split()) > 1 else ''

        cur.execute(
            "SELECT id FROM rider WHERE first_name ILIKE %s OR last_name ILIKE %s LIMIT 1",
            (f'{first_name}%', f'{last_name}%')
        )
        row = cur.fetchone()
        if not row:
            print(f'  Skipped {sender}: not in rider table')
            skipped += 1
            continue

        rider_id = row['id']

        # Upsert gear preference
        fields = {
            'bike_make': gear.get('bike_make'),
            'bike_model': gear.get('bike_model'),
            'bike_year': gear.get('bike_year'),
            'bike_material': gear.get('bike_material'),
            'wheels_tires': gear.get('wheels_tires'),
            'lighting': gear.get('lighting'),
            'bags': gear.get('bags'),
            'navigation': gear.get('navigation'),
            'kit': gear.get('kit'),
            'value_orientation': gear.get('value_orientation'),
        }

        # Validate enum values
        valid_materials = ('aluminum', 'steel', 'titanium', 'carbon', 'other')
        if fields['bike_material'] and fields['bike_material'] not in valid_materials:
            fields['bike_material'] = None

        valid_values = ('budget', 'mid-range', 'premium', 'buy-once-buy-right')
        if fields['value_orientation'] and fields['value_orientation'] not in valid_values:
            fields['value_orientation'] = None

        set_parts = []
        values = []
        for k, v in fields.items():
            if v is not None:
                set_parts.append(f'{k} = %s')
                values.append(v)

        if not set_parts:
            skipped += 1
            continue

        values.extend([rider_id])
        cur.execute(f"""
            INSERT INTO gear_preference (rider_id, {', '.join(k for k, v in fields.items() if v is not None)}, updated_by)
            VALUES (%s, {', '.join('%s' for v in values[:-1])}, 'gear_extract')
            ON CONFLICT (rider_id) DO UPDATE SET
            {', '.join(set_parts)}, updated_at = NOW(), updated_by = 'gear_extract'
        """, [rider_id] + [v for v in fields.values() if v is not None] + [v for v in fields.values() if v is not None])

        stored += 1
        print(f'  ✓ {sender} (rider_id={rider_id})')

    conn.commit()
    cur.close()
    conn.close()

    print(f'\n=== Done. Stored: {stored}, Skipped: {skipped} ===')


if __name__ == '__main__':
    main()
