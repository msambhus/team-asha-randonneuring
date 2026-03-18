"""Tests for RWGPS chat integration — route lookup, summarization, caching, error handling."""
import pytest
from unittest.mock import patch, MagicMock


# ── Sample data fixtures ────────────────────────────────────────────

SAMPLE_ROUTE_DATA = {
    'id': 12345,
    'name': 'SFR 300K Brevet',
    'distance': 300000,  # meters
    'elevation_gain': 3048,  # meters (~10,000 ft)
    'track_points': [],
    'course_points': [
        {'t': 'Start', 'n': 'San Francisco Start', 'd': 0, 'e': 10},
        {'t': 'Control', 'n': 'Petaluma Control', 'd': 80000, 'e': 50},
        {'t': 'Food', 'n': 'Lunch Stop', 'd': 150000, 'e': 100},
        {'t': 'Control', 'n': 'Bodega Bay Control', 'd': 240000, 'e': 30},
        {'t': 'End', 'n': 'San Francisco Finish', 'd': 300000, 'e': 10},
    ],
}

SAMPLE_CONTROLS = [
    {'name': 'San Francisco Start', 'distance_m': 0, 'elevation_m': 10, 'stop_type': 'start', 'rwgps_type': 'Start'},
    {'name': 'Petaluma Control', 'distance_m': 80000, 'elevation_m': 50, 'stop_type': 'control', 'rwgps_type': 'Control'},
    {'name': 'Lunch Stop', 'distance_m': 150000, 'elevation_m': 100, 'stop_type': 'rest', 'rwgps_type': 'Food'},
    {'name': 'Bodega Bay Control', 'distance_m': 240000, 'elevation_m': 30, 'stop_type': 'control', 'rwgps_type': 'Control'},
    {'name': 'San Francisco Finish', 'distance_m': 300000, 'elevation_m': 10, 'stop_type': 'finish', 'rwgps_type': 'End'},
]


# ── RWGPS-01: get_ride_rwgps_url SQL query ──────────────────────────

class TestGetRideRwgpsUrlQuery:
    def test_query_exists_in_allowed_queries(self, app):
        """get_ride_rwgps_url must exist in ALLOWED_QUERIES."""
        with app.app_context():
            from services.chat_tools import ALLOWED_QUERIES
            assert 'get_ride_rwgps_url' in ALLOWED_QUERIES

    def test_query_selects_from_ride_table(self, app):
        """Query should select from ride table with name, rwgps_url, distance_km."""
        with app.app_context():
            from services.chat_tools import ALLOWED_QUERIES
            sql = ALLOWED_QUERIES['get_ride_rwgps_url'].lower()
            assert 'from ride' in sql or 'from ride ' in sql
            assert 'rwgps_url' in sql
            assert 'distance_km' in sql
            assert 'ilike' in sql
            assert 'order by' in sql
            assert 'limit 1' in sql

    def test_query_has_no_rider_id_filter(self, app):
        """Routes are team-level, not user-scoped — no rider_id filter."""
        with app.app_context():
            from services.chat_tools import ALLOWED_QUERIES
            sql = ALLOWED_QUERIES['get_ride_rwgps_url'].lower()
            assert 'rider_id' not in sql


# ── RWGPS-03: summarize_route_for_chat ──────────────────────────────

class TestSummarizeRouteForChat:
    def test_returns_compact_dict_with_required_keys(self, app):
        """Summary must have all required keys for LLM context."""
        with app.app_context():
            from services.chat_tools import summarize_route_for_chat
            result = summarize_route_for_chat(SAMPLE_ROUTE_DATA, SAMPLE_CONTROLS)

            required_keys = {
                'name', 'total_distance_miles', 'total_elevation_ft',
                'distance_km', 'cutoff_hours', 'overall_ft_per_mile',
                'avg_moving_speed_mph', 'rwgps_url', 'control_stops', 'source',
            }
            assert required_keys.issubset(set(result.keys()))

    def test_control_stops_have_required_keys(self, app):
        """Each control stop must have location, distance_miles, stop_type, elevation_gain_ft."""
        with app.app_context():
            from services.chat_tools import summarize_route_for_chat
            result = summarize_route_for_chat(SAMPLE_ROUTE_DATA, SAMPLE_CONTROLS)

            assert len(result['control_stops']) > 0
            for stop in result['control_stops']:
                assert 'location' in stop
                assert 'distance_miles' in stop
                assert 'stop_type' in stop
                assert 'elevation_gain_ft' in stop

    def test_source_field_is_live_rwgps_api(self, app):
        """Source must indicate live API data."""
        with app.app_context():
            from services.chat_tools import summarize_route_for_chat
            result = summarize_route_for_chat(SAMPLE_ROUTE_DATA, SAMPLE_CONTROLS)
            assert result['source'] == 'live_rwgps_api'

    def test_no_track_points_in_output(self, app):
        """Summary must NOT include track_points — too large for LLM context."""
        with app.app_context():
            from services.chat_tools import summarize_route_for_chat
            result = summarize_route_for_chat(SAMPLE_ROUTE_DATA, SAMPLE_CONTROLS)
            assert 'track_points' not in result


# ── RWGPS-04/06: fetch_and_summarize_route caching ──────────────────

class TestFetchAndSummarizeRouteCaching:
    @patch('services.chat_tools.cache')
    @patch('services.chat_tools.fetch_route')
    @patch('services.chat_tools.extract_controls')
    def test_cache_hit_returns_cached_without_api_call(self, mock_extract, mock_fetch, mock_cache, app):
        """When cache has entry, return it without calling fetch_route."""
        with app.app_context():
            from services.chat_tools import fetch_and_summarize_route
            cached_data = {'name': 'Cached Route', 'source': 'live_rwgps_api'}
            mock_cache.get.return_value = cached_data

            result = fetch_and_summarize_route('12345')
            assert result == {'rows': [cached_data]}
            mock_fetch.assert_not_called()

    @patch('services.chat_tools.cache')
    @patch('services.chat_tools.fetch_route')
    @patch('services.chat_tools.extract_controls')
    def test_cache_miss_calls_api_and_stores(self, mock_extract, mock_fetch, mock_cache, app):
        """First call fetches from API and stores in cache."""
        with app.app_context():
            from services.chat_tools import fetch_and_summarize_route
            mock_cache.get.return_value = None
            mock_fetch.return_value = SAMPLE_ROUTE_DATA
            mock_extract.return_value = SAMPLE_CONTROLS

            result = fetch_and_summarize_route('12345')
            assert 'rows' in result
            assert len(result['rows']) == 1
            assert result['rows'][0]['source'] == 'live_rwgps_api'
            mock_fetch.assert_called_once_with('12345')
            mock_cache.set.assert_called_once()

    @patch('services.chat_tools.cache')
    @patch('services.chat_tools.fetch_route')
    @patch('services.chat_tools.extract_controls')
    def test_cache_key_format(self, mock_extract, mock_fetch, mock_cache, app):
        """Cache key must be 'rwgps_route_{route_id}'."""
        with app.app_context():
            from services.chat_tools import fetch_and_summarize_route
            mock_cache.get.return_value = None
            mock_fetch.return_value = SAMPLE_ROUTE_DATA
            mock_extract.return_value = SAMPLE_CONTROLS

            fetch_and_summarize_route('99999')
            mock_cache.get.assert_called_with('rwgps_route_99999')
            # Check the set call uses the same key
            set_call = mock_cache.set.call_args
            assert set_call[0][0] == 'rwgps_route_99999'

    @patch('services.chat_tools.cache')
    @patch('services.chat_tools.fetch_route')
    @patch('services.chat_tools.extract_controls')
    def test_second_call_uses_cache(self, mock_extract, mock_fetch, mock_cache, app):
        """Second call with same route_id should hit cache, not API."""
        with app.app_context():
            from services.chat_tools import fetch_and_summarize_route
            cached_summary = {'name': 'Test', 'source': 'live_rwgps_api'}

            # First call: cache miss
            mock_cache.get.return_value = None
            mock_fetch.return_value = SAMPLE_ROUTE_DATA
            mock_extract.return_value = SAMPLE_CONTROLS
            fetch_and_summarize_route('12345')

            # Second call: cache hit
            mock_cache.get.return_value = cached_summary
            result = fetch_and_summarize_route('12345')
            assert result == {'rows': [cached_summary]}
            # fetch_route should only have been called once (first call)
            assert mock_fetch.call_count == 1


# ── RWGPS-05: Error handling ────────────────────────────────────────

class TestFetchAndSummarizeRouteErrors:
    @patch('services.chat_tools.cache')
    @patch('services.chat_tools.fetch_route')
    def test_404_not_found_error(self, mock_fetch, mock_cache, app):
        """404/not found produces user-friendly error dict."""
        with app.app_context():
            from services.chat_tools import fetch_and_summarize_route
            mock_cache.get.return_value = None
            mock_fetch.side_effect = Exception("RWGPS route 12345 not found.")

            result = fetch_and_summarize_route('12345')
            assert 'error' in result
            assert 'not found' in result['error'].lower()

    @patch('services.chat_tools.cache')
    @patch('services.chat_tools.fetch_route')
    def test_401_auth_error(self, mock_fetch, mock_cache, app):
        """401/authentication error produces credentials message."""
        with app.app_context():
            from services.chat_tools import fetch_and_summarize_route
            mock_cache.get.return_value = None
            mock_fetch.side_effect = Exception("RWGPS API authentication failed (401).")

            result = fetch_and_summarize_route('12345')
            assert 'error' in result
            assert 'credential' in result['error'].lower() or 'auth' in result['error'].lower()

    @patch('services.chat_tools.cache')
    @patch('services.chat_tools.fetch_route')
    def test_429_rate_limit_error(self, mock_fetch, mock_cache, app):
        """429/rate limited produces rate limit message."""
        with app.app_context():
            from services.chat_tools import fetch_and_summarize_route
            mock_cache.get.return_value = None
            mock_fetch.side_effect = Exception("RWGPS API rate limited.")

            result = fetch_and_summarize_route('12345')
            assert 'error' in result
            assert 'rate limit' in result['error'].lower()

    @patch('services.chat_tools.cache')
    @patch('services.chat_tools.fetch_route')
    def test_generic_error(self, mock_fetch, mock_cache, app):
        """Generic exception produces 'Could not fetch' message."""
        with app.app_context():
            from services.chat_tools import fetch_and_summarize_route
            mock_cache.get.return_value = None
            mock_fetch.side_effect = Exception("Something unexpected happened")

            result = fetch_and_summarize_route('12345')
            assert 'error' in result
            assert 'could not fetch' in result['error'].lower()

    @patch('services.chat_tools.cache')
    @patch('services.chat_tools.fetch_route')
    @patch('services.chat_tools.extract_controls')
    def test_no_waypoints_falls_back_to_minimal_summary(self, mock_extract, mock_fetch, mock_cache, app):
        """extract_controls() raising falls back to minimal summary."""
        with app.app_context():
            from services.chat_tools import fetch_and_summarize_route
            mock_cache.get.return_value = None
            mock_fetch.return_value = SAMPLE_ROUTE_DATA
            mock_extract.side_effect = Exception("This route has no waypoints/POIs.")

            result = fetch_and_summarize_route('12345')
            assert 'rows' in result
            summary = result['rows'][0]
            assert summary['name'] == 'SFR 300K Brevet'
            assert 'control_stops' not in summary or summary['control_stops'] == []
            assert summary['source'] == 'live_rwgps_api'


# ── RWGPS-02/07: Agent loop integration — route_discussion with live RWGPS fallback ──

def _mock_stream(messages, accumulator, **kwargs):
    """Helper that simulates _stream_completion yielding one chunk."""
    accumulator['full_content'] = 'Route info here'
    accumulator['prompt_tokens'] = 100
    accumulator['completion_tokens'] = 20
    yield 'data: "Route info here"\n\n'


class TestRouteDiscussionLiveFetch:
    """Agent loop integration tests for route_discussion with cache-first + live RWGPS fallback."""

    def test_cached_ride_plan_returns_immediately(self, app):
        """When get_ride_plan returns rows, no live RWGPS fetch is attempted (RWGPS-04)."""
        with app.app_context():
            from services.chat_service import run_agent_loop, IntentResult

            intent = IntentResult(intent='route_discussion', ride_name='SFR 300K')
            cached_plan = {'rows': [{'name': 'SFR 300K', 'plan': 'Start at 5am...'}]}

            with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
                 patch('services.chat_service.execute_allowed_query', return_value=cached_plan) as mock_exec, \
                 patch('services.chat_service.fetch_and_summarize_route') as mock_live, \
                 patch('services.chat_service._stream_completion', side_effect=_mock_stream):

                messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'test'}]
                list(run_agent_loop(MagicMock(), 'Tell me about SFR 300K', messages, rider_id=5, user_id=1))

                # get_ride_plan called once, live fetch NOT called
                mock_exec.assert_called_once_with(
                    query_type='get_ride_plan',
                    params=('SFR 300K', 'SFR 300K'),
                    user_id=1,
                )
                mock_live.assert_not_called()

    def test_no_cached_plan_triggers_live_fetch(self, app):
        """When get_ride_plan returns no rows, falls back to RWGPS URL lookup + live fetch."""
        with app.app_context():
            from services.chat_service import run_agent_loop, IntentResult

            intent = IntentResult(intent='route_discussion', ride_name='SFR 300K')
            empty_plan = {'rows': []}
            url_result = {'rows': [{'name': 'SFR 300K Brevet', 'rwgps_url': 'https://ridewithgps.com/routes/12345', 'distance_km': 300}]}
            live_data = {'rows': [{'name': 'SFR 300K Brevet', 'source': 'live_rwgps_api'}]}

            def mock_exec_side_effect(query_type, params, user_id):
                if query_type == 'get_ride_plan':
                    return empty_plan
                elif query_type == 'get_ride_rwgps_url':
                    return url_result
                return {'rows': []}

            with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
                 patch('services.chat_service.execute_allowed_query', side_effect=mock_exec_side_effect) as mock_exec, \
                 patch('services.chat_service.extract_rwgps_route_id', return_value='12345') as mock_extract_id, \
                 patch('services.chat_service.fetch_and_summarize_route', return_value=live_data) as mock_live, \
                 patch('services.chat_service._stream_completion', side_effect=_mock_stream):

                messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'test'}]
                chunks = list(run_agent_loop(MagicMock(), 'Tell me about SFR 300K', messages, rider_id=5, user_id=1))

                # Both queries made
                assert mock_exec.call_count == 2
                mock_extract_id.assert_called_once_with('https://ridewithgps.com/routes/12345')
                mock_live.assert_called_once_with('12345')

    def test_no_rwgps_url_rows_no_live_fetch(self, app):
        """When get_ride_rwgps_url returns no rows, no live fetch is attempted."""
        with app.app_context():
            from services.chat_service import run_agent_loop, IntentResult

            intent = IntentResult(intent='route_discussion', ride_name='Unknown Route')

            def mock_exec_side_effect(query_type, params, user_id):
                if query_type == 'get_ride_plan':
                    return {'rows': []}
                elif query_type == 'get_ride_rwgps_url':
                    return {'rows': []}
                return {'rows': []}

            with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
                 patch('services.chat_service.execute_allowed_query', side_effect=mock_exec_side_effect), \
                 patch('services.chat_service.fetch_and_summarize_route') as mock_live, \
                 patch('services.chat_service._stream_completion', side_effect=_mock_stream):

                messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'test'}]
                list(run_agent_loop(MagicMock(), 'Tell me about Unknown Route', messages, rider_id=5, user_id=1))

                mock_live.assert_not_called()

    def test_null_rwgps_url_no_live_fetch(self, app):
        """When ride row has null rwgps_url, no live fetch is attempted."""
        with app.app_context():
            from services.chat_service import run_agent_loop, IntentResult

            intent = IntentResult(intent='route_discussion', ride_name='Old Route')

            def mock_exec_side_effect(query_type, params, user_id):
                if query_type == 'get_ride_plan':
                    return {'rows': []}
                elif query_type == 'get_ride_rwgps_url':
                    return {'rows': [{'name': 'Old Route 200', 'rwgps_url': None, 'distance_km': 200}]}
                return {'rows': []}

            with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
                 patch('services.chat_service.execute_allowed_query', side_effect=mock_exec_side_effect), \
                 patch('services.chat_service.fetch_and_summarize_route') as mock_live, \
                 patch('services.chat_service._stream_completion', side_effect=_mock_stream):

                messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'test'}]
                list(run_agent_loop(MagicMock(), 'Tell me about Old Route', messages, rider_id=5, user_id=1))

                mock_live.assert_not_called()

    def test_malformed_rwgps_url_no_live_fetch(self, app):
        """When extract_rwgps_route_id returns None (malformed URL), no live fetch."""
        with app.app_context():
            from services.chat_service import run_agent_loop, IntentResult

            intent = IntentResult(intent='route_discussion', ride_name='Bad URL Route')

            def mock_exec_side_effect(query_type, params, user_id):
                if query_type == 'get_ride_plan':
                    return {'rows': []}
                elif query_type == 'get_ride_rwgps_url':
                    return {'rows': [{'name': 'Bad URL Route', 'rwgps_url': 'not-a-valid-url', 'distance_km': 200}]}
                return {'rows': []}

            with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
                 patch('services.chat_service.execute_allowed_query', side_effect=mock_exec_side_effect), \
                 patch('services.chat_service.extract_rwgps_route_id', return_value=None), \
                 patch('services.chat_service.fetch_and_summarize_route') as mock_live, \
                 patch('services.chat_service._stream_completion', side_effect=_mock_stream):

                messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'test'}]
                list(run_agent_loop(MagicMock(), 'Tell me about Bad URL Route', messages, rider_id=5, user_id=1))

                mock_live.assert_not_called()

    def test_db_query_count_incremented_correctly(self, app):
        """db_query_count incremented twice: once for get_ride_plan, once for get_ride_rwgps_url."""
        with app.app_context():
            from services.chat_service import run_agent_loop, IntentResult

            intent = IntentResult(intent='route_discussion', ride_name='SFR 300K')

            call_log = []

            def mock_exec_side_effect(query_type, params, user_id):
                call_log.append(query_type)
                if query_type == 'get_ride_plan':
                    return {'rows': []}
                elif query_type == 'get_ride_rwgps_url':
                    return {'rows': [{'name': 'SFR 300K', 'rwgps_url': 'https://ridewithgps.com/routes/12345', 'distance_km': 300}]}
                return {'rows': []}

            with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
                 patch('services.chat_service.execute_allowed_query', side_effect=mock_exec_side_effect), \
                 patch('services.chat_service.extract_rwgps_route_id', return_value='12345'), \
                 patch('services.chat_service.fetch_and_summarize_route', return_value={'rows': [{'name': 'test'}]}), \
                 patch('services.chat_service._stream_completion', side_effect=_mock_stream):

                messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'test'}]
                list(run_agent_loop(MagicMock(), 'Tell me about SFR 300K', messages, rider_id=5, user_id=1))

                assert call_log == ['get_ride_plan', 'get_ride_rwgps_url']

    def test_live_route_data_tool_result_appended(self, app):
        """Live route data is appended to tool_results with tool='live_route_data'."""
        with app.app_context():
            from services.chat_service import run_agent_loop, IntentResult, _format_tool_results
            import json

            intent = IntentResult(intent='route_discussion', ride_name='SFR 300K')
            live_data = {'rows': [{'name': 'SFR 300K Brevet', 'source': 'live_rwgps_api'}]}

            def mock_exec_side_effect(query_type, params, user_id):
                if query_type == 'get_ride_plan':
                    return {'rows': []}
                elif query_type == 'get_ride_rwgps_url':
                    return {'rows': [{'name': 'SFR 300K', 'rwgps_url': 'https://ridewithgps.com/routes/12345', 'distance_km': 300}]}
                return {'rows': []}

            captured_messages = []

            def mock_stream(messages, accumulator, **kwargs):
                captured_messages.extend(messages)
                accumulator['full_content'] = 'Route info'
                accumulator['prompt_tokens'] = 100
                accumulator['completion_tokens'] = 20
                yield 'data: "Route info"\n\n'

            with patch('services.chat_service.classify_intent', return_value=(intent, MagicMock())), \
                 patch('services.chat_service.execute_allowed_query', side_effect=mock_exec_side_effect), \
                 patch('services.chat_service.extract_rwgps_route_id', return_value='12345'), \
                 patch('services.chat_service.fetch_and_summarize_route', return_value=live_data), \
                 patch('services.chat_service._stream_completion', side_effect=mock_stream):

                messages = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'test'}]
                list(run_agent_loop(MagicMock(), 'Tell me about SFR 300K', messages, rider_id=5, user_id=1))

                # Check that tool_results were injected into messages
                system_msgs = [m for m in captured_messages if m['role'] == 'system']
                tool_content = [m['content'] for m in system_msgs if 'live_route_data' in m['content']]
                assert len(tool_content) > 0
                assert 'live_rwgps_api' in tool_content[0]


# ── RWGPS-07: Intent prompt update ──────────────────────────────────

class TestIntentPromptLiveRoute:
    def test_intent_prompt_mentions_live_route(self, app):
        """Intent classification prompt describes live RWGPS route data capability."""
        with app.app_context():
            from services.chat_service import INTENT_CLASSIFICATION_PROMPT
            assert 'RideWithGPS' in INTENT_CLASSIFICATION_PROMPT
            assert 'live route data' in INTENT_CLASSIFICATION_PROMPT or 'live' in INTENT_CLASSIFICATION_PROMPT
