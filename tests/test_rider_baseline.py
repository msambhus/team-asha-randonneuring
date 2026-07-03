"""Tests for get_rider_activity_baseline model function.

DB-independent: models._execute is mocked, so no real database is touched.
Covers mean/median computation, the <3-rides empty result, missing-power
handling, and the SQL distance/day filter parameters.
"""
from unittest.mock import patch


def _make_row(distance=200000.0, total_elevation_gain=2000.0, average_speed=8.0,
              average_watts=180.0, weighted_average_watts=200.0,
              average_heartrate=140.0, average_cadence=85.0, suffer_score=120,
              **kwargs):
    """Build a mock strava_activity row as returned by _execute().fetchall()."""
    return {
        'distance': distance,
        'total_elevation_gain': total_elevation_gain,
        'average_speed': average_speed,
        'average_watts': average_watts,
        'weighted_average_watts': weighted_average_watts,
        'average_heartrate': average_heartrate,
        'average_cadence': average_cadence,
        'suffer_score': suffer_score,
        **kwargs,
    }


class TestGetRiderActivityBaseline:

    @patch('models._execute')
    def test_computes_means_and_median(self, mock_execute):
        from models import get_rider_activity_baseline
        # 3 rides, speeds (m/s): 8.0, 9.0, 10.0 -> mph 17.90, 20.13, 22.37
        mock_execute.return_value.fetchall.return_value = [
            _make_row(average_speed=8.0, average_watts=180, weighted_average_watts=200,
                      average_heartrate=140, average_cadence=85, suffer_score=100),
            _make_row(average_speed=9.0, average_watts=200, weighted_average_watts=220,
                      average_heartrate=150, average_cadence=90, suffer_score=150),
            _make_row(average_speed=10.0, average_watts=220, weighted_average_watts=240,
                      average_heartrate=160, average_cadence=95, suffer_score=200),
        ]
        result = get_rider_activity_baseline(1)
        assert result['n_rides'] == 3
        # mean speed mph = 9.0 * 2.23694 = 20.13 (rounded 1dp)
        assert result['avg_speed_mph'] == 20.1
        # median speed = middle 9.0 -> 20.13 -> 20.1
        assert result['median_speed_mph'] == 20.1
        assert result['avg_watts'] == 200          # mean(180,200,220)
        assert result['avg_np_watts'] == 220       # mean(200,220,240)
        assert result['avg_hr'] == 150             # mean(140,150,160)
        assert result['avg_cadence'] == 90         # mean(85,90,95)
        assert result['avg_suffer'] == 150         # mean(100,150,200)
        assert isinstance(result['avg_watts'], int)
        assert isinstance(result['avg_speed_mph'], float)

    @patch('models._execute')
    def test_median_even_count(self, mock_execute):
        from models import get_rider_activity_baseline
        # 4 rides, speeds 8,9,10,11 m/s; median = (9+10)/2 = 9.5 m/s -> 21.25 -> 21.3
        mock_execute.return_value.fetchall.return_value = [
            _make_row(average_speed=8.0),
            _make_row(average_speed=9.0),
            _make_row(average_speed=10.0),
            _make_row(average_speed=11.0),
        ]
        result = get_rider_activity_baseline(1)
        assert result['n_rides'] == 4
        assert result['median_speed_mph'] == 21.3

    @patch('models._execute')
    def test_returns_empty_when_fewer_than_three_rides(self, mock_execute):
        from models import get_rider_activity_baseline
        mock_execute.return_value.fetchall.return_value = [
            _make_row(), _make_row(),
        ]
        assert get_rider_activity_baseline(1) == {}

    @patch('models._execute')
    def test_missing_power_gives_none_watts_but_speed_still_computed(self, mock_execute):
        from models import get_rider_activity_baseline
        mock_execute.return_value.fetchall.return_value = [
            _make_row(average_speed=8.0, average_watts=None,
                      weighted_average_watts=None, average_heartrate=140),
            _make_row(average_speed=9.0, average_watts=None,
                      weighted_average_watts=None, average_heartrate=150),
            _make_row(average_speed=10.0, average_watts=None,
                      weighted_average_watts=None, average_heartrate=160),
        ]
        result = get_rider_activity_baseline(1)
        assert result['avg_watts'] is None
        assert result['avg_np_watts'] is None
        # speed / hr still computed
        assert result['avg_speed_mph'] == 20.1
        assert result['avg_hr'] == 150

    @patch('models._execute')
    def test_partial_power_averages_only_rides_with_power(self, mock_execute):
        from models import get_rider_activity_baseline
        mock_execute.return_value.fetchall.return_value = [
            _make_row(average_watts=200),
            _make_row(average_watts=None),
            _make_row(average_watts=300),
        ]
        result = get_rider_activity_baseline(1)
        # mean over the two rides that have power
        assert result['avg_watts'] == 250

    @patch('models._execute')
    def test_elev_per_mile_computed(self, mock_execute):
        from models import get_rider_activity_baseline
        # distance 160934 m = 100 miles; gain 304.8 m = 1000 ft -> 10 ft/mile
        mock_execute.return_value.fetchall.return_value = [
            _make_row(distance=160934.0, total_elevation_gain=304.8),
            _make_row(distance=160934.0, total_elevation_gain=304.8),
            _make_row(distance=160934.0, total_elevation_gain=304.8),
        ]
        result = get_rider_activity_baseline(1)
        assert result['avg_elev_per_mile_ft'] == 10.0

    @patch('models._execute')
    def test_sql_receives_min_distance_and_days_params(self, mock_execute):
        from models import get_rider_activity_baseline
        mock_execute.return_value.fetchall.return_value = []
        get_rider_activity_baseline(42, days=180, min_distance_km=200)
        args = mock_execute.call_args
        params = args[0][1]
        assert params[0] == 42
        # min_distance_km converted to meters
        assert params[1] == 200 * 1000
        assert params[2] == 180

    @patch('models._execute')
    def test_default_params(self, mock_execute):
        from models import get_rider_activity_baseline
        mock_execute.return_value.fetchall.return_value = []
        get_rider_activity_baseline(7)
        params = mock_execute.call_args[0][1]
        assert params == (7, 180 * 1000, 365)
