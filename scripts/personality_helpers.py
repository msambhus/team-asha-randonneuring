#!/usr/bin/env python3
"""Shared personality extraction utilities for WhatsApp and blog extraction scripts.

Provides:
  - Pydantic models: PersonalityExtraction, TraitEvidence
  - EXTRACTION_SYSTEM_PROMPT: system prompt for GPT-4o personality classification
  - group_by_sender: parse WhatsApp export and group qualifying messages by sender
  - compute_confidence: map qualifying message count to confidence level string
  - sample_messages: sample messages spread across time range
  - extract_from_messages: call GPT-4o structured output for personality extraction
  - store_extraction_results: upsert personality_profile row for a rider
  - store_evidence: delete + insert personality_trait_evidence rows (idempotent)
  - merge_profiles: merge blog (priority) + WhatsApp personality profiles

Used by:
  - scripts/extract_personality_whatsapp.py (Plan 02)
  - scripts/extract_personality_blog.py (Plan 03)
  - scripts/merge_personality.py (Plan 03)
"""

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from datetime import date, datetime
from typing import Literal, Optional, Union

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TraitEvidence(BaseModel):
    """A single evidence record: one trait name and one verbatim source quote."""
    trait_name: str
    source_quote: str


class PersonalityExtraction(BaseModel):
    """Structured personality profile extracted via GPT-4o structured output.

    All evidence fields are Optional so the model can be used without evidence
    (e.g. in dry-run comparisons or unit test fixtures).
    """
    tone: Literal['direct', 'warm', 'playful', 'serious', 'sarcastic']
    humor_type: Literal['none', 'dry', 'sarcastic', 'gentle', 'self-deprecating']
    directness: Literal['low', 'medium', 'high']
    encouragement_style: Literal['data-driven', 'emotional', 'balanced', 'tough-love']
    technical_depth: Literal['beginner', 'intermediate', 'expert']
    domain_bias: Optional[str] = None          # e.g. "gear and components"
    response_length_tendency: Literal['brief', 'moderate', 'verbose']
    question_asking_behavior: Literal['rarely', 'sometimes', 'frequently']
    signature_phrases: list[str] = Field(default_factory=list, max_length=5)

    # Evidence: 3-5 quotes per trait category (optional for test fixtures)
    tone_evidence: Optional[list[str]] = Field(default=None, max_length=5)
    humor_evidence: Optional[list[str]] = Field(default=None, max_length=5)
    directness_evidence: Optional[list[str]] = Field(default=None, max_length=5)
    signature_phrase_evidence: Optional[list[str]] = Field(default=None, max_length=5)


# ---------------------------------------------------------------------------
# Extraction system prompt
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Noise filter helpers
# ---------------------------------------------------------------------------

# Media skip patterns — case-insensitive substring match
_MEDIA_SKIP_PATTERNS = [
    'image omitted', 'video omitted', 'audio omitted',
    'sticker omitted', 'document omitted', 'gif omitted',
    'contact card omitted', 'location:', 'file attached',
    '<media omitted>',
]


def _is_noise(content: str) -> bool:
    """Return True if the message content should be filtered out."""
    stripped = content.strip()
    lower = stripped.lower()

    # Media/system skip patterns
    if any(p in lower for p in _MEDIA_SKIP_PATTERNS):
        return True

    # Short reactions (fewer than 3 words)
    if len(stripped.split()) < 3:
        return True

    # URL-only messages
    if stripped.startswith('https://') and ' ' not in stripped:
        return True

    return False


# ---------------------------------------------------------------------------
# Core extraction functions
# ---------------------------------------------------------------------------


def group_by_sender(
    source: Union[str, list],
) -> dict[str, list[dict]]:
    """Parse WhatsApp export and group qualifying messages by sender name.

    Args:
        source: Either a filepath (str) to a WhatsApp .txt export, or a
                pre-parsed list of message dicts (for testing / callers who
                already have parsed messages).

    Returns:
        Dict mapping sender name to list of qualifying message dicts.
    """
    if isinstance(source, str):
        from scripts.whatsapp_parser import parse_export
        all_messages = parse_export(source)
    else:
        all_messages = source

    by_sender: dict[str, list[dict]] = {}
    for msg in all_messages:
        if msg.get('is_system', False):
            continue
        content = msg.get('content', '').strip()
        if _is_noise(content):
            continue
        sender = msg.get('sender', '')
        by_sender.setdefault(sender, []).append(msg)

    return by_sender


def compute_confidence(qualifying_message_count: int) -> str:
    """Map qualifying message count to confidence level string.

    Args:
        qualifying_message_count: Number of qualifying messages from group_by_sender.

    Returns:
        'high' if >= 50, 'medium' if >= 20, 'low' otherwise.
    """
    if qualifying_message_count >= 50:
        return 'high'
    elif qualifying_message_count >= 20:
        return 'medium'
    else:
        return 'low'


def sample_messages(messages: list[dict], max_count: int = 200) -> list[dict]:
    """Sample messages spread evenly across the time range.

    If len(messages) <= max_count, all messages are returned.
    Otherwise, evenly-spaced indexes are selected so the sample spans
    the full date range rather than just the first N messages.

    Args:
        messages: List of message dicts (already sorted by timestamp from parser).
        max_count: Maximum number of messages to include in the sample.

    Returns:
        Sampled list of message dicts.
    """
    if len(messages) <= max_count:
        return messages

    # Compute evenly-spaced indices across the full range
    n = len(messages)
    step = n / max_count
    indices = [int(i * step) for i in range(max_count)]
    return [messages[i] for i in indices]


def extract_from_messages(
    client,
    messages: list[dict],
    sender_name: str,
) -> PersonalityExtraction:
    """Call GPT-4o structured output to extract personality from messages.

    Args:
        client: OpenAI client instance.
        messages: List of qualifying message dicts (will be sampled internally).
        sender_name: Display name for the user prompt.

    Returns:
        Parsed PersonalityExtraction pydantic model.
    """
    sampled = sample_messages(messages)
    formatted_lines = []
    for m in sampled:
        ts = m.get('ts')
        if isinstance(ts, datetime):
            date_str = ts.strftime('%Y-%m-%d')
        else:
            date_str = str(ts)[:10] if ts else 'unknown'
        formatted_lines.append(f"[{date_str}] {m['content']}")
    formatted = '\n'.join(formatted_lines)

    response = client.chat.completions.parse(
        model='gpt-4o',
        messages=[
            {'role': 'system', 'content': EXTRACTION_SYSTEM_PROMPT},
            {'role': 'user', 'content': f"Analyze {sender_name}'s messages:\n\n{formatted}"},
        ],
        response_format=PersonalityExtraction,
        temperature=0,
    )
    return response.choices[0].message.parsed


# ---------------------------------------------------------------------------
# Database storage helpers
# ---------------------------------------------------------------------------


def store_extraction_results(
    cur,
    rider_id: int,
    extraction: PersonalityExtraction,
    extraction_source: str,
    message_count: int,
    profile_type: str = 'coach',
) -> None:
    """Upsert personality profile row for a rider.

    Uses INSERT ... ON CONFLICT (rider_id, profile_type, extraction_source)
    DO UPDATE SET ... for idempotent re-extraction.

    Args:
        cur: psycopg2 cursor (with RealDictCursor or plain cursor).
        rider_id: Primary key of the rider in the rider table.
        extraction: Parsed PersonalityExtraction model.
        extraction_source: One of 'whatsapp', 'blog', 'manual', 'merged'.
        message_count: Number of qualifying messages used for extraction.
        profile_type: 'coach' or 'rider' (default: 'coach').
    """
    confidence = compute_confidence(message_count)

    # Build field dict from extraction (exclude evidence fields)
    evidence_fields = {'tone_evidence', 'humor_evidence', 'directness_evidence', 'signature_phrase_evidence'}
    extraction_dict = extraction.model_dump(exclude=evidence_fields)

    fields = {
        **extraction_dict,
        'extraction_source': extraction_source,
        'extraction_date': date.today(),
        'source_message_count': message_count,
        'extraction_confidence': confidence,
    }

    col_names = list(fields.keys())
    col_values = list(fields.values())
    all_cols = ['rider_id', 'profile_type', 'updated_by'] + col_names
    all_placeholders = ['%s', '%s', '%s'] + ['%s'] * len(col_names)
    all_values = [rider_id, profile_type, 'extraction_script'] + col_values

    set_parts = [f"{c} = EXCLUDED.{c}" for c in col_names]
    set_parts.append("updated_by = EXCLUDED.updated_by")
    set_parts.append("updated_at = NOW()")

    cur.execute(
        f"""INSERT INTO personality_profile ({', '.join(all_cols)})
            VALUES ({', '.join(all_placeholders)})
            ON CONFLICT (rider_id, profile_type, extraction_source) DO UPDATE SET
            {', '.join(set_parts)}""",
        all_values,
    )


def store_evidence(
    cur,
    rider_id: int,
    extraction: PersonalityExtraction,
    extraction_source: str,
) -> int:
    """Store trait evidence quotes in personality_trait_evidence table.

    Deletes existing evidence for (rider_id, extraction_source) before
    inserting new rows — makes re-extraction idempotent.

    Args:
        cur: psycopg2 cursor.
        rider_id: Primary key of the rider.
        extraction: Parsed PersonalityExtraction with evidence fields populated.
        extraction_source: One of 'whatsapp', 'blog', 'manual', 'merged'.

    Returns:
        Number of evidence rows inserted.
    """
    # Delete old evidence for this rider + source (idempotent re-extraction)
    cur.execute(
        "DELETE FROM personality_trait_evidence WHERE rider_id = %s AND extraction_source = %s",
        (rider_id, extraction_source),
    )

    evidence_map = {
        'tone': extraction.tone_evidence or [],
        'humor_type': extraction.humor_evidence or [],
        'directness': extraction.directness_evidence or [],
        'signature_phrases': extraction.signature_phrase_evidence or [],
    }

    inserted = 0
    for trait_name, quotes in evidence_map.items():
        for quote in quotes:
            truncated = str(quote)[:500]
            cur.execute(
                """INSERT INTO personality_trait_evidence
                       (rider_id, trait_name, source_quote, extraction_source)
                   VALUES (%s, %s, %s, %s)""",
                (rider_id, trait_name, truncated, extraction_source),
            )
            inserted += 1

    return inserted


# ---------------------------------------------------------------------------
# Merge logic (used by merge_personality.py in Plan 03)
# ---------------------------------------------------------------------------


def merge_profiles(whatsapp_row: dict, blog_row: dict) -> dict:
    """Merge blog (priority) with WhatsApp personality profile.

    Merge rules:
    - Enum fields: blog wins if present; fall back to WhatsApp value.
    - signature_phrases: union of both lists, deduplicated, capped at 5 items.
    - extraction_confidence: lower of the two source values (conservative merge).
    - source_message_count: sum of both sources.
    - extraction_source: always 'merged'.

    Args:
        whatsapp_row: personality_profile row dict from extraction_source='whatsapp'.
        blog_row: personality_profile row dict from extraction_source='blog'.

    Returns:
        Merged field dict ready for DB upsert with extraction_source='merged'.
    """
    enum_fields = [
        'tone', 'humor_type', 'directness', 'encouragement_style',
        'technical_depth', 'response_length_tendency',
        'question_asking_behavior', 'domain_bias',
    ]
    merged: dict = {}

    # Blog wins on enum/optional fields; fall back to WhatsApp
    for field in enum_fields:
        merged[field] = blog_row.get(field) or whatsapp_row.get(field)

    # Signature phrases: union, blog first, deduplicated, capped at 5
    wa_phrases = whatsapp_row.get('signature_phrases') or []
    blog_phrases = blog_row.get('signature_phrases') or []
    seen: set = set()
    combined: list = []
    for phrase in blog_phrases + wa_phrases:
        if phrase not in seen:
            seen.add(phrase)
            combined.append(phrase)
    merged['signature_phrases'] = combined[:5]

    # Confidence: take the lower (conservative)
    confidence_rank = {'high': 2, 'medium': 1, 'low': 0}
    wa_conf = whatsapp_row.get('extraction_confidence', 'low')
    blog_conf = blog_row.get('extraction_confidence', 'low')
    merged['extraction_confidence'] = (
        wa_conf if confidence_rank.get(wa_conf, 0) <= confidence_rank.get(blog_conf, 0) else blog_conf
    )

    # Source message count: sum
    merged['source_message_count'] = (
        (whatsapp_row.get('source_message_count') or 0)
        + (blog_row.get('source_message_count') or 0)
    )

    merged['extraction_source'] = 'merged'
    return merged
