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


def test_context_privacy_flag(app):
    """strava_data_private=True returns empty string (SEC-11)."""
    with app.app_context():
        from services.chat_service import assemble_rider_context

        with patch('services.chat_service.models') as mock_models:
            mock_models.get_rider_privacy_flag.return_value = True
            result = assemble_rider_context(user_id=1, rider_id=5)
            assert result == ''
            # Should not query Strava or anything else
            mock_models.get_strava_connection.assert_not_called()


def test_context_no_rider_id(app):
    """rider_id=None returns empty string."""
    with app.app_context():
        from services.chat_service import assemble_rider_context

        result = assemble_rider_context(user_id=1, rider_id=None)
        assert result == ''


def test_context_strava_connected(app):
    """Strava-connected user gets rider_data with fitness score (PERS-01)."""
    with app.app_context():
        from services.chat_service import assemble_rider_context

        mock_activities = [
            {'activity_type': 'Ride', 'distance': 80000, 'total_elevation_gain': 500,
             'moving_time': 10800, 'start_date_local': '2026-03-10', 'has_heartrate': False,
             'average_heartrate': None, 'max_heartrate': None, 'device_watts': False,
             'weighted_average_watts': None, 'suffer_score': None}
        ]

        with patch('services.chat_service.models') as mock_models, \
             patch('services.chat_service.calculate_fitness_score') as mock_fitness, \
             patch('services.chat_service._build_training_summary') as mock_summary:
            mock_models.get_rider_privacy_flag.return_value = False
            mock_models.get_strava_connection.return_value = {'id': 1, 'rider_id': 5}
            mock_models.get_strava_activities.return_value = mock_activities
            mock_fitness.return_value = {'total': 65, 'frequency': 15, 'volume': 25,
                                         'intensity': 15, 'recency': 10}
            mock_summary.return_value = "STRAVA DATA (last 4 weeks): 1 ride, 80 km"
            mock_models.get_rider_upcoming_signups.return_value = []

            result = assemble_rider_context(user_id=1, rider_id=5)
            assert '<rider_data>' in result
            assert 'STRAVA DATA' in result
            mock_fitness.assert_called_once_with(mock_activities)
            mock_summary.assert_called_once()


def test_context_no_strava(app):
    """No Strava connection and no brevet history returns empty string (PERS-02)."""
    with app.app_context():
        from services.chat_service import assemble_rider_context

        with patch('services.chat_service.models') as mock_models:
            mock_models.get_rider_privacy_flag.return_value = False
            mock_models.get_strava_connection.return_value = None
            mock_models.get_current_season.return_value = {'id': 1, 'name': '2025-2026'}
            mock_models.get_rider_participation.return_value = []
            mock_models.get_rider_upcoming_signups.return_value = []

            result = assemble_rider_context(user_id=1, rider_id=5)
            assert result == ''


def test_context_brevet_fallback(app):
    """No Strava but has brevet history returns brevet data (PERS-02 fallback)."""
    with app.app_context():
        from services.chat_service import assemble_rider_context

        mock_participation = [
            {'status': 'FINISHED', 'ride_name': 'Cascade 200', 'date': '2026-02-15',
             'distance_km': 200, 'elevation_ft': 8000, 'finish_time': '11:30'}
        ]

        with patch('services.chat_service.models') as mock_models, \
             patch('services.chat_service._build_brevet_history_summary') as mock_brevet:
            mock_models.get_rider_privacy_flag.return_value = False
            mock_models.get_strava_connection.return_value = None
            mock_models.get_current_season.return_value = {'id': 1, 'name': '2025-2026'}
            mock_models.get_rider_participation.return_value = mock_participation
            mock_brevet.return_value = "BREVET HISTORY: 1 completed, 200 km"
            mock_models.get_rider_upcoming_signups.return_value = []

            result = assemble_rider_context(user_id=1, rider_id=5)
            assert '<rider_data>' in result
            assert 'BREVET HISTORY' in result


def test_context_includes_upcoming_brevets(app):
    """Upcoming brevets appear in context, capped at 3 (KNOW-06)."""
    with app.app_context():
        from services.chat_service import assemble_rider_context

        mock_signups = [
            {'name': 'Cascade 200', 'date': '2026-04-01', 'distance_km': 200, 'signup_status': 'GOING'},
            {'name': 'Cascade 300', 'date': '2026-04-15', 'distance_km': 300, 'signup_status': 'GOING'},
            {'name': 'Cascade 400', 'date': '2026-05-01', 'distance_km': 400, 'signup_status': 'INTERESTED'},
            {'name': 'Cascade 600', 'date': '2026-06-01', 'distance_km': 600, 'signup_status': 'GOING'},
        ]

        with patch('services.chat_service.models') as mock_models:
            mock_models.get_rider_privacy_flag.return_value = False
            mock_models.get_strava_connection.return_value = None
            mock_models.get_current_season.return_value = None
            mock_models.get_rider_upcoming_signups.return_value = mock_signups

            result = assemble_rider_context(user_id=1, rider_id=5)
            assert '<rider_data>' in result
            assert 'Cascade 200' in result
            assert 'Cascade 300' in result
            assert 'Cascade 400' in result
            # 4th signup should NOT appear (capped at 3)
            assert 'Cascade 600' not in result


def test_context_team_data(app):
    """Team context includes upcoming rides from get_upcoming_rides() (PERS-03)."""
    with app.app_context():
        from services.chat_service import assemble_team_context

        mock_rides = [
            {'name': 'Spring 200', 'date': '2026-04-01', 'distance_km': 200},
            {'name': 'Spring 300', 'date': '2026-04-15', 'distance_km': 300},
        ]

        with patch('services.chat_service.models') as mock_models:
            mock_models.get_upcoming_rides.return_value = mock_rides

            result = assemble_team_context()
            assert '<team_context>' in result
            assert 'Spring 200' in result
            assert 'Spring 300' in result


def test_context_team_empty(app):
    """Team context with no rides returns 'no rides scheduled'."""
    with app.app_context():
        from services.chat_service import assemble_team_context

        with patch('services.chat_service.models') as mock_models:
            mock_models.get_upcoming_rides.return_value = []

            result = assemble_team_context()
            assert '<team_context>' in result
            assert 'No upcoming Team Asha rides scheduled' in result


def test_stream_chunk_parsing(app):
    """SSE output lines are properly terminated with data: prefix and double newlines (CHAT-04)."""
    with app.app_context():
        from services.chat_service import process_message

        with patch('services.chat_service.moderate_input', return_value=True), \
             patch('services.chat_service.models') as mock_models, \
             patch('services.chat_service._stream_completion') as mock_stream, \
             patch('services.chat_service._get_system_prompt', return_value='system'), \
             patch('services.chat_service.assemble_rider_context', return_value=''), \
             patch('services.chat_service.assemble_team_context', return_value=''):
            mock_models.create_conversation.return_value = {'id': 'conv-123'}
            mock_models.get_recent_messages.return_value = []
            # Simulate streaming tokens
            mock_stream.return_value = iter([
                'data: "Hello"\n\n',
                'data: " world"\n\n',
            ])

            chunks = list(process_message(user_id=1, message='Hi', rider_id=5))
            # Every SSE line should end with \n\n
            for chunk in chunks:
                assert chunk.endswith('\n\n'), f"SSE line not properly terminated: {chunk!r}"
            # First data line should be conversation_id
            assert '"conversation_id"' in chunks[0]


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
