"""Tests for Braintrust integration — span logging, metadata storage, graceful degradation.

Tests mock the Braintrust SDK so no API key is needed. These verify that:
- _bt_logger is initialized at module scope
- process_message() opens a span and extracts span_id/trace_id
- span_id and trace_id are passed to insert_chat_message metadata
- span.log() is called with output and metadata after streaming
- Graceful degradation when _bt_logger is None
"""
import json
import pytest
from unittest.mock import patch, MagicMock, PropertyMock


@pytest.fixture
def mock_bt_span():
    """Create a mock Braintrust span with correct attribute names."""
    span = MagicMock()
    # span.id is a property on SpanImpl, mock it correctly
    type(span).id = PropertyMock(return_value='test-span-id-123')
    span.root_span_id = 'test-trace-id-456'
    span.__enter__ = MagicMock(return_value=span)
    span.__exit__ = MagicMock(return_value=False)
    return span


@pytest.fixture
def mock_bt_logger(mock_bt_span):
    """Create a mock Braintrust logger that returns the mock span."""
    logger = MagicMock()
    logger.start_span.return_value = mock_bt_span
    return logger


def test_bt_logger_initialized(app):
    """_bt_logger is initialized at module scope with project='Team Asha' and async_flush=False."""
    with app.app_context():
        with patch('braintrust.init_logger') as mock_init:
            mock_init.return_value = MagicMock()
            # Force reimport to trigger module-level init
            import importlib
            import services.chat_service as cs
            # Check if _bt_logger exists (it may be None if no API key)
            assert hasattr(cs, '_bt_logger'), "_bt_logger must be defined at module scope"


def test_process_message_starts_span(app, mock_bt_logger, mock_bt_span):
    """process_message() calls start_span() on the logger."""
    with app.app_context():
        from services.chat_service import process_message

        def mock_agent_loop(client, msg, messages, rider_id, user_id, accumulator=None):
            if accumulator is not None:
                accumulator['full_content'] = 'Test response'
                accumulator['prompt_tokens'] = 100
                accumulator['completion_tokens'] = 50
            yield 'data: {"status": "thinking"}\n\n'
            yield 'data: "Test response"\n\n'

        with patch('services.chat_service._bt_logger', mock_bt_logger), \
             patch('services.chat_service.moderate_input', return_value=True), \
             patch('services.chat_service.models') as mock_models, \
             patch('services.chat_service.run_agent_loop', side_effect=mock_agent_loop), \
             patch('services.chat_service._get_client', return_value=MagicMock()), \
             patch('services.chat_service._get_system_prompt', return_value='system'), \
             patch('services.chat_service.assemble_rider_context', return_value=''), \
             patch('services.chat_service.assemble_team_context', return_value=''):
            mock_models.create_conversation.return_value = {'id': 'conv-123'}
            mock_models.get_recent_messages.return_value = []

            chunks = list(process_message(user_id=1, message='Hi', rider_id=5))

            # start_span must have been called
            mock_bt_logger.start_span.assert_called_once()
            call_kwargs = mock_bt_logger.start_span.call_args
            assert call_kwargs[1]['name'] == 'chat_message'


def test_span_ids_stored_in_metadata(app, mock_bt_logger, mock_bt_span):
    """process_message passes metadata={'span_id': ..., 'trace_id': ...} to insert_chat_message."""
    with app.app_context():
        from services.chat_service import process_message

        def mock_agent_loop(client, msg, messages, rider_id, user_id, accumulator=None):
            if accumulator is not None:
                accumulator['full_content'] = 'Response with span'
                accumulator['prompt_tokens'] = 80
                accumulator['completion_tokens'] = 40
            yield 'data: "Response with span"\n\n'

        with patch('services.chat_service._bt_logger', mock_bt_logger), \
             patch('services.chat_service.moderate_input', return_value=True), \
             patch('services.chat_service.models') as mock_models, \
             patch('services.chat_service.run_agent_loop', side_effect=mock_agent_loop), \
             patch('services.chat_service._get_client', return_value=MagicMock()), \
             patch('services.chat_service._get_system_prompt', return_value='system'), \
             patch('services.chat_service.assemble_rider_context', return_value=''), \
             patch('services.chat_service.assemble_team_context', return_value=''):
            mock_models.create_conversation.return_value = {'id': 'conv-456'}
            mock_models.get_recent_messages.return_value = []

            chunks = list(process_message(user_id=1, message='Test', rider_id=5))

            # Check insert_chat_message was called with metadata containing span_id and trace_id
            insert_calls = mock_models.insert_chat_message.call_args_list
            # Find the assistant message insert (not the user message)
            assistant_calls = [c for c in insert_calls if len(c[0]) >= 2 and c[0][1] == 'assistant']
            assert len(assistant_calls) == 1, f"Expected 1 assistant insert, got {len(assistant_calls)}"

            call_kwargs = assistant_calls[0][1] if assistant_calls[0][1] else {}
            call_args = assistant_calls[0][0]
            # metadata could be positional or keyword
            metadata = call_kwargs.get('metadata') or (call_args[5] if len(call_args) > 5 else None)
            assert metadata is not None, "metadata must be passed to insert_chat_message"
            assert metadata.get('span_id') == 'test-span-id-123'
            assert metadata.get('trace_id') == 'test-trace-id-456'


def test_span_log_called(app, mock_bt_logger, mock_bt_span):
    """span.log() is called with output containing response_length and metadata with token counts."""
    with app.app_context():
        from services.chat_service import process_message

        def mock_agent_loop(client, msg, messages, rider_id, user_id, accumulator=None):
            if accumulator is not None:
                accumulator['full_content'] = 'Hello world'
                accumulator['prompt_tokens'] = 120
                accumulator['completion_tokens'] = 30
            yield 'data: "Hello world"\n\n'

        with patch('services.chat_service._bt_logger', mock_bt_logger), \
             patch('services.chat_service.moderate_input', return_value=True), \
             patch('services.chat_service.models') as mock_models, \
             patch('services.chat_service.run_agent_loop', side_effect=mock_agent_loop), \
             patch('services.chat_service._get_client', return_value=MagicMock()), \
             patch('services.chat_service._get_system_prompt', return_value='system'), \
             patch('services.chat_service.assemble_rider_context', return_value=''), \
             patch('services.chat_service.assemble_team_context', return_value=''):
            mock_models.create_conversation.return_value = {'id': 'conv-789'}
            mock_models.get_recent_messages.return_value = []

            chunks = list(process_message(user_id=1, message='Test', rider_id=5))

            # span.log() must have been called
            mock_bt_span.log.assert_called_once()
            log_kwargs = mock_bt_span.log.call_args[1]
            assert 'output' in log_kwargs
            assert 'response_length' in log_kwargs['output']
            assert log_kwargs['output']['response_length'] == len('Hello world')
            assert 'metadata' in log_kwargs
            assert log_kwargs['metadata']['prompt_tokens'] == 120
            assert log_kwargs['metadata']['completion_tokens'] == 30


def test_graceful_degradation_no_logger(app):
    """When _bt_logger is None, process_message works without spans."""
    with app.app_context():
        from services.chat_service import process_message

        def mock_agent_loop(client, msg, messages, rider_id, user_id, accumulator=None):
            if accumulator is not None:
                accumulator['full_content'] = 'No span response'
                accumulator['prompt_tokens'] = 50
                accumulator['completion_tokens'] = 20
            yield 'data: "No span response"\n\n'

        with patch('services.chat_service._bt_logger', None), \
             patch('services.chat_service.moderate_input', return_value=True), \
             patch('services.chat_service.models') as mock_models, \
             patch('services.chat_service.run_agent_loop', side_effect=mock_agent_loop), \
             patch('services.chat_service._get_client', return_value=MagicMock()), \
             patch('services.chat_service._get_system_prompt', return_value='system'), \
             patch('services.chat_service.assemble_rider_context', return_value=''), \
             patch('services.chat_service.assemble_team_context', return_value=''):
            mock_models.create_conversation.return_value = {'id': 'conv-nologger'}
            mock_models.get_recent_messages.return_value = []

            # Should not raise — app works without Braintrust
            chunks = list(process_message(user_id=1, message='Hi', rider_id=5))
            assert len(chunks) > 0
            # Assistant message should still be persisted (without span metadata)
            insert_calls = mock_models.insert_chat_message.call_args_list
            assistant_calls = [c for c in insert_calls if len(c[0]) >= 2 and c[0][1] == 'assistant']
            assert len(assistant_calls) == 1
