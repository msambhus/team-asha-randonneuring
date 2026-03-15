"""Tests for chat service — moderation, message construction, streaming, error handling."""
import pytest
from unittest.mock import patch, MagicMock


def test_moderation_blocks(app):
    """Flagged content is blocked before LLM call."""
    with app.app_context():
        from services.chat_service import moderate_input

        mock_result = MagicMock()
        mock_result.results = [MagicMock(flagged=True, categories=MagicMock())]
        with patch('services.chat_service._get_client') as mock_client:
            mock_client.return_value.moderations.create.return_value = mock_result
            assert moderate_input('bad content') is False


def test_moderation_passes(app):
    """Safe content passes moderation."""
    with app.app_context():
        from services.chat_service import moderate_input

        mock_result = MagicMock()
        mock_result.results = [MagicMock(flagged=False)]
        with patch('services.chat_service._get_client') as mock_client:
            mock_client.return_value.moderations.create.return_value = mock_result
            assert moderate_input('good content') is True


def test_moderation_api_failure(app):
    """Moderation API failure fails closed (returns False)."""
    with app.app_context():
        from services.chat_service import moderate_input

        with patch('services.chat_service._get_client') as mock_client:
            mock_client.return_value.moderations.create.side_effect = Exception('API down')
            assert moderate_input('anything') is False


def test_message_construction(app):
    """build_messages puts system first, history in order, user last."""
    with app.app_context():
        from services.chat_service import build_messages

        history = [
            {'role': 'user', 'content': 'previous question'},
            {'role': 'assistant', 'content': 'previous answer'},
        ]
        result = build_messages('new question', history, 'You are a cycling coach.')

        assert result[0]['role'] == 'system'
        assert result[0]['content'] == 'You are a cycling coach.'
        assert result[1] == {'role': 'user', 'content': 'previous question'}
        assert result[2] == {'role': 'assistant', 'content': 'previous answer'}
        assert result[-1] == {'role': 'user', 'content': 'new question'}
        # User content must NOT appear in system prompt
        assert 'new question' not in result[0]['content']


def test_max_tokens_set(app):
    """Streaming call sets max_tokens <= 800, stream=True, timeout."""
    with app.app_context():
        from services.chat_service import _stream_completion

        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))

        with patch('services.chat_service._get_client') as mock_client:
            mock_client.return_value.chat.completions.create.return_value = mock_stream
            accumulator = {}
            gen = _stream_completion([{'role': 'user', 'content': 'test'}], accumulator)
            list(gen)  # exhaust the generator

            call_kwargs = mock_client.return_value.chat.completions.create.call_args[1]
            assert call_kwargs['max_tokens'] <= 800
            assert call_kwargs['stream'] is True
            assert call_kwargs.get('timeout', 60) <= 50


def test_rate_limit_error(app):
    """RateLimitError yields specific user-friendly message."""
    with app.app_context():
        from services.chat_service import _stream_completion
        from openai import RateLimitError

        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {}

        with patch('services.chat_service._get_client') as mock_client:
            mock_client.return_value.chat.completions.create.side_effect = RateLimitError(
                message='rate limited', response=mock_resp, body=None
            )
            accumulator = {}
            chunks = list(_stream_completion([{'role': 'user', 'content': 'test'}], accumulator))
            combined = ''.join(chunks)
            assert 'too many requests' in combined.lower()


def test_api_timeout_error(app):
    """APITimeoutError yields specific user-friendly message."""
    with app.app_context():
        from services.chat_service import _stream_completion
        from openai import APITimeoutError

        with patch('services.chat_service._get_client') as mock_client:
            mock_client.return_value.chat.completions.create.side_effect = APITimeoutError(
                request=MagicMock()
            )
            accumulator = {}
            chunks = list(_stream_completion([{'role': 'user', 'content': 'test'}], accumulator))
            combined = ''.join(chunks)
            assert 'took too long' in combined.lower()


def test_internal_server_error(app):
    """InternalServerError yields specific user-friendly message."""
    with app.app_context():
        from services.chat_service import _stream_completion
        from openai import InternalServerError

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.headers = {}

        with patch('services.chat_service._get_client') as mock_client:
            mock_client.return_value.chat.completions.create.side_effect = InternalServerError(
                message='server error', response=mock_resp, body=None
            )
            accumulator = {}
            chunks = list(_stream_completion([{'role': 'user', 'content': 'test'}], accumulator))
            combined = ''.join(chunks)
            assert 'temporary issue' in combined.lower()


def test_cross_user_conversation_rejected(app):
    """process_message rejects conversation_id belonging to a different user."""
    with app.app_context():
        from services.chat_service import process_message

        # Mock moderate_input to pass, get_conversation to return None (wrong user)
        with patch('services.chat_service.moderate_input', return_value=True), \
             patch('services.chat_service.models') as mock_models:
            mock_models.get_conversation.return_value = None
            chunks = list(process_message(
                user_id=999,
                message='Hello',
                conversation_id='some-conversation-id'
            ))
            combined = ''.join(chunks)
            assert 'error' in combined.lower()
            # Should NOT have called create or insert
            mock_models.create_conversation.assert_not_called()
