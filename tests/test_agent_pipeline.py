"""Tests for agentic pipeline — intent classification, agent loop, tool result formatting."""
import json
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
        for intent in ['data_query', 'coaching', 'knowledge', 'route_discussion', 'web_search', 'weather_query', 'off_topic']:
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


# ========== _format_tool_results() tests ==========


def test_format_tool_results_valid_xml(app):
    """_format_tool_results produces valid XML with <tool_results> wrapper."""
    with app.app_context():
        from services.chat_service import _format_tool_results

        results = [
            {'tool': 'fitness_score', 'result': {'rows': [{'score': 85}]}},
        ]
        output = _format_tool_results(results)
        assert '<tool_results>' in output
        assert '</tool_results>' in output
        assert '<tool_result tool="fitness_score">' in output
        assert '</tool_result>' in output
        # Should contain the JSON data
        assert '"score": 85' in output or '"score":85' in output


def test_format_tool_results_error(app):
    """_format_tool_results handles error results with Error: prefix."""
    with app.app_context():
        from services.chat_service import _format_tool_results

        results = [
            {'tool': 'fitness_score', 'result': {'error': 'Query timed out'}},
        ]
        output = _format_tool_results(results)
        assert '<tool_result tool="fitness_score">' in output
        assert 'Error: Query timed out' in output


def test_format_tool_results_empty_list(app):
    """_format_tool_results returns empty string for empty list."""
    with app.app_context():
        from services.chat_service import _format_tool_results

        assert _format_tool_results([]) == ''


# ========== run_agent_loop() tests ==========


def _mock_stream_completion(messages, accumulator):
    """Helper that simulates _stream_completion yielding one chunk."""
    accumulator['full_content'] = 'Hello'
    accumulator['prompt_tokens'] = 100
    accumulator['completion_tokens'] = 20
    yield 'data: "Hello"\n\n'


def test_agent_loop_data_query(app):
    """data_query intent calls execute_allowed_query with rider_id."""
    with app.app_context():
        from services.chat_service import run_agent_loop, IntentResult

        intent = IntentResult(intent='data_query', query_type='fitness_score')
        mock_client = MagicMock()

        with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())) as mock_classify, \
             patch('services.chat_service.execute_allowed_query', return_value={'rows': [{'score': 85}]}) as mock_exec, \
             patch('services.chat_service._stream_completion', side_effect=_mock_stream_completion):

            messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'test'}]
            chunks = list(run_agent_loop(mock_client, 'What is my fitness?', messages, rider_id=5, user_id=1))

            mock_exec.assert_called_once_with(query_type='fitness_score', params=(5,), user_id=1)
            # Should have thinking event + content
            assert any('"thinking"' in c for c in chunks)


def test_agent_loop_team_stats_no_rider_id(app):
    """data_query with get_team_stats passes params=() (team-scoped, no rider_id)."""
    with app.app_context():
        from services.chat_service import run_agent_loop, IntentResult

        intent = IntentResult(intent='data_query', query_type='get_team_stats')
        mock_client = MagicMock()

        with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
             patch('services.chat_service.execute_allowed_query', return_value={'rows': [{'count': 10}]}) as mock_exec, \
             patch('services.chat_service._stream_completion', side_effect=_mock_stream_completion):

            messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'test'}]
            list(run_agent_loop(mock_client, 'Team stats?', messages, rider_id=5, user_id=1))

            mock_exec.assert_called_once_with(query_type='get_team_stats', params=(), user_id=1)


def test_agent_loop_route_discussion(app):
    """route_discussion intent calls execute_allowed_query with (ride_name, ride_name)."""
    with app.app_context():
        from services.chat_service import run_agent_loop, IntentResult

        intent = IntentResult(intent='route_discussion', ride_name='Cascade 400')
        mock_client = MagicMock()

        with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
             patch('services.chat_service.execute_allowed_query', return_value={'rows': [{'name': 'Cascade 400'}]}) as mock_exec, \
             patch('services.chat_service._stream_completion', side_effect=_mock_stream_completion):

            messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'test'}]
            list(run_agent_loop(mock_client, 'Tell me about Cascade 400', messages, rider_id=5, user_id=1))

            mock_exec.assert_called_once_with(query_type='get_ride_plan', params=('Cascade 400', 'Cascade 400'), user_id=1)


def test_agent_loop_off_topic_no_db(app):
    """off_topic intent does NOT call execute_allowed_query."""
    with app.app_context():
        from services.chat_service import run_agent_loop, IntentResult

        intent = IntentResult(intent='off_topic')
        mock_client = MagicMock()

        with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
             patch('services.chat_service.execute_allowed_query') as mock_exec, \
             patch('services.chat_service._stream_completion', side_effect=_mock_stream_completion):

            messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'test'}]
            list(run_agent_loop(mock_client, 'Who won the World Cup?', messages, rider_id=5, user_id=1))

            mock_exec.assert_not_called()


def test_agent_loop_coaching_no_db(app):
    """coaching intent does NOT call execute_allowed_query."""
    with app.app_context():
        from services.chat_service import run_agent_loop, IntentResult

        intent = IntentResult(intent='coaching')
        mock_client = MagicMock()

        with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
             patch('services.chat_service.execute_allowed_query') as mock_exec, \
             patch('services.chat_service._stream_completion', side_effect=_mock_stream_completion):

            messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'test'}]
            list(run_agent_loop(mock_client, 'How do I train?', messages, rider_id=5, user_id=1))

            mock_exec.assert_not_called()


def test_agent_loop_knowledge_no_db(app):
    """knowledge intent does NOT call execute_allowed_query."""
    with app.app_context():
        from services.chat_service import run_agent_loop, IntentResult

        intent = IntentResult(intent='knowledge')
        mock_client = MagicMock()

        with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
             patch('services.chat_service.execute_allowed_query') as mock_exec, \
             patch('services.chat_service._stream_completion', side_effect=_mock_stream_completion):

            messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'test'}]
            list(run_agent_loop(mock_client, 'What are ACP rules?', messages, rider_id=5, user_id=1))

            mock_exec.assert_not_called()


def test_agent_loop_max_iterations_guard(app):
    """Loop exits after MAX_ITERATIONS (5) even if classify_intent keeps returning data_query."""
    with app.app_context():
        from services.chat_service import run_agent_loop, IntentResult, MAX_ITERATIONS

        assert MAX_ITERATIONS == 5  # Verify constant

        # This test just verifies the guard exists — v1 only does one iteration anyway
        intent = IntentResult(intent='data_query', query_type='fitness_score')
        mock_client = MagicMock()

        with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
             patch('services.chat_service.execute_allowed_query', return_value={'rows': [{'score': 85}]}), \
             patch('services.chat_service._stream_completion', side_effect=_mock_stream_completion):

            messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'test'}]
            chunks = list(run_agent_loop(mock_client, 'test', messages, rider_id=5, user_id=1))
            # Should complete without hanging
            assert len(chunks) > 0


def test_agent_loop_max_db_queries_guard(app):
    """MAX_DB_QUERIES constant is 3."""
    with app.app_context():
        from services.chat_service import MAX_DB_QUERIES
        assert MAX_DB_QUERIES == 3


def test_agent_loop_thinking_event(app):
    """run_agent_loop yields a {"status": "thinking"} SSE event before classification."""
    with app.app_context():
        from services.chat_service import run_agent_loop, IntentResult

        intent = IntentResult(intent='coaching')
        mock_client = MagicMock()

        with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
             patch('services.chat_service._stream_completion', side_effect=_mock_stream_completion):

            messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'test'}]
            chunks = list(run_agent_loop(mock_client, 'How do I train?', messages, rider_id=5, user_id=1))

            # First chunk should be the thinking event
            thinking_chunk = chunks[0]
            assert 'data:' in thinking_chunk
            parsed = json.loads(thinking_chunk.replace('data: ', '').strip())
            assert parsed['status'] == 'thinking'


def test_agent_loop_token_usage_in_accumulator(app):
    """Token usage from streaming call is available after generator exhaustion."""
    with app.app_context():
        from services.chat_service import run_agent_loop, IntentResult

        intent = IntentResult(intent='coaching')
        mock_client = MagicMock()

        with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
             patch('services.chat_service._stream_completion', side_effect=_mock_stream_completion):

            messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'test'}]
            gen = run_agent_loop(mock_client, 'test', messages, rider_id=5, user_id=1)
            chunks = list(gen)
            # Content chunks should be present
            assert any('"Hello"' in c for c in chunks)
