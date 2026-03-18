"""
Test scaffolds for personality extraction logic (Phase 8, Plans 02 and 03).

Unit tests: run immediately (will RED until Plans 02/03 create implementations).
Integration tests: marked @pytest.mark.integration — require a live DB.

Import paths:
  - scripts.personality_helpers — group_by_sender, compute_confidence,
      merge_profiles, extract_from_messages, PersonalityExtraction, TraitEvidence
  - scripts.extract_personality_blog — fetch_blog_text, extract_pdf_text
"""

import pytest
import sys
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Imports from extraction scripts (will fail RED until Plans 02/03 implement)
# ---------------------------------------------------------------------------
from scripts.personality_helpers import (
    PersonalityExtraction,
    TraitEvidence,
    group_by_sender,
    compute_confidence,
    merge_profiles,
    extract_from_messages,
)
from scripts.extract_personality_blog import fetch_blog_text, extract_pdf_text


# ===========================================================================
# Unit tests — no database, no external services
# ===========================================================================


def test_extraction_model_fields():
    """PersonalityExtraction Pydantic model must expose all 8 required fields."""
    fields = PersonalityExtraction.model_fields
    required = [
        'tone',
        'humor_type',
        'directness',
        'encouragement_style',
        'domain_bias',
        'response_length_tendency',
        'question_asking_behavior',
        'signature_phrases',
    ]
    missing = [f for f in required if f not in fields]
    assert not missing, f"PersonalityExtraction missing fields: {missing}"

    # TraitEvidence should also have evidence fields
    evidence_fields = TraitEvidence.model_fields
    assert 'trait_name' in evidence_fields, "TraitEvidence missing 'trait_name'"
    assert 'source_quote' in evidence_fields, "TraitEvidence missing 'source_quote'"


def test_group_by_sender_filters_noise():
    """group_by_sender() must filter noise: system msgs, media skips, short reactions, URL-only."""
    now = datetime(2024, 1, 1, 10, 0, 0)
    messages = [
        # Should be kept — real content from Venki
        {'sender': 'Venki', 'content': 'The brevet was incredible, totally worth the effort.', 'is_system': False, 'ts': now},
        # Should be filtered — system message
        {'sender': 'Venki', 'content': 'Venki added you', 'is_system': True, 'ts': now},
        # Should be filtered — media skip pattern
        {'sender': 'Venki', 'content': '<Media omitted>', 'is_system': False, 'ts': now},
        # Should be filtered — short reaction (< 3 words)
        {'sender': 'Venki', 'content': 'Haha', 'is_system': False, 'ts': now},
        # Should be filtered — URL-only message
        {'sender': 'Venki', 'content': 'https://strava.com/activities/12345678', 'is_system': False, 'ts': now},
        # Should be kept — different sender but real content
        {'sender': 'Shriram', 'content': 'Great ride, the climb was tough but we made it!', 'is_system': False, 'ts': now},
        # Should be filtered — another media pattern
        {'sender': 'Venki', 'content': 'image omitted', 'is_system': False, 'ts': now},
        # Should be filtered — two-word message
        {'sender': 'Venki', 'content': 'Good job', 'is_system': False, 'ts': now},
    ]

    result = group_by_sender(messages)

    # Venki should have 1 qualifying message
    assert 'Venki' in result, "Venki should appear in result"
    venki_msgs = result['Venki']
    assert len(venki_msgs) == 1, f"Expected 1 Venki message, got {len(venki_msgs)}"
    assert 'brevet was incredible' in venki_msgs[0]['content']

    # Shriram should have 1 qualifying message
    assert 'Shriram' in result, "Shriram should appear in result"
    assert len(result['Shriram']) == 1


def test_compute_confidence():
    """compute_confidence() returns 'high' >=50, 'medium' 20-49, 'low' <20."""
    assert compute_confidence(50) == 'high', "50 messages should be 'high'"
    assert compute_confidence(100) == 'high', "100 messages should be 'high'"
    assert compute_confidence(49) == 'medium', "49 messages should be 'medium'"
    assert compute_confidence(20) == 'medium', "20 messages should be 'medium'"
    assert compute_confidence(19) == 'low', "19 messages should be 'low'"
    assert compute_confidence(0) == 'low', "0 messages should be 'low'"


def test_extract_from_messages_returns_model():
    """extract_from_messages() calls OpenAI parse() and returns PersonalityExtraction."""
    # Build a valid PersonalityExtraction result to return from the mock
    fake_result = PersonalityExtraction(
        tone='warm',
        humor_type='gentle',
        directness='medium',
        encouragement_style='balanced',
        domain_bias='mountain climbing',
        response_length_tendency='moderate',
        question_asking_behavior='sometimes',
        signature_phrases=['Great effort!', 'Keep pushing!'],
    )

    # Mock the OpenAI client
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.parsed = fake_result
    mock_client.chat.completions.parse.return_value = mock_response

    messages = [
        {'sender': 'Venki', 'content': 'The brevet was incredible, totally worth it.', 'is_system': False, 'ts': datetime(2024, 1, 1)},
        {'sender': 'Venki', 'content': 'Keep pushing on those climbs, you will get there!', 'is_system': False, 'ts': datetime(2024, 1, 2)},
    ]

    result = extract_from_messages(mock_client, messages, 'Venki')

    assert isinstance(result, PersonalityExtraction), (
        f"Expected PersonalityExtraction, got {type(result)}"
    )
    # Verify parse() was called with correct parameters
    call_kwargs = mock_client.chat.completions.parse.call_args[1]
    assert call_kwargs.get('response_format') is PersonalityExtraction, (
        "response_format must be PersonalityExtraction"
    )
    assert call_kwargs.get('temperature') == 0, "temperature must be 0"


def test_merge_profiles_blog_wins():
    """merge_profiles() uses blog value for enum fields; unions phrases capped at 5; lower confidence."""
    whatsapp_row = {
        'tone': 'direct',
        'humor_type': 'dry',
        'directness': 'high',
        'encouragement_style': 'tough-love',
        'domain_bias': 'endurance',
        'response_length_tendency': 'brief',
        'question_asking_behavior': 'rarely',
        'signature_phrases': ['Let us go!', 'No excuses.', 'Pain is temporary.'],
        'extraction_confidence': 'high',
        'extraction_source': 'whatsapp',
    }
    blog_row = {
        'tone': 'warm',
        'humor_type': 'gentle',
        'directness': 'medium',
        'encouragement_style': 'balanced',
        'domain_bias': 'mountain climbing',
        'response_length_tendency': 'moderate',
        'question_asking_behavior': 'sometimes',
        'signature_phrases': ['Great effort!', 'Keep pushing!', 'You have got this!'],
        'extraction_confidence': 'medium',
        'extraction_source': 'blog',
    }

    merged = merge_profiles(whatsapp_row, blog_row)

    # Blog wins on all enum fields when both sources present
    assert merged['tone'] == 'warm', "Blog tone should win"
    assert merged['humor_type'] == 'gentle', "Blog humor_type should win"
    assert merged['directness'] == 'medium', "Blog directness should win"
    assert merged['encouragement_style'] == 'balanced', "Blog encouragement_style should win"
    assert merged['response_length_tendency'] == 'moderate', "Blog response_length_tendency should win"
    assert merged['question_asking_behavior'] == 'sometimes', "Blog question_asking_behavior should win"

    # Signature phrases should be union, capped at 5
    all_phrases = set(whatsapp_row['signature_phrases']) | set(blog_row['signature_phrases'])
    assert len(merged['signature_phrases']) <= 5, "Merged phrases must be capped at 5"
    # All merged phrases should be from the union of both sources
    for phrase in merged['signature_phrases']:
        assert phrase in all_phrases, f"Unexpected phrase: {phrase}"

    # Merged confidence should be the lower of the two ('medium' < 'high')
    assert merged['extraction_confidence'] == 'medium', "Lower confidence should win"

    # extraction_source should be 'merged'
    assert merged['extraction_source'] == 'merged', "Merged profile should have source='merged'"


def test_fetch_blog_text_from_url():
    """fetch_blog_text() calls trafilatura and returns extracted text."""
    fake_html = "<html><body><p>Great brevet report from Venki.</p></body></html>"
    fake_text = "Great brevet report from Venki."

    with patch('scripts.extract_personality_blog.trafilatura.fetch_url', return_value=fake_html) as mock_fetch, \
         patch('scripts.extract_personality_blog.trafilatura.extract', return_value=fake_text) as mock_extract:

        result = fetch_blog_text('https://venki.example.com/brevet-2024')

        mock_fetch.assert_called_once_with('https://venki.example.com/brevet-2024')
        mock_extract.assert_called_once_with(fake_html)
        assert result == fake_text, f"Expected extracted text, got: {result}"


def test_extract_pdf_text():
    """extract_pdf_text() reads PDF pages and returns concatenated text."""
    fake_page_1 = MagicMock()
    fake_page_1.extract_text.return_value = "Page 1: Brevet report begins here."
    fake_page_2 = MagicMock()
    fake_page_2.extract_text.return_value = "Page 2: Continued riding through the night."

    mock_pdf = MagicMock()
    mock_pdf.pages = [fake_page_1, fake_page_2]
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)

    with patch('scripts.extract_personality_blog.pdfplumber.open', return_value=mock_pdf):
        result = extract_pdf_text('/tmp/test_brevet_report.pdf')

    assert 'Page 1' in result, "Page 1 text should appear in output"
    assert 'Page 2' in result, "Page 2 text should appear in output"
    assert 'Brevet report begins here' in result
    assert 'riding through the night' in result


# ===========================================================================
# Integration tests — require live database, run with: pytest -m integration
# ===========================================================================


@pytest.mark.integration
def test_evidence_quotes_stored():
    """After extraction, personality_trait_evidence has 3-5 rows per trait per rider."""
    # Stub: full implementation in Plan 02 (whatsapp extraction)
    # When Plans 02/03 are implemented, this test should:
    #   1. Run extract_from_messages() against a real DB fixture rider
    #   2. Query personality_trait_evidence WHERE rider_id = fixture_id
    #   3. Group by trait_name and assert each group has 3-5 rows
    pytest.skip("Integration stub — implement after Plan 02 creates evidence storage")


@pytest.mark.integration
def test_migration_012_columns():
    """personality_profile has response_length_tendency, question_asking_behavior, domain_bias."""
    # Stub: verifies migration 012 was applied to the database
    # Implementation should:
    #   1. Connect to DB using test DATABASE_URL
    #   2. Query information_schema.columns for personality_profile
    #   3. Assert all 3 new columns exist with correct types
    pytest.skip("Integration stub — requires live DB with migration 012 applied")


@pytest.mark.integration
def test_trait_evidence_schema():
    """personality_trait_evidence table exists with correct columns."""
    # Stub: verifies the evidence table schema matches migration 012
    # Implementation should:
    #   1. Connect to DB using test DATABASE_URL
    #   2. Query information_schema.columns for personality_trait_evidence
    #   3. Assert id, rider_id, trait_name, source_quote, extraction_source, created_at, updated_at
    pytest.skip("Integration stub — requires live DB with migration 012 applied")
