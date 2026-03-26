"""Tests for Strava activity stream caching (compress, decompress, cache-hit logic)."""

import json
import zlib
from unittest.mock import patch, MagicMock

import pytest


# ── Compression Roundtrip ────────────────────────────────────────────

class TestStreamCompression:
    def test_compress_decompress_roundtrip(self):
        """Compressing then decompressing returns identical dict."""
        from services.strava_analysis import _compress_streams, _decompress_streams

        original = {
            'time': list(range(1000)),
            'distance': [i * 5.0 for i in range(1000)],
            'velocity_smooth': [4.5] * 1000,
            'heartrate': [140 + (i % 20) for i in range(1000)],
            'watts': [200 + (i % 50) for i in range(1000)],
            'cadence': [90] * 1000,
            'latlng': [[37.7 + i * 0.0001, -122.4 + i * 0.0001] for i in range(1000)],
        }

        compressed = _compress_streams(original)
        assert isinstance(compressed, bytes)
        assert len(compressed) < len(json.dumps(original).encode())  # actually compressed

        restored = _decompress_streams(compressed)
        assert restored == original

    def test_compress_empty_streams(self):
        """Empty streams dict roundtrips correctly."""
        from services.strava_analysis import _compress_streams, _decompress_streams

        empty = {}
        restored = _decompress_streams(_compress_streams(empty))
        assert restored == {}

    def test_decompress_handles_memoryview(self):
        """psycopg2 returns memoryview for BYTEA — decompress handles it."""
        from services.strava_analysis import _compress_streams, _decompress_streams

        original = {'time': [1, 2, 3], 'distance': [0, 100, 200]}
        compressed = _compress_streams(original)
        # Simulate psycopg2 returning memoryview
        mv = memoryview(compressed)
        restored = _decompress_streams(mv)
        assert restored == original


# ── Expanded Stream Keys ─────────────────────────────────────────────

class TestStreamKeys:
    def test_stream_keys_include_new_types(self):
        """The API request should ask for all 9 stream types."""
        from services.strava_analysis import _STREAM_KEYS

        keys = set(_STREAM_KEYS.split(','))
        assert 'cadence' in keys
        assert 'altitude' in keys
        assert 'grade_smooth' in keys
        assert 'latlng' in keys
        assert 'time' in keys
        assert 'distance' in keys
        assert 'velocity_smooth' in keys
        assert 'heartrate' in keys
        assert 'watts' in keys
        assert len(keys) == 9


# ── Cache Hit / Miss Logic ──────────────────────────────────────────

class TestFetchAndAnalyzeCaching:
    """Test that cached streams skip the Strava API call."""

    def _make_cached_row(self, with_streams=True, with_error=None):
        """Build a fake strava_ride_analysis DB row."""
        from services.strava_analysis import _compress_streams

        streams = {'time': [0, 1, 2], 'distance': [0, 50, 100],
                   'velocity_smooth': [5.0, 5.0, 5.0]}
        row = {
            'match_id': 1,
            'detected_stops': [{'distance_miles': 10, 'duration_s': 300}],
            'stream_summary': {'total_time_s': 100, 'avg_hr': 140},
            'strava_api_error': with_error,
            'activity_streams': _compress_streams(streams) if with_streams else None,
            'streams_fetched_at': '2026-03-25 10:00:00',
        }
        return row

    @patch('services.strava_analysis.http_requests')
    def test_cached_streams_skip_api(self, mock_requests, app):
        """When streams are cached, no HTTP request is made."""
        with app.app_context():
            cached_row = self._make_cached_row(with_streams=True)

            with patch('models.get_strava_ride_analysis', return_value=cached_row), \
                 patch('models.get_strava_connection', return_value={'rider_id': 1}):
                from services.strava_analysis import fetch_and_analyze
                result = fetch_and_analyze(rider_id=1, match_id=1,
                                           strava_activity_id=12345)

            # No HTTP call should have been made
            mock_requests.get.assert_not_called()

            assert result['error'] is None
            assert result['streams']['time'] == [0, 1, 2]
            assert result['detected_stops'] is not None

    @patch('services.strava_analysis.http_requests')
    def test_no_cached_streams_calls_api(self, mock_requests, app):
        """When streams are NOT cached, fetches from Strava API."""
        with app.app_context():
            cached_row = self._make_cached_row(with_streams=False)

            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_resp.status_code = 200
            mock_resp.json.return_value = [
                {'type': 'time', 'data': [0, 1]},
                {'type': 'distance', 'data': [0, 50]},
                {'type': 'velocity_smooth', 'data': [5.0, 5.0]},
            ]
            mock_requests.get.return_value = mock_resp

            with patch('models.get_strava_ride_analysis', return_value=cached_row), \
                 patch('models.get_strava_connection', return_value={'rider_id': 1}), \
                 patch('services.strava._get_valid_token', return_value='fake_token'), \
                 patch('models.upsert_strava_ride_analysis') as mock_upsert:
                from services.strava_analysis import fetch_and_analyze
                result = fetch_and_analyze(rider_id=1, match_id=1,
                                           strava_activity_id=12345)

            # API should have been called
            mock_requests.get.assert_called_once()
            # Streams should be passed to upsert as compressed bytes
            assert mock_upsert.called
            call_kwargs = mock_upsert.call_args
            assert call_kwargs.kwargs.get('compressed_streams') is not None

            assert result['error'] is None
            assert result['streams']['time'] == [0, 1]

    @patch('services.strava_analysis.http_requests')
    def test_error_cached_forces_refetch(self, mock_requests, app):
        """When cached row has an error, it re-fetches from API."""
        with app.app_context():
            cached_row = self._make_cached_row(with_streams=True,
                                                with_error='Previous error')

            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_resp.status_code = 200
            mock_resp.json.return_value = [
                {'type': 'time', 'data': [0, 1, 2]},
                {'type': 'distance', 'data': [0, 50, 100]},
                {'type': 'velocity_smooth', 'data': [5.0, 5.0, 5.0]},
            ]
            mock_requests.get.return_value = mock_resp

            with patch('models.get_strava_ride_analysis', return_value=cached_row), \
                 patch('models.get_strava_connection', return_value={'rider_id': 1}), \
                 patch('services.strava._get_valid_token', return_value='fake_token'), \
                 patch('models.upsert_strava_ride_analysis'):
                from services.strava_analysis import fetch_and_analyze
                result = fetch_and_analyze(rider_id=1, match_id=1,
                                           strava_activity_id=12345)

            # Should have called API because of the error flag
            mock_requests.get.assert_called_once()

    @patch('services.strava_analysis.http_requests')
    def test_first_visit_no_cached_row(self, mock_requests, app):
        """First visit: no cached row at all — fetches from API and stores."""
        with app.app_context():
            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_resp.status_code = 200
            mock_resp.json.return_value = [
                {'type': 'time', 'data': [0, 1, 2, 3]},
                {'type': 'distance', 'data': [0, 100, 200, 300]},
                {'type': 'velocity_smooth', 'data': [5.0, 5.0, 5.0, 5.0]},
            ]
            mock_requests.get.return_value = mock_resp

            with patch('models.get_strava_ride_analysis', return_value=None), \
                 patch('models.get_strava_connection', return_value={'rider_id': 1}), \
                 patch('services.strava._get_valid_token', return_value='fake_token'), \
                 patch('models.upsert_strava_ride_analysis') as mock_upsert:
                from services.strava_analysis import fetch_and_analyze
                result = fetch_and_analyze(rider_id=1, match_id=1,
                                           strava_activity_id=12345)

            mock_requests.get.assert_called_once()
            assert mock_upsert.called
            assert result['error'] is None
            assert len(result['streams']['time']) == 4
