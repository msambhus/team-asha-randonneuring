"""Tests for brevet comparison feature.

Covers: get_rider_rides_with_cached_streams model function,
build_brevet_comparison_data service function,
/my/brevet-comparison route handler.
"""
import json
import zlib
import pytest
from unittest.mock import patch, MagicMock
from datetime import date


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_streams(n_points=100, total_distance_m=321869, total_time_s=43200):
    """Build a synthetic Strava streams dict."""
    distance = [total_distance_m * i / (n_points - 1) for i in range(n_points)]
    time = [total_time_s * i / (n_points - 1) for i in range(n_points)]
    # Simulate a stop in the middle: velocity drops to 0 for 10 consecutive points
    stop_start = n_points // 2
    stop_end = stop_start + 10
    velocity = [4.5] * n_points  # ~10 mph
    for i in range(stop_start, min(stop_end, n_points)):
        velocity[i] = 0.0
    # Adjust time to account for stop (add 600 seconds = 10 min)
    for i in range(stop_end, n_points):
        time[i] += 600
    return {
        'distance': distance,
        'time': time,
        'velocity_smooth': velocity,
    }


def _compress(streams_dict):
    """Compress streams dict the same way the app does."""
    return zlib.compress(json.dumps(streams_dict).encode(), level=6)


def _make_ride_row(ride_id=1, ride_name='Test 200km Brevet', ride_date=date(2026, 3, 15),
                   distance_km=200, season_name='2025-2026', n_points=100, **kwargs):
    """Build a mock row as returned by get_rider_rides_with_cached_streams."""
    streams = _make_streams(n_points=n_points)
    return {
        'ride_id': ride_id,
        'ride_name': ride_name,
        'date': ride_date,
        'distance_km': distance_km,
        'elevation_ft': 5000,
        'season_name': season_name,
        'match_id': ride_id * 10,
        'elapsed_time': 43200,
        'moving_time': 36000,
        'strava_distance_m': 321869,
        'total_elevation_gain': 1524,
        'activity_streams': _compress(streams),
        **kwargs,
    }


# ---------------------------------------------------------------------------
# Service tests: build_brevet_comparison_data
# ---------------------------------------------------------------------------

class TestBuildBrevetComparisonData:

    def test_empty_rides_list(self, app):
        from services.strava_analysis import build_brevet_comparison_data
        with app.app_context():
            result = build_brevet_comparison_data([])
        assert result == []

    def test_single_ride_returns_points(self, app):
        from services.strava_analysis import build_brevet_comparison_data
        ride = _make_ride_row(ride_id=1, n_points=50)
        with app.app_context():
            result = build_brevet_comparison_data([ride])
        assert len(result) == 1
        r = result[0]
        assert r['ride_id'] == 1
        assert r['ride_name'] == 'Test 200km Brevet'
        assert r['distance_km'] == 200
        assert len(r['points']) > 0
        # Points should have x (miles) and y (hours)
        p = r['points'][0]
        assert 'x' in p and 'y' in p
        assert r['points'][0]['x'] == 0  # first point is 0 distance
        assert r['points'][0]['y'] == 0  # first point is 0 time
        # Last point should be near total distance
        assert r['distance_miles'] > 0
        assert r['elapsed_time_hrs'] > 0

    def test_downsampling(self, app):
        from services.strava_analysis import build_brevet_comparison_data
        ride = _make_ride_row(ride_id=1, n_points=5000)
        with app.app_context():
            result = build_brevet_comparison_data([ride], max_points=500)
        assert len(result) == 1
        # Should be downsampled to approximately max_points (plus preserved stop boundaries)
        n_points = len(result[0]['points'])
        assert n_points <= 600  # some margin for stop-boundary preservation
        assert n_points >= 400

    def test_stops_detected(self, app):
        from services.strava_analysis import build_brevet_comparison_data
        ride = _make_ride_row(ride_id=1, n_points=200)
        with app.app_context():
            result = build_brevet_comparison_data([ride])
        stops = result[0]['stops']
        # Our synthetic data has a stop in the middle
        assert len(stops) >= 1
        assert 'distance_miles' in stops[0]
        assert 'duration_min' in stops[0]

    def test_missing_streams_blob_skipped(self, app):
        from services.strava_analysis import build_brevet_comparison_data
        ride = _make_ride_row(ride_id=1)
        ride['activity_streams'] = None
        with app.app_context():
            result = build_brevet_comparison_data([ride])
        assert result == []

    def test_corrupt_streams_skipped(self, app):
        from services.strava_analysis import build_brevet_comparison_data
        ride = _make_ride_row(ride_id=1)
        ride['activity_streams'] = b'not valid zlib data'
        with app.app_context():
            result = build_brevet_comparison_data([ride])
        assert result == []

    def test_multiple_rides(self, app):
        from services.strava_analysis import build_brevet_comparison_data
        rides = [
            _make_ride_row(ride_id=1, ride_name='200km Brevet'),
            _make_ride_row(ride_id=2, ride_name='300km Brevet', distance_km=300),
        ]
        with app.app_context():
            result = build_brevet_comparison_data(rides)
        assert len(result) == 2
        assert result[0]['ride_name'] == '200km Brevet'
        assert result[1]['ride_name'] == '300km Brevet'

    def test_date_serialization(self, app):
        from services.strava_analysis import build_brevet_comparison_data
        ride = _make_ride_row(ride_id=1, ride_date=date(2026, 3, 15))
        with app.app_context():
            result = build_brevet_comparison_data([ride])
        assert result[0]['date'] == '2026-03-15'


# ---------------------------------------------------------------------------
# Model tests: get_rider_rides_with_cached_streams
# ---------------------------------------------------------------------------

class TestGetRiderRidesWithCachedStreams:

    @patch('models._execute')
    def test_returns_results(self, mock_execute):
        from models import get_rider_rides_with_cached_streams
        mock_execute.return_value.fetchall.return_value = [
            {'ride_id': 1, 'ride_name': 'Test', 'date': date(2026, 3, 15),
             'distance_km': 200, 'season_name': '2025-2026'},
        ]
        result = get_rider_rides_with_cached_streams(rider_id=42)
        assert len(result) == 1
        assert result[0]['ride_id'] == 1

    @patch('models._execute')
    def test_returns_empty_for_no_matches(self, mock_execute):
        from models import get_rider_rides_with_cached_streams
        mock_execute.return_value.fetchall.return_value = []
        result = get_rider_rides_with_cached_streams(rider_id=42)
        assert result == []

    @patch('models._execute')
    def test_passes_rider_id_and_finished_status(self, mock_execute):
        from models import get_rider_rides_with_cached_streams, RideStatus
        mock_execute.return_value.fetchall.return_value = []
        get_rider_rides_with_cached_streams(rider_id=42)
        args = mock_execute.call_args[0]
        # Second arg is the params tuple
        assert args[1] == (42, RideStatus.FINISHED.value)


# ---------------------------------------------------------------------------
# Route tests: /my/brevet-comparison
# ---------------------------------------------------------------------------

class TestBrevetComparisonRoute:

    def test_unauthenticated_redirects_to_login(self, client):
        resp = client.get('/my/brevet-comparison')
        assert resp.status_code == 302
        assert 'login' in resp.headers['Location'].lower()

    @patch('models._execute')
    @patch('models.get_strava_connection')
    @patch('models.get_rider_rides_with_cached_streams')
    @patch('services.strava_analysis.build_brevet_comparison_data')
    def test_renders_with_rides(self, mock_build, mock_get_rides, mock_strava_conn,
                                mock_execute, client):
        mock_execute.return_value.fetchone.return_value = {
            'id': 1, 'first_name': 'Test', 'last_name': 'Rider', 'rusa_id': 1001,
            'photo_filename': None,
        }
        mock_strava_conn.return_value = {'rider_id': 1}
        mock_get_rides.return_value = []
        mock_build.return_value = [
            {'ride_id': 1, 'ride_name': 'Test 200km', 'date': '2026-03-15',
             'distance_km': 200, 'season_name': '2025-2026',
             'elapsed_time_hrs': 12.0, 'distance_miles': 124.3,
             'points': [{'x': 0, 'y': 0}], 'stops': []},
            {'ride_id': 2, 'ride_name': 'Test 300km', 'date': '2026-04-12',
             'distance_km': 300, 'season_name': '2025-2026',
             'elapsed_time_hrs': 18.0, 'distance_miles': 186.4,
             'points': [{'x': 0, 'y': 0}], 'stops': []},
        ]

        with client.session_transaction() as sess:
            sess['user_id'] = 'test@example.com'
            sess['rider_id'] = 1

        resp = client.get('/my/brevet-comparison')
        assert resp.status_code == 200
        assert b'Compare My Brevets' in resp.data

    @patch('models._execute')
    @patch('models.get_strava_connection')
    @patch('models.get_rider_rides_with_cached_streams')
    @patch('services.strava_analysis.build_brevet_comparison_data')
    def test_empty_state_with_fewer_than_2_rides(self, mock_build, mock_get_rides,
                                                  mock_strava_conn, mock_execute, client):
        mock_execute.return_value.fetchone.return_value = {
            'id': 1, 'first_name': 'Test', 'last_name': 'Rider', 'rusa_id': 1001,
            'photo_filename': None,
        }
        mock_strava_conn.return_value = {'rider_id': 1}
        mock_get_rides.return_value = []
        mock_build.return_value = [
            {'ride_id': 1, 'ride_name': 'Test 200km', 'date': '2026-03-15',
             'distance_km': 200, 'season_name': '2025-2026',
             'elapsed_time_hrs': 12.0, 'distance_miles': 124.3,
             'points': [{'x': 0, 'y': 0}], 'stops': []},
        ]

        with client.session_transaction() as sess:
            sess['user_id'] = 'test@example.com'
            sess['rider_id'] = 1

        resp = client.get('/my/brevet-comparison')
        assert resp.status_code == 200
        assert b'Not Enough Rides' in resp.data

    def test_no_strava_connection_redirects(self, client):
        with patch('models._execute') as mock_execute, \
             patch('models.get_strava_connection') as mock_strava_conn:
            mock_execute.return_value.fetchone.return_value = {
                'id': 1, 'first_name': 'Test', 'last_name': 'Rider', 'rusa_id': 1001,
                'photo_filename': None,
            }
            mock_strava_conn.return_value = None

            with client.session_transaction() as sess:
                sess['user_id'] = 'test@example.com'
                sess['rider_id'] = 1

            resp = client.get('/my/brevet-comparison')
            assert resp.status_code == 302
