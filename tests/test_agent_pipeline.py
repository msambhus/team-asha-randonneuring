"""Tests for agentic pipeline — intent classification with Pydantic IntentResult model."""
import pytest
from unittest.mock import patch, MagicMock
from pydantic import ValidationError


def _mock_parse_response(parsed_result, prompt_tokens=50, completion_tokens=10):
    """Helper to build a mock parse() response."""
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = prompt_tokens
    mock_usage.completion_tokens = completion_tokens

    mock_message = MagicMock()
    mock_message.parsed = parsed_result

    mock_choice = MagicMock()
    mock_choice.message = mock_message

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage
    return mock_response


def test_classify_intent_data_query(app):
    """data_query intent returns IntentResult with valid query_type."""
    with app.app_context():
        from services.chat_service import classify_intent, IntentResult

        intent_obj = IntentResult(intent='data_query', query_type='fitness_score')
        mock_response = _mock_parse_response(intent_obj)

        mock_client = MagicMock()
        mock_client.chat.completions.parse.return_value = mock_response

        result, usage = classify_intent(mock_client, "What's my fitness score?", [])
        assert result.intent == 'data_query'
        assert result.query_type == 'fitness_score'
        assert usage.prompt_tokens == 50


def test_classify_intent_off_topic(app):
    """off_topic intent returns IntentResult with no query_type."""
    with app.app_context():
        from services.chat_service import classify_intent, IntentResult

        intent_obj = IntentResult(intent='off_topic')
        mock_response = _mock_parse_response(intent_obj)

        mock_client = MagicMock()
        mock_client.chat.completions.parse.return_value = mock_response

        result, usage = classify_intent(mock_client, "Who won the World Cup?", [])
        assert result.intent == 'off_topic'
        assert result.query_type is None
        assert result.ride_name is None


def test_classify_intent_route_discussion(app):
    """route_discussion intent returns IntentResult with ride_name set."""
    with app.app_context():
        from services.chat_service import classify_intent, IntentResult

        intent_obj = IntentResult(intent='route_discussion', ride_name='Cascade 400')
        mock_response = _mock_parse_response(intent_obj)

        mock_client = MagicMock()
        mock_client.chat.completions.parse.return_value = mock_response

        result, usage = classify_intent(mock_client, "Tell me about the Cascade 400 route", [])
        assert result.intent == 'route_discussion'
        assert result.ride_name == 'Cascade 400'


def test_classify_intent_coaching(app):
    """coaching intent classified correctly."""
    with app.app_context():
        from services.chat_service import classify_intent, IntentResult

        intent_obj = IntentResult(intent='coaching')
        mock_response = _mock_parse_response(intent_obj)

        mock_client = MagicMock()
        mock_client.chat.completions.parse.return_value = mock_response

        result, usage = classify_intent(mock_client, "How should I train for my next 400km?", [])
        assert result.intent == 'coaching'


def test_classify_intent_knowledge(app):
    """knowledge intent classified correctly."""
    with app.app_context():
        from services.chat_service import classify_intent, IntentResult

        intent_obj = IntentResult(intent='knowledge')
        mock_response = _mock_parse_response(intent_obj)

        mock_client = MagicMock()
        mock_client.chat.completions.parse.return_value = mock_response

        result, usage = classify_intent(mock_client, "What are the ACP time limits?", [])
        assert result.intent == 'knowledge'


def test_classify_intent_refusal_returns_off_topic(app):
    """When parse() returns None (refusal), classify_intent returns off_topic."""
    with app.app_context():
        from services.chat_service import classify_intent

        mock_response = _mock_parse_response(None)

        mock_client = MagicMock()
        mock_client.chat.completions.parse.return_value = mock_response

        result, usage = classify_intent(mock_client, "something problematic", [])
        assert result.intent == 'off_topic'
        assert result.query_type is None


def test_intent_result_validates_literal(app):
    """IntentResult only accepts the 5 valid intent values."""
    with app.app_context():
        from services.chat_service import IntentResult

        # Valid intents
        for intent in ['data_query', 'coaching', 'knowledge', 'route_discussion', 'off_topic']:
            obj = IntentResult(intent=intent)
            assert obj.intent == intent

        # Invalid intent
        with pytest.raises(ValidationError):
            IntentResult(intent='invalid_intent')


def test_intent_result_optional_fields(app):
    """query_type and ride_name default to None."""
    with app.app_context():
        from services.chat_service import IntentResult

        obj = IntentResult(intent='coaching')
        assert obj.query_type is None
        assert obj.ride_name is None


def test_classify_intent_uses_gpt4o_mini(app):
    """classify_intent uses gpt-4o-mini model for fast, cheap classification."""
    with app.app_context():
        from services.chat_service import classify_intent, IntentResult

        intent_obj = IntentResult(intent='coaching')
        mock_response = _mock_parse_response(intent_obj)

        mock_client = MagicMock()
        mock_client.chat.completions.parse.return_value = mock_response

        classify_intent(mock_client, "test message", [])

        call_kwargs = mock_client.chat.completions.parse.call_args[1]
        assert call_kwargs['model'] == 'gpt-4o-mini'
        assert call_kwargs['max_tokens'] == 200
