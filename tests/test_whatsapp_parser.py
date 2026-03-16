"""Unit tests for WhatsApp export parser, chunker, rule filter, LLM classifier, and formatter."""
import json
import sys
import os
from datetime import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# Ensure scripts directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from scripts.whatsapp_parser import (
    parse_export,
    chunk_by_time_window,
    is_cycling_chunk_rule,
    classify_chunks_llm,
    format_chunk_content,
)


# ---------------------------------------------------------------------------
# parse_export tests
# ---------------------------------------------------------------------------


def test_parse_single_message(tmp_path):
    """A single WhatsApp line parses into dict with correct fields."""
    export = tmp_path / "chat.txt"
    # U+202F (narrow no-break space) between time and AM
    export.write_text(
        "[3/15/26, 10:30:00\u202fAM] John: Hello\n",
        encoding="utf-8",
    )
    msgs = parse_export(str(export))
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg["ts"] == datetime(2026, 3, 15, 10, 30, 0)
    assert msg["sender"] == "John"
    assert msg["content"] == "Hello"
    assert msg["is_system"] is False


def test_parse_unicode_timestamp(tmp_path):
    """U+202F narrow no-break space between time and AM/PM matches correctly."""
    export = tmp_path / "chat.txt"
    export.write_text(
        "[1/5/26, 8:15:30\u202fPM] Alice: Good evening\n",
        encoding="utf-8",
    )
    msgs = parse_export(str(export))
    assert len(msgs) > 0, "Parser must handle U+202F and not return 0 messages"
    assert msgs[0]["ts"] == datetime(2026, 1, 5, 20, 15, 30)


def test_parse_multiline_message(tmp_path):
    """Continuation lines (no timestamp prefix) concatenate into content."""
    export = tmp_path / "chat.txt"
    export.write_text(
        "[3/15/26, 10:30:00\u202fAM] John: Line one\n"
        "Line two\n"
        "Line three\n",
        encoding="utf-8",
    )
    msgs = parse_export(str(export))
    assert len(msgs) == 1
    assert "Line one\nLine two\nLine three" == msgs[0]["content"]


def test_system_message_detection(tmp_path):
    """A line whose content starts with U+200E has is_system=True."""
    export = tmp_path / "chat.txt"
    export.write_text(
        "[3/15/26, 10:30:00\u202fAM] System: \u200eJohn created this group\n",
        encoding="utf-8",
    )
    msgs = parse_export(str(export))
    assert len(msgs) == 1
    assert msgs[0]["is_system"] is True


def test_parse_multiple_messages(tmp_path):
    """Three sequential messages parse into list of 3 dicts in order."""
    export = tmp_path / "chat.txt"
    export.write_text(
        "[3/15/26, 10:30:00\u202fAM] Alice: First\n"
        "[3/15/26, 10:31:00\u202fAM] Bob: Second\n"
        "[3/15/26, 10:32:00\u202fAM] Charlie: Third\n",
        encoding="utf-8",
    )
    msgs = parse_export(str(export))
    assert len(msgs) == 3
    assert msgs[0]["sender"] == "Alice"
    assert msgs[1]["sender"] == "Bob"
    assert msgs[2]["sender"] == "Charlie"


# ---------------------------------------------------------------------------
# chunk_by_time_window tests
# ---------------------------------------------------------------------------


def _make_msg(ts, sender="User", content="Hello", is_system=False):
    """Helper to build a message dict."""
    return {"ts": ts, "sender": sender, "content": content, "is_system": is_system}


def test_chunk_single_window():
    """5 messages all within 10 minutes produce exactly 1 chunk."""
    base = datetime(2026, 3, 15, 10, 0, 0)
    msgs = [
        _make_msg(datetime(2026, 3, 15, 10, i, 0)) for i in range(5)
    ]
    chunks = chunk_by_time_window(msgs, window_minutes=30)
    assert len(chunks) == 1
    assert len(chunks[0]) == 5


def test_chunk_boundary():
    """A 45-minute gap between msg 3 and msg 4 produces 2 chunks."""
    msgs = [
        _make_msg(datetime(2026, 3, 15, 10, 0, 0)),
        _make_msg(datetime(2026, 3, 15, 10, 5, 0)),
        _make_msg(datetime(2026, 3, 15, 10, 10, 0)),
        # 45-minute gap
        _make_msg(datetime(2026, 3, 15, 10, 55, 0)),
        _make_msg(datetime(2026, 3, 15, 11, 0, 0)),
    ]
    chunks = chunk_by_time_window(msgs, window_minutes=30)
    assert len(chunks) == 2
    assert len(chunks[0]) == 3
    assert len(chunks[1]) == 2


def test_chunk_excludes_system():
    """System messages are filtered out before chunking."""
    msgs = [
        _make_msg(datetime(2026, 3, 15, 10, 0, 0), is_system=True),
        _make_msg(datetime(2026, 3, 15, 10, 1, 0)),
        _make_msg(datetime(2026, 3, 15, 10, 2, 0), is_system=True),
        _make_msg(datetime(2026, 3, 15, 10, 3, 0)),
        _make_msg(datetime(2026, 3, 15, 10, 4, 0)),
    ]
    chunks = chunk_by_time_window(msgs, window_minutes=30)
    total_msgs = sum(len(c) for c in chunks)
    assert total_msgs == 3  # only the 3 non-system messages


# ---------------------------------------------------------------------------
# is_cycling_chunk_rule tests
# ---------------------------------------------------------------------------


def test_filter_media_skip():
    """A chunk whose only content is 'image omitted' returns False."""
    assert is_cycling_chunk_rule("image omitted") is False


def test_filter_too_short():
    """A chunk with content under 20 characters returns False."""
    assert is_cycling_chunk_rule("hi there") is False


def test_filter_cycling_keyword():
    """A chunk containing 'brevet' returns True."""
    text = "We should sign up for the next brevet distance 200km"
    assert is_cycling_chunk_rule(text) is True


def test_filter_no_keyword():
    """A chunk about cooking dinner with no cycling keywords returns False."""
    text = "I made a great pasta dinner last night with mushrooms and cream sauce"
    assert is_cycling_chunk_rule(text) is False


# ---------------------------------------------------------------------------
# format_chunk_content tests
# ---------------------------------------------------------------------------


def test_format_chunk():
    """format_chunk_content produces '[YYYY-MM-DD HH:MM] Sender: content' lines."""
    msgs = [
        _make_msg(datetime(2026, 3, 15, 10, 30, 0), "Alice", "Hello everyone"),
        _make_msg(datetime(2026, 3, 15, 10, 31, 0), "Bob", "Hi Alice"),
    ]
    result = format_chunk_content(msgs)
    assert "[2026-03-15 10:30] Alice: Hello everyone" in result
    assert "[2026-03-15 10:31] Bob: Hi Alice" in result


def test_format_preserves_urls():
    """URLs are preserved in formatted output."""
    msgs = [
        _make_msg(
            datetime(2026, 3, 15, 10, 30, 0),
            "Venki",
            "Check this route https://www.strava.com/activities/12345 it was great",
        ),
    ]
    result = format_chunk_content(msgs)
    assert "https://www.strava.com/activities/12345" in result


# ---------------------------------------------------------------------------
# classify_chunks_llm tests
# ---------------------------------------------------------------------------


def test_classify_chunks_llm_returns_filtered():
    """classify_chunks_llm returns only chunks classified as relevant."""
    chunks = ["brevet discussion about nutrition", "random social chat hello", "bike maintenance tips"]

    # Mock OpenAI client: first call classifies 3 chunks
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({"results": [True, False, True]})
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_response

    result = classify_chunks_llm(chunks, mock_client, batch_size=50)
    assert len(result) == 2
    assert result[0] == "brevet discussion about nutrition"
    assert result[1] == "bike maintenance tips"


def test_classify_chunks_llm_batching():
    """classify_chunks_llm with 150 chunks calls the API in multiple batches."""
    chunks = [f"chunk {i} about cycling brevet" for i in range(150)]

    mock_client = MagicMock()

    def create_response(**kwargs):
        msgs = kwargs.get("messages", [])
        # Find the user message to determine batch size
        user_msg = [m for m in msgs if m["role"] == "user"][0]["content"]
        # Count chunks in the batch by counting "---CHUNK" delimiters
        chunk_count = user_msg.count("---CHUNK")
        resp = MagicMock()
        choice = MagicMock()
        choice.message.content = json.dumps({"results": [True] * chunk_count})
        resp.choices = [choice]
        return resp

    mock_client.chat.completions.create.side_effect = create_response

    result = classify_chunks_llm(chunks, mock_client, batch_size=50)
    # With 150 chunks and batch_size=50, expect 3 API calls
    assert mock_client.chat.completions.create.call_count == 3
    assert len(result) == 150


def test_classify_chunks_llm_error_returns_all():
    """When the OpenAI API call raises, classify_chunks_llm returns all input chunks unchanged."""
    chunks = ["chunk 1", "chunk 2", "chunk 3"]

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("API error")

    result = classify_chunks_llm(chunks, mock_client, batch_size=50)
    assert result == chunks  # fail-open: return all
