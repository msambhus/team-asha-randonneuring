"""Tests for multi-rider Strava analysis model function and route.

Covers: get_finished_riders_for_ride model function, /ride/<ride_id>/all-strava route,
privacy filtering, match/analysis status handling, cached-only analysis policy.
"""
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_rider(rider_id=1, first_name='Alice', last_name='Smith', rusa_id=1001,
                strava_data_private=False, match_id=None, strava_activity_id=None,
                has_analysis=False, strava_api_error=None, **kwargs):
    """Build a mock rider row as returned by get_finished_riders_for_ride."""
    return {
        'rider_id': rider_id,
        'first_name': first_name,
        'last_name': last_name,
        'rusa_id': rusa_id,
        'strava_data_private': strava_data_private,
        'match_id': match_id,
        'strava_activity_id': strava_activity_id,
        'strava_url': f'https://strava.com/activities/{strava_activity_id}' if strava_activity_id else None,
        'start_date_local': '2026-03-15T07:00:00',
        'distance': 32000,
        'moving_time': 7200,
        'elapsed_time': 8400,
        'total_elevation_gain': 500,
        'average_speed': 4.44,
        'average_heartrate': 145,
        'max_heartrate': 175,
        'has_heartrate': True,
        'average_watts': 180,
        'max_watts': 350,
        'weighted_average_watts': 195,
        'kilojoules': 1300,
        'device_watts': True,
        'suffer_score': 85,
        'has_analysis': has_analysis,
        'strava_api_error': strava_api_error,
        **kwargs,
    }


def _make_ride(ride_id=10, ride_plan_id=5, plan_slug='test-plan', **kwargs):
    """Build a mock ride dict as returned by get_ride_by_id_full."""
    return {
        'id': ride_id,
        'name': 'Test 200km Brevet',
        'date': '2026-03-15',
        'distance_km': 200,
        'ride_plan_id': ride_plan_id,
        'plan_slug': plan_slug,
        'start_time': '07:00',
        'plan_start_time': '07:00',
        **kwargs,
    }


# ---------------------------------------------------------------------------
# Model function tests
# ---------------------------------------------------------------------------

class TestGetFinishedRidersForRide:
    """Tests for the get_finished_riders_for_ride model function."""

    @patch('models._execute')
    def test_returns_list_of_finished_riders(self, mock_execute):
        from models import get_finished_riders_for_ride
        mock_execute.return_value.fetchall.return_value = [
            _make_rider(rider_id=1, first_name='Alice'),
            _make_rider(rider_id=2, first_name='Bob'),
        ]
        result = get_finished_riders_for_ride(10)
        assert len(result) == 2
        assert result[0]['first_name'] == 'Alice'
        assert result[1]['first_name'] == 'Bob'

    @patch('models._execute')
    def test_passes_ride_id_and_finished_status(self, mock_execute):
        from models import get_finished_riders_for_ride, RideStatus
        mock_execute.return_value.fetchall.return_value = []
        get_finished_riders_for_ride(42)
        args = mock_execute.call_args
        assert 42 in args[0][1] or args[0][1][0] == 42
        assert RideStatus.FINISHED.value in args[0][1]

    @patch('models._execute')
    def test_returns_empty_list_when_no_finished_riders(self, mock_execute):
        from models import get_finished_riders_for_ride
        mock_execute.return_value.fetchall.return_value = []
        result = get_finished_riders_for_ride(99)
        assert result == []


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

class TestRideAllStravaAnalysisRoute:
    """Tests for GET /ride/<ride_id>/all-strava."""

    @patch('routes.riders.is_admin_user', return_value=False)
    @patch('services.strava_analysis.build_comparison')
    @patch('services.strava_analysis.fetch_and_analyze')
    @patch('models.get_ride_plan_stops', return_value=[])
    @patch('models.get_finished_riders_for_ride')
    @patch('models.get_ride_by_id_full')
    def test_returns_200_for_valid_ride(self, mock_ride, mock_riders, mock_stops,
                                        mock_fetch, mock_build, mock_admin, client):
        mock_ride.return_value = _make_ride()
        mock_riders.return_value = [_make_rider()]
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get('/ride/10/all-strava')
        assert resp.status_code == 200

    @patch('models.get_ride_by_id_full')
    def test_returns_404_for_nonexistent_ride(self, mock_ride, client):
        mock_ride.return_value = None
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get('/ride/999/all-strava')
        assert resp.status_code == 404

    @patch('routes.riders.is_admin_user', return_value=False)
    @patch('services.strava_analysis.build_comparison')
    @patch('services.strava_analysis.fetch_and_analyze')
    @patch('models.get_ride_plan_stops', return_value=[])
    @patch('models.get_finished_riders_for_ride')
    @patch('models.get_ride_by_id_full')
    def test_private_rider_gets_error_private(self, mock_ride, mock_riders, mock_stops,
                                              mock_fetch, mock_build, mock_admin, client):
        mock_ride.return_value = _make_ride()
        mock_riders.return_value = [
            _make_rider(rider_id=1, strava_data_private=True, match_id=100, has_analysis=True),
        ]
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get('/ride/10/all-strava')
        assert resp.status_code == 200
        # fetch_and_analyze should NOT be called for private riders
        mock_fetch.assert_not_called()

    @patch('routes.riders.is_admin_user', return_value=False)
    @patch('services.strava_analysis.build_comparison')
    @patch('services.strava_analysis.fetch_and_analyze')
    @patch('models.get_ride_plan_stops', return_value=[])
    @patch('models.get_finished_riders_for_ride')
    @patch('models.get_ride_by_id_full')
    def test_rider_without_match_has_match_false(self, mock_ride, mock_riders, mock_stops,
                                                  mock_fetch, mock_build, mock_admin, client):
        mock_ride.return_value = _make_ride()
        mock_riders.return_value = [
            _make_rider(rider_id=1, match_id=None, has_analysis=False),
        ]
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get('/ride/10/all-strava')
        assert resp.status_code == 200
        mock_fetch.assert_not_called()

    @patch('routes.riders.is_admin_user', return_value=False)
    @patch('services.strava_analysis.build_comparison')
    @patch('services.strava_analysis.fetch_and_analyze')
    @patch('models.get_ride_plan_stops', return_value=[])
    @patch('models.get_finished_riders_for_ride')
    @patch('models.get_ride_by_id_full')
    def test_rider_without_cached_analysis_not_fetched(self, mock_ride, mock_riders, mock_stops,
                                                        mock_fetch, mock_build, mock_admin, client):
        """Riders without a strava_ride_analysis row should NOT trigger fetch_and_analyze."""
        mock_ride.return_value = _make_ride()
        mock_riders.return_value = [
            _make_rider(rider_id=1, match_id=100, strava_activity_id=55555, has_analysis=False),
        ]
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get('/ride/10/all-strava')
        assert resp.status_code == 200
        mock_fetch.assert_not_called()

    @patch('routes.riders.is_admin_user', return_value=False)
    @patch('services.strava_analysis.build_comparison')
    @patch('services.strava_analysis.fetch_and_analyze')
    @patch('models.get_ride_plan_stops', return_value=[{'name': 'Start', 'distance_miles': 0}])
    @patch('models.get_finished_riders_for_ride')
    @patch('models.get_ride_by_id_full')
    def test_rider_with_cached_analysis_gets_comparison(self, mock_ride, mock_riders, mock_stops,
                                                         mock_fetch, mock_build, mock_admin, client):
        """Riders WITH cached analysis should have fetch_and_analyze + build_comparison called."""
        mock_ride.return_value = _make_ride()
        mock_riders.return_value = [
            _make_rider(rider_id=1, match_id=100, strava_activity_id=55555, has_analysis=True),
        ]
        mock_fetch.return_value = {
            'detected_stops': [{'name': 'Stop 1'}],
            'stream_summary': {},
            'error': None,
        }
        mock_build.return_value = {
            'rows': [{
                'location': 'Start', 'distance_miles': 0.0, 'stop_type': 'start',
                'is_extra': False, 'plan_stop_duration_min': None,
                'actual_stop_duration_min': None, 'stop_delta_min': None,
                'plan_cum_time_min': None, 'actual_cum_time_min': None,
                'cum_time_delta_min': None, 'plan_time_of_day': None,
                'actual_time_of_day': None,
            }],
            'summary': {'total_stopped_min': 10},
        }
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get('/ride/10/all-strava')
        assert resp.status_code == 200
        mock_fetch.assert_called_once()
        mock_build.assert_called_once()

    @patch('routes.riders.is_admin_user', return_value=False)
    @patch('services.strava_analysis.build_comparison')
    @patch('services.strava_analysis.fetch_and_analyze')
    @patch('models.get_ride_plan_stops', return_value=[])
    @patch('models.get_finished_riders_for_ride')
    @patch('models.get_ride_by_id_full')
    def test_only_finished_riders_included(self, mock_ride, mock_riders, mock_stops,
                                            mock_fetch, mock_build, mock_admin, client):
        """The model function is called with correct params; route trusts it to filter."""
        mock_ride.return_value = _make_ride()
        mock_riders.return_value = [
            _make_rider(rider_id=1, first_name='Finisher'),
        ]
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get('/ride/10/all-strava')
        assert resp.status_code == 200
        mock_riders.assert_called_once_with(10)

    def test_redirects_when_not_logged_in(self, client):
        """Route should redirect to login when no session user_id."""
        resp = client.get('/ride/10/all-strava')
        assert resp.status_code == 302
