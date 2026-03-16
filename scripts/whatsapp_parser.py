"""WhatsApp export parser, chunker, rule-based filter, LLM classifier, and formatter.

Parses WhatsApp group chat .txt exports into structured message dicts,
chunks them by time windows, filters for cycling-relevant content using
a two-stage pipeline (rule-based + LLM batch classification), and formats
chunks as readable timestamped text.

Functions 1-3 and 5 use only Python stdlib (re, datetime, json, logging).
Function 4 (classify_chunks_llm) receives an OpenAI client as a parameter
but does NOT import openai itself -- the caller passes the client.

Exports:
    parse_export          -- Parse a WhatsApp .txt export into message dicts
    chunk_by_time_window  -- Group messages into time-window chunks
    is_cycling_chunk_rule -- Stage 1 rule-based cycling content filter
    classify_chunks_llm   -- Stage 2 LLM batch classification
    format_chunk_content  -- Format chunk messages as readable timestamped text
"""

import json
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# WhatsApp message line pattern.
# U+202F NARROW NO-BREAK SPACE appears between the time and AM/PM.
MSG_PATTERN = re.compile(
    r'^\[(\d{1,2}/\d{1,2}/\d{2,4}), (\d{1,2}:\d{2}:\d{2})\u202f([AP]M)\] ([^:]+): (.*)$'
)

CYCLING_KEYWORDS = [
    'strava', 'ridewithgps', 'brevet', 'randonneuring', 'cassette',
    'saddle', 'elevation', 'miles', 'km', 'psi', 'tire', 'tyre', 'chain',
    'lights', 'route', 'control', 'acp', 'rusa', 'cycling', 'cyclist',
    'bike', 'ride', 'riding', 'nutrition', 'gel', 'segment', 'training',
    'everesting', 'garmin', 'wahoo', 'century', 'climb',
    'descent', 'cue sheet', 'jersey', 'tubeless', 'flat tire', 'gravel',
    'watt', 'power meter', 'dnf', 'dns', 'finish', 'checkpoint',
]

MEDIA_SKIP_PATTERNS = [
    'image omitted', 'video omitted', 'audio omitted',
    'sticker omitted', 'file attached', 'document omitted',
]


# ---------------------------------------------------------------------------
# 1. parse_export
# ---------------------------------------------------------------------------

def parse_export(filepath):
    """Parse a WhatsApp .txt export file into a list of message dicts.

    Each message dict contains:
        ts (datetime): Message timestamp.
        sender (str): Sender name.
        content (str): Message body (multiline messages concatenated with newlines).
        is_system (bool): True if message is a system notification (U+200E prefix).

    Handles both 2-digit and 4-digit year formats in timestamps.
    Continuation lines (no timestamp prefix) are appended to the current
    message's content with a newline separator.

    Args:
        filepath: Path to the WhatsApp .txt export file.

    Returns:
        List of message dicts, in chronological order.
    """
    messages = []
    current = None

    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.rstrip()
            m = MSG_PATTERN.match(line)
            if m:
                if current:
                    messages.append(current)
                date_str = m.group(1)
                time_str = m.group(2) + ' ' + m.group(3)
                try:
                    ts = datetime.strptime(
                        f'{date_str} {time_str}', '%m/%d/%y %I:%M:%S %p'
                    )
                except ValueError:
                    ts = datetime.strptime(
                        f'{date_str} {time_str}', '%m/%d/%Y %I:%M:%S %p'
                    )
                content = m.group(5)
                is_system = '\u200e' in content
                current = {
                    'ts': ts,
                    'sender': m.group(4).strip(),
                    'content': content,
                    'is_system': is_system,
                }
            elif current:
                current['content'] += '\n' + line
        if current:
            messages.append(current)

    return messages


# ---------------------------------------------------------------------------
# 2. chunk_by_time_window
# ---------------------------------------------------------------------------

def chunk_by_time_window(messages, window_minutes=30):
    """Group messages into time-window chunks, excluding system messages.

    Filters out system messages first, then groups remaining messages such
    that a new chunk starts whenever the gap from the chunk's start timestamp
    exceeds ``window_minutes``.

    Args:
        messages: List of message dicts from parse_export().
        window_minutes: Maximum time span in minutes for one chunk (default 30).

    Returns:
        List of chunks, where each chunk is a list of message dicts.
    """
    real_msgs = [m for m in messages if not m['is_system']]
    if not real_msgs:
        return []

    chunks = []
    current_chunk = []
    window_start = None

    for msg in real_msgs:
        if not current_chunk:
            current_chunk.append(msg)
            window_start = msg['ts']
        elif (msg['ts'] - window_start).total_seconds() <= window_minutes * 60:
            current_chunk.append(msg)
        else:
            chunks.append(current_chunk)
            current_chunk = [msg]
            window_start = msg['ts']

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


# ---------------------------------------------------------------------------
# 3. is_cycling_chunk_rule
# ---------------------------------------------------------------------------

def is_cycling_chunk_rule(chunk_text):
    """Stage 1 rule-based filter for cycling-relevant content.

    Returns False if:
        - Any MEDIA_SKIP_PATTERNS are found in the text.
        - The text is shorter than 20 characters (too short to be meaningful).
        - No CYCLING_KEYWORDS are present.

    Returns True only if at least one cycling keyword is found and no
    skip conditions are met.

    Args:
        chunk_text: The text content of a chunk.

    Returns:
        True if chunk is cycling-relevant by keyword match, False otherwise.
    """
    lower = chunk_text.lower()
    if any(p in lower for p in MEDIA_SKIP_PATTERNS):
        return False
    if len(chunk_text.strip()) < 20:
        return False
    return any(kw in lower for kw in CYCLING_KEYWORDS)


# ---------------------------------------------------------------------------
# 4. classify_chunks_llm
# ---------------------------------------------------------------------------

def classify_chunks_llm(chunk_texts, client, batch_size=50, model="gpt-4o-mini"):
    """Stage 2 LLM batch classification for cycling-relevant content.

    Sends chunks in batches to the specified model and asks for a JSON
    classification of each chunk as relevant or noise.

    Fail-open behavior: On ANY exception (API error, JSON parse error, etc.),
    returns ALL input chunks unchanged. Data is never discarded on error.

    Args:
        chunk_texts: List of chunk text strings to classify.
        client: An OpenAI client instance (caller provides this).
        batch_size: Number of chunks per API call (default 50).
        model: Model name for classification (default "gpt-4o-mini").

    Returns:
        List of chunk texts classified as relevant. On error, returns all
        input chunks unchanged.
    """
    if not chunk_texts:
        return []

    relevant = []

    try:
        for i in range(0, len(chunk_texts), batch_size):
            batch = chunk_texts[i:i + batch_size]

            # Format chunks with numbered delimiters for the LLM
            formatted_chunks = []
            for idx, text in enumerate(batch):
                formatted_chunks.append(f"---CHUNK {idx + 1}---\n{text}")
            chunks_block = "\n\n".join(formatted_chunks)

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Classify each chunk as 'relevant' (cycling, randonneuring, "
                            "bike maintenance, training, nutrition, route planning, gear) "
                            "or 'noise' (social chat, off-topic, greetings only). "
                            "Respond with a JSON object containing a 'results' array of "
                            "booleans, one per chunk, where true = relevant."
                        ),
                    },
                    {
                        "role": "user",
                        "content": chunks_block,
                    },
                ],
                response_format={"type": "json_object"},
            )

            result_json = json.loads(response.choices[0].message.content)
            classifications = result_json.get("results", [])

            for text, is_relevant in zip(batch, classifications):
                if is_relevant:
                    relevant.append(text)

    except Exception as e:
        logger.warning("LLM chunk classification failed: %s. Returning all chunks.", e)
        return list(chunk_texts)

    return relevant


# ---------------------------------------------------------------------------
# 5. format_chunk_content
# ---------------------------------------------------------------------------

def format_chunk_content(chunk_messages):
    """Format a list of message dicts as readable timestamped text.

    Each message is formatted as ``[YYYY-MM-DD HH:MM] Sender: content``.
    Messages containing media-omitted patterns are skipped.
    URLs in message content are preserved without modification.

    Args:
        chunk_messages: List of message dicts (with ts, sender, content keys).

    Returns:
        Formatted string with one message per line, joined by newlines.
    """
    lines = []
    for msg in chunk_messages:
        content = msg['content'].strip()
        if any(p in content.lower() for p in MEDIA_SKIP_PATTERNS):
            continue
        ts_str = msg['ts'].strftime('%Y-%m-%d %H:%M')
        lines.append(f"[{ts_str}] {msg['sender']}: {content}")
    return '\n'.join(lines)
