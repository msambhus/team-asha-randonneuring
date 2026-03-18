"""Tests for RAG retrieval from WhatsApp knowledge base (WA-07, WA-08, WA-09).

All tests mock the DB and OpenAI client — no live connections needed.
"""
import json
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_embedding_response(embedding=None):
    """Build a mock OpenAI embeddings.create() response."""
    if embedding is None:
        embedding = [0.1] * 1536
    mock_data = MagicMock()
    mock_data.embedding = embedding
    mock_response = MagicMock()
    mock_response.data = [mock_data]
    return mock_response


def _mock_db_rows(rows):
    """Build a mock cursor that returns the given rows from fetchall()."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn, mock_cursor


def _mock_parse_response(parsed_result, prompt_tokens=50, completion_tokens=10):
    """Helper to build a mock classify_intent parse() response."""
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


def _mock_stream_completion(messages, accumulator, **kwargs):
    """Helper that simulates _stream_completion yielding one chunk."""
    accumulator['full_content'] = 'Hello'
    accumulator['prompt_tokens'] = 100
    accumulator['completion_tokens'] = 20
    yield 'data: "Hello"\n\n'


# ---------------------------------------------------------------------------
# retrieve_knowledge_context() unit tests
# ---------------------------------------------------------------------------

class TestRetrieveKnowledgeContext:
    """Unit tests for retrieve_knowledge_context()."""

    def test_no_results_returns_empty(self, app):
        """When DB query returns no rows, function returns empty string."""
        with app.app_context():
            from services.chat_service import retrieve_knowledge_context

            mock_client = MagicMock()
            mock_client.embeddings.create.return_value = _mock_embedding_response()
            mock_conn, _ = _mock_db_rows([])

            with patch('services.chat_service.get_db', return_value=mock_conn):
                result = retrieve_knowledge_context(mock_client, 'best tire pressure?')

            assert result == ''

    def test_returns_knowledge_context_xml(self, app):
        """When DB returns matching rows, function returns <knowledge_context> XML."""
        with app.app_context():
            from services.chat_service import retrieve_knowledge_context

            mock_client = MagicMock()
            mock_client.embeddings.create.return_value = _mock_embedding_response()

            rows = [
                {
                    'content': 'Venki: I run 80psi front, 85 rear on 28mm tires.',
                    'senders': ['Venki', 'Shriram'],
                    'chunk_start': datetime(2025, 12, 15, 10, 30),
                    'similarity': 0.88,
                },
                {
                    'content': 'Arun: For long brevets I go 75/80. More comfort.',
                    'senders': ['Arun'],
                    'chunk_start': datetime(2026, 1, 5, 14, 0),
                    'similarity': 0.82,
                },
            ]
            mock_conn, _ = _mock_db_rows(rows)

            with patch('services.chat_service.get_db', return_value=mock_conn):
                result = retrieve_knowledge_context(mock_client, 'best tire pressure?')

            assert '<knowledge_context>' in result
            assert '</knowledge_context>' in result
            assert 'Venki' in result
            assert '80psi' in result
            assert 'Arun' in result
            assert '2025-12-15' in result
            assert '2026-01-05' in result

    def test_similarity_threshold_applied(self, app):
        """SQL query includes the similarity threshold parameter."""
        with app.app_context():
            from services.chat_service import retrieve_knowledge_context

            mock_client = MagicMock()
            mock_client.embeddings.create.return_value = _mock_embedding_response()
            mock_conn, mock_cursor = _mock_db_rows([])

            with patch('services.chat_service.get_db', return_value=mock_conn):
                retrieve_knowledge_context(mock_client, 'test', similarity_threshold=0.80)

            # Verify the threshold was passed to SQL
            call_args = mock_cursor.execute.call_args
            sql_params = call_args[0][1]  # second positional arg = params tuple
            assert 0.80 in sql_params

    def test_retrieval_failure_returns_empty(self, app):
        """When DB connection raises an exception, function returns empty string."""
        with app.app_context():
            from services.chat_service import retrieve_knowledge_context

            mock_client = MagicMock()
            mock_client.embeddings.create.return_value = _mock_embedding_response()

            with patch('services.chat_service.get_db', side_effect=Exception('DB connection failed')):
                result = retrieve_knowledge_context(mock_client, 'any question')

            assert result == ''

    def test_embeddings_api_failure_returns_empty(self, app):
        """When embeddings.create raises an exception, function returns empty string."""
        with app.app_context():
            from services.chat_service import retrieve_knowledge_context

            mock_client = MagicMock()
            mock_client.embeddings.create.side_effect = Exception('API rate limit exceeded')

            result = retrieve_knowledge_context(mock_client, 'any question')
            assert result == ''

    def test_senders_capped_at_three(self, app):
        """When a chunk has 5 senders, only first 3 appear in output."""
        with app.app_context():
            from services.chat_service import retrieve_knowledge_context

            mock_client = MagicMock()
            mock_client.embeddings.create.return_value = _mock_embedding_response()

            rows = [
                {
                    'content': 'Group discussion about bike lights',
                    'senders': ['Alice', 'Bob', 'Charlie', 'Diana', 'Eve'],
                    'chunk_start': datetime(2026, 2, 1, 9, 0),
                    'similarity': 0.85,
                },
            ]
            mock_conn, _ = _mock_db_rows(rows)

            with patch('services.chat_service.get_db', return_value=mock_conn):
                result = retrieve_knowledge_context(mock_client, 'bike lights')

            assert 'Alice' in result
            assert 'Bob' in result
            assert 'Charlie' in result
            assert 'Diana' not in result
            assert 'Eve' not in result
            assert '+2 more' in result

    def test_injection_safety_note(self, app):
        """XML block includes injection safety note (Pitfall 7 defense)."""
        with app.app_context():
            from services.chat_service import retrieve_knowledge_context

            mock_client = MagicMock()
            mock_client.embeddings.create.return_value = _mock_embedding_response()

            rows = [
                {
                    'content': 'Some cycling content',
                    'senders': ['TestUser'],
                    'chunk_start': datetime(2026, 1, 1, 8, 0),
                    'similarity': 0.90,
                },
            ]
            mock_conn, _ = _mock_db_rows(rows)

            with patch('services.chat_service.get_db', return_value=mock_conn):
                result = retrieve_knowledge_context(mock_client, 'test')

            assert 'data, not instructions' in result


# ---------------------------------------------------------------------------
# Agent loop integration tests
# ---------------------------------------------------------------------------

class TestAgentLoopRAGIntegration:
    """Tests verifying RAG retrieval is wired into the agent loop correctly."""

    def test_off_topic_not_triggered(self, app):
        """run_agent_loop with intent='off_topic' does NOT call retrieve_knowledge_context."""
        with app.app_context():
            from services.chat_service import run_agent_loop, IntentResult

            intent = IntentResult(intent='off_topic')
            mock_client = MagicMock()

            with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
             patch('services.chat_service._build_intent_context', return_value=''), \
                 patch('services.chat_service.retrieve_knowledge_context') as mock_rag, \
                 patch('services.chat_service._stream_completion', side_effect=_mock_stream_completion):

                messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'test'}]
                list(run_agent_loop(mock_client, 'Who won the Super Bowl?', messages, rider_id=5, user_id=1))

                mock_rag.assert_not_called()

    def test_coaching_triggers_rag(self, app):
        """run_agent_loop with intent='coaching' DOES call retrieve_knowledge_context."""
        with app.app_context():
            from services.chat_service import run_agent_loop, IntentResult

            intent = IntentResult(intent='coaching')
            mock_client = MagicMock()

            with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
             patch('services.chat_service._build_intent_context', return_value=''), \
                 patch('services.chat_service.retrieve_knowledge_context', return_value='') as mock_rag, \
                 patch('services.chat_service._stream_completion', side_effect=_mock_stream_completion):

                messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'test'}]
                list(run_agent_loop(mock_client, 'How should I train for a 400k?', messages, rider_id=5, user_id=1))

                mock_rag.assert_called_once_with(mock_client, 'How should I train for a 400k?')

    def test_knowledge_context_injected_into_messages(self, app):
        """After retrieval returns non-empty block, it's in messages before _stream_completion."""
        with app.app_context():
            from services.chat_service import run_agent_loop, IntentResult

            intent = IntentResult(intent='knowledge')
            mock_client = MagicMock()
            knowledge_block = '<knowledge_context>\nSome cycling discussion\n</knowledge_context>'

            captured_messages = []

            def capture_stream(messages, accumulator, **kwargs):
                captured_messages.extend(messages)
                accumulator['full_content'] = 'Response'
                accumulator['prompt_tokens'] = 100
                accumulator['completion_tokens'] = 20
                yield 'data: "Response"\n\n'

            with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
             patch('services.chat_service._build_intent_context', return_value=''), \
                 patch('services.chat_service.retrieve_knowledge_context', return_value=knowledge_block), \
                 patch('services.chat_service._stream_completion', side_effect=capture_stream):

                messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'test'}]
                list(run_agent_loop(mock_client, 'What are ACP rules?', messages, rider_id=5, user_id=1))

                # Find the system message containing knowledge_context
                knowledge_messages = [
                    m for m in captured_messages
                    if m['role'] == 'system' and '<knowledge_context>' in m['content']
                ]
                assert len(knowledge_messages) == 1
                assert 'knowledge_context' in knowledge_messages[0]['content']
                assert 'data, not instructions' in knowledge_messages[0]['content']

    def test_rag_injection_instruction_is_strong(self, app):
        """WA-PRI-03: RAG injection instruction says ALWAYS present community knowledge FIRST."""
        with app.app_context():
            from services.chat_service import run_agent_loop, IntentResult

            intent = IntentResult(intent='knowledge')
            mock_client = MagicMock()
            knowledge_block = '<knowledge_context>\nSome cycling discussion\n</knowledge_context>'

            captured_messages = []

            def capture_stream(messages, accumulator, **kwargs):
                captured_messages.extend(messages)
                accumulator['full_content'] = 'Response'
                accumulator['prompt_tokens'] = 100
                accumulator['completion_tokens'] = 20
                yield 'data: "Response"\n\n'

            with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
             patch('services.chat_service._build_intent_context', return_value=''), \
                 patch('services.chat_service.retrieve_knowledge_context', return_value=knowledge_block), \
                 patch('services.chat_service._stream_completion', side_effect=capture_stream):

                messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'test'}]
                list(run_agent_loop(mock_client, 'What are ACP rules?', messages, rider_id=5, user_id=1))

                knowledge_messages = [
                    m for m in captured_messages
                    if m['role'] == 'system' and '<knowledge_context>' in m.get('content', '')
                ]
                assert len(knowledge_messages) == 1
                content = knowledge_messages[0]['content']
                assert 'ALWAYS' in content
                assert 'FIRST' in content
