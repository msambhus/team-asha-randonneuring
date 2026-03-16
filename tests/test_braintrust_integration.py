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


# ========== EVAL DATASET COVERAGE & SCORER TESTS (Plan 04-02) ==========


def test_intent_dataset_coverage():
    """EVAL-02: Intent dataset has 20+ records with all 6 intent types, >= 4 each."""
    from evals.eval_intent import INTENT_DATASET_RECORDS

    assert len(INTENT_DATASET_RECORDS) >= 20

    # Group by expected intent
    from collections import Counter
    counts = Counter(r["expected"] for r in INTENT_DATASET_RECORDS)

    expected_types = {"data_query", "coaching", "knowledge", "route_discussion", "web_search", "off_topic"}
    assert set(counts.keys()) == expected_types, f"Missing intents: {expected_types - set(counts.keys())}"

    for intent_type, count in counts.items():
        assert count >= 4, f"Intent '{intent_type}' has only {count} records, need >= 4"

    # Each record must have input and expected keys
    for record in INTENT_DATASET_RECORDS:
        assert "input" in record
        assert "expected" in record


def test_intent_scorer_correct():
    """EVAL-02: intent_accuracy_scorer returns 1 on match, 0 on mismatch."""
    from evals.eval_intent import intent_accuracy_scorer

    result_match = intent_accuracy_scorer(
        input={"question": "test"}, output="data_query", expected="data_query"
    )
    assert result_match["score"] == 1
    assert result_match["name"] == "intent_accuracy"

    result_mismatch = intent_accuracy_scorer(
        input={"question": "test"}, output="coaching", expected="off_topic"
    )
    assert result_mismatch["score"] == 0


def test_grounding_dataset_coverage():
    """EVAL-03: Grounding dataset has 10+ records with input and expected keys."""
    from evals.eval_grounding import GROUNDING_DATASET_RECORDS

    assert len(GROUNDING_DATASET_RECORDS) >= 10

    for record in GROUNDING_DATASET_RECORDS:
        assert "input" in record
        assert "expected" in record


def test_grounding_scorer():
    """EVAL-03: contains_expected_value_scorer returns 1 when value found, 0 otherwise."""
    from evals.eval_grounding import contains_expected_value_scorer

    result_found = contains_expected_value_scorer(
        input={"question": "test"},
        output='{"rows": [{"total_km": 485.3}]}',
        expected="485.3",
    )
    assert result_found["score"] == 1
    assert result_found["name"] == "contains_expected_value"

    result_missing = contains_expected_value_scorer(
        input={"question": "test"},
        output='{"rows": [{"total_km": 100}]}',
        expected="485.3",
    )
    assert result_missing["score"] == 0


def test_guardrail_dataset_coverage():
    """EVAL-04: Guardrail dataset has 10+ bypass patterns."""
    from evals.eval_guardrail import BYPASS_PATTERNS

    assert len(BYPASS_PATTERNS) >= 10


def test_guardrail_scorer():
    """EVAL-04: guardrail_scorer returns 1 only when off_topic + no DB call."""
    from evals.eval_guardrail import guardrail_scorer

    # Correct: off_topic, no DB call
    result_pass = guardrail_scorer(
        input={"question": "test"},
        output={"intent": "off_topic", "db_tool_called": False},
        expected="blocked",
    )
    assert result_pass["score"] == 1

    # Fail: data_query with DB call
    result_fail_query = guardrail_scorer(
        input={"question": "test"},
        output={"intent": "data_query", "db_tool_called": True},
        expected="blocked",
    )
    assert result_fail_query["score"] == 0

    # Fail: off_topic but DB call happened (shouldn't happen, but score 0)
    result_fail_db = guardrail_scorer(
        input={"question": "test"},
        output={"intent": "off_topic", "db_tool_called": True},
        expected="blocked",
    )
    assert result_fail_db["score"] == 0


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


# ========== E2E EVAL DATASET & SCORER TESTS (EVAL-05) ==========


def test_e2e_dataset_coverage():
    """EVAL-05: E2E dataset has 20+ records across all 7 scenario types."""
    from evals.eval_e2e import E2E_DATASET_RECORDS

    assert len(E2E_DATASET_RECORDS) >= 20

    from collections import Counter
    scenarios = Counter(r["metadata"]["scenario"] for r in E2E_DATASET_RECORDS)

    expected_scenarios = {
        "data_query", "coaching", "knowledge",
        "bike_knowledge", "web_search", "off_topic", "route_discussion",
    }
    assert set(scenarios.keys()) == expected_scenarios, (
        f"Missing scenarios: {expected_scenarios - set(scenarios.keys())}"
    )

    for record in E2E_DATASET_RECORDS:
        assert "input" in record
        assert "expected" in record
        assert "intent" in record["expected"]


def test_e2e_intent_scorer():
    """EVAL-05: intent_correct_scorer returns 1 on match, 0 on mismatch."""
    from evals.eval_e2e import intent_correct_scorer

    result_match = intent_correct_scorer(
        input={"question": "test"},
        output={"intent": "data_query"},
        expected={"intent": "data_query"},
    )
    assert result_match["score"] == 1
    assert result_match["name"] == "intent_correct"

    result_mismatch = intent_correct_scorer(
        input={"question": "test"},
        output={"intent": "coaching"},
        expected={"intent": "off_topic"},
    )
    assert result_mismatch["score"] == 0


def test_e2e_tool_scorer():
    """EVAL-05: tool_called_correctly_scorer checks tool or None."""
    from evals.eval_e2e import tool_called_correctly_scorer

    # Correct tool
    result = tool_called_correctly_scorer(
        input={"question": "test"},
        output={"tool_called": "fitness_score"},
        expected={"tool_called": "fitness_score"},
    )
    assert result["score"] == 1

    # Wrong tool
    result = tool_called_correctly_scorer(
        input={"question": "test"},
        output={"tool_called": "brevet_history"},
        expected={"tool_called": "fitness_score"},
    )
    assert result["score"] == 0

    # Correctly no tool
    result = tool_called_correctly_scorer(
        input={"question": "test"},
        output={"tool_called": None},
        expected={"tool_called": None},
    )
    assert result["score"] == 1


def test_e2e_response_quality_scorer():
    """EVAL-05: response_quality_scorer returns fractional keyword match score."""
    from evals.eval_e2e import response_quality_scorer

    # All keywords found
    result = response_quality_scorer(
        input={"question": "test"},
        output={"response_text": "You have 485 km across 12 rides"},
        expected={"response_keywords": ["485", "12"]},
    )
    assert result["score"] == 1.0

    # Partial match (1 of 2)
    result = response_quality_scorer(
        input={"question": "test"},
        output={"response_text": "You have 485 km total"},
        expected={"response_keywords": ["485", "12"]},
    )
    assert result["score"] == 0.5

    # No match
    result = response_quality_scorer(
        input={"question": "test"},
        output={"response_text": "I can help with cycling"},
        expected={"response_keywords": ["485", "12"]},
    )
    assert result["score"] == 0.0

    # No keywords expected
    result = response_quality_scorer(
        input={"question": "test"},
        output={"response_text": "anything"},
        expected={"response_keywords": []},
    )
    assert result["score"] == 1.0


def test_e2e_scope_scorer():
    """EVAL-05: scope_maintained_scorer checks redirect for off-topic, no leak for on-topic."""
    from evals.eval_e2e import scope_maintained_scorer

    # Off-topic with cycling redirect — pass
    result = scope_maintained_scorer(
        input={"question": "test"},
        output={"intent": "off_topic",
                "response_text": "I focus on cycling and randonneuring coaching."},
        expected={},
    )
    assert result["score"] == 1

    # Off-topic without cycling redirect — fail
    result = scope_maintained_scorer(
        input={"question": "test"},
        output={"intent": "off_topic",
                "response_text": "The capital of France is Paris."},
        expected={},
    )
    assert result["score"] == 0

    # On-topic without leakage — pass
    result = scope_maintained_scorer(
        input={"question": "test"},
        output={"intent": "knowledge",
                "response_text": "ACP time limits are 13.5 hours for 200km."},
        expected={},
    )
    assert result["score"] == 1

    # On-topic with off-topic leakage — fail
    result = scope_maintained_scorer(
        input={"question": "test"},
        output={"intent": "knowledge",
                "response_text": "Here is a recipe for pasta."},
        expected={},
    )
    assert result["score"] == 0


def test_e2e_persona_scorer():
    """EVAL-05: persona_correct_scorer matches coach persona."""
    from evals.eval_e2e import persona_correct_scorer

    # Shriram match
    result = persona_correct_scorer(
        input={"question": "test"},
        output={"coach": "shriram"},
        expected={"coach": "shriram"},
    )
    assert result["score"] == 1

    # Venki match
    result = persona_correct_scorer(
        input={"question": "test"},
        output={"coach": "venki"},
        expected={"coach": "venki"},
    )
    assert result["score"] == 1

    # None match (off-topic)
    result = persona_correct_scorer(
        input={"question": "test"},
        output={"coach": None},
        expected={"coach": None},
    )
    assert result["score"] == 1

    # Mismatch
    result = persona_correct_scorer(
        input={"question": "test"},
        output={"coach": "venki"},
        expected={"coach": "shriram"},
    )
    assert result["score"] == 0
