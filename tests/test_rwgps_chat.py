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
