"""Tests for weather map API endpoint (/api/weather-map)."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


# ── Sample data ─────────────────────────────────────────────────────

SAMPLE_TRACK_POINTS = [
    {'y': 37.77, 'x': -122.41, 'd': 0, 'e': 10},
    {'y': 37.70, 'x': -122.30, 'd': 25000, 'e': 50},
    {'y': 37.60, 'x': -122.20, 'd': 50000, 'e': 100},
    {'y': 37.50, 'x': -122.10, 'd': 75000, 'e': 80},
    {'y': 37.40, 'x': -122.00, 'd': 100000, 'e': 150},
    {'y': 37.30, 'x': -121.90, 'd': 125000, 'e': 200},
    {'y': 37.20, 'x': -121.80, 'd': 150000, 'e': 50},
    {'y': 37.10, 'x': -121.70, 'd': 175000, 'e': 30},
    {'y': 37.00, 'x': -121.60, 'd': 200000, 'e': 20},
    {'y': 36.97, 'x': -121.55, 'd': 210000, 'e': 10},
]

SAMPLE_ROUTE_DATA = {
    'id': 12345,
    'name': 'SFR 300K',
    'distance': 310000,
    'elevation_gain': 3000,
    'track_points': SAMPLE_TRACK_POINTS,
    'course_points': [
        {'t': 'Start', 'n': 'Start Location', 'd': 0, 'e': 10},
        {'t': 'Control', 'n': 'Control 1', 'd': 100000, 'e': 150},
        {'t': 'End', 'n': 'Finish', 'd': 210000, 'e': 10},
    ],
}

HOURLY_TIMES = [f"2026-03-17T{h:02d}:00" for h in range(24)]

SAMPLE_WEATHER_DATA = [
    {
        'hourly': {
            'time': HOURLY_TIMES,
            'temperature_2m': [12.0] * 24,
            'wind_speed_10m': [20.0] * 24,
            'wind_direction_10m': [270] * 24,
            'precipitation_probability': [10] * 24,
            'weather_code': [0] * 24,
        }
    },
    {
        'hourly': {
            'time': HOURLY_TIMES,
            'temperature_2m': [8.0] * 24,
            'wind_speed_10m': [25.0] * 24,
            'wind_direction_10m': [180] * 24,
            'precipitation_probability': [50] * 24,
            'weather_code': [63] * 24,
        }
    },
]


# ── Endpoint tests ──────────────────────────────────────────────────

class TestWeatherMapPage:
    def test_get_page_returns_200(self, client):
        resp = client.get('/weather')
        assert resp.status_code == 200
        assert b'Route Weather Forecast' in resp.data


class TestWeatherMapAPI:
    def test_missing_url_returns_400(self, client):
        resp = client.post('/api/weather-map',
                           json={},
                           content_type='application/json')
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'error' in data

    def test_invalid_url_returns_400(self, client):
        resp = client.post('/api/weather-map',
                           json={'rwgps_url': 'https://example.com/not-a-route'},
                           content_type='application/json')
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'route ID' in data['error'].lower() or 'url' in data['error'].lower()

    def test_empty_url_returns_400(self, client):
        resp = client.post('/api/weather-map',
                           json={'rwgps_url': '   '},
                           content_type='application/json')
        assert resp.status_code == 400

    @patch('routes.weather.get_cached_route_weather')
    @patch('routes.weather.fetch_route')
    def test_happy_path_returns_imperial_units(self, mock_fetch, mock_weather, client):
        mock_fetch.return_value = SAMPLE_ROUTE_DATA
        mock_weather.return_value = SAMPLE_WEATHER_DATA

        resp = client.post('/api/weather-map',
                           json={'rwgps_url': 'https://ridewithgps.com/routes/12345'},
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()

        assert data['route_name'] == 'SFR 300K'
        assert 'total_distance_mi' in data
        assert 'table_segments' in data
        assert 'map_segments' in data
        assert 'polyline' in data
        assert 'ride_summary' in data
        assert 'temp_range' in data
        assert 'min_f' in data['temp_range']
        assert 'max_f' in data['temp_range']

    @patch('routes.weather.get_cached_route_weather')
    @patch('routes.weather.fetch_route')
    def test_table_segments_have_imperial_fields(self, mock_fetch, mock_weather, client):
        mock_fetch.return_value = SAMPLE_ROUTE_DATA
        mock_weather.return_value = SAMPLE_WEATHER_DATA

        resp = client.post('/api/weather-map',
                           json={'rwgps_url': 'https://ridewithgps.com/routes/12345'},
                           content_type='application/json')
        data = resp.get_json()

        for seg in data['table_segments']:
            assert 'distance_mi' in seg, 'segment missing distance_mi'
            assert 'temperature_f' in seg, 'segment missing temperature_f'
            assert 'wind_speed_mph' in seg, 'segment missing wind_speed_mph'
            assert 'headwind_mph' in seg, 'segment missing headwind_mph'
            assert 'wind_label' in seg
            assert 'wind_direction_deg' in seg

    @patch('routes.weather.get_cached_route_weather')
    @patch('routes.weather.fetch_route')
    def test_map_segments_have_lat_lng_bearing(self, mock_fetch, mock_weather, client):
        mock_fetch.return_value = SAMPLE_ROUTE_DATA
        mock_weather.return_value = SAMPLE_WEATHER_DATA

        resp = client.post('/api/weather-map',
                           json={'rwgps_url': 'https://ridewithgps.com/routes/12345'},
                           content_type='application/json')
        data = resp.get_json()

        for seg in data['map_segments']:
            assert 'lat' in seg, 'segment missing lat'
            assert 'lng' in seg, 'segment missing lng'
            assert 'rider_bearing_deg' in seg, 'segment missing rider_bearing_deg'
            assert 'wind_speed_mph' in seg

    @patch('routes.weather.get_cached_route_weather')
    @patch('routes.weather.fetch_route')
    def test_map_segments_denser_than_table(self, mock_fetch, mock_weather, client):
        """Map segments use 10km interval vs 50km for table — should have more points."""
        mock_fetch.return_value = SAMPLE_ROUTE_DATA
        mock_weather.return_value = SAMPLE_WEATHER_DATA

        resp = client.post('/api/weather-map',
                           json={'rwgps_url': 'https://ridewithgps.com/routes/12345'},
                           content_type='application/json')
        data = resp.get_json()

        assert len(data['map_segments']) >= len(data['table_segments'])

    @patch('routes.weather.get_cached_route_weather')
    @patch('routes.weather.fetch_route')
    def test_polyline_is_decimated(self, mock_fetch, mock_weather, client):
        mock_fetch.return_value = SAMPLE_ROUTE_DATA
        mock_weather.return_value = SAMPLE_WEATHER_DATA

        resp = client.post('/api/weather-map',
                           json={'rwgps_url': 'https://ridewithgps.com/routes/12345'},
                           content_type='application/json')
        data = resp.get_json()

        assert len(data['polyline']) <= len(SAMPLE_TRACK_POINTS)
        assert len(data['polyline']) >= 2

    @patch('routes.weather.get_cached_route_weather')
    @patch('routes.weather.fetch_route')
    def test_cue_points_in_miles(self, mock_fetch, mock_weather, client):
        mock_fetch.return_value = SAMPLE_ROUTE_DATA
        mock_weather.return_value = SAMPLE_WEATHER_DATA

        resp = client.post('/api/weather-map',
                           json={'rwgps_url': 'https://ridewithgps.com/routes/12345'},
                           content_type='application/json')
        data = resp.get_json()

        assert 'cue_points' in data
        assert len(data['cue_points']) >= 2
        for cp in data['cue_points']:
            assert 'distance_mi' in cp

    @patch('routes.weather.fetch_route')
    def test_rwgps_404_returns_error(self, mock_fetch, client):
        mock_fetch.side_effect = Exception('RWGPS route 99999 not found.')

        resp = client.post('/api/weather-map',
                           json={'rwgps_url': 'https://ridewithgps.com/routes/99999'},
                           content_type='application/json')
        assert resp.status_code == 502
        data = resp.get_json()
        assert 'could not fetch' in data['error'].lower() or 'ridewithgps' in data['error'].lower()

    @patch('routes.weather.fetch_route')
    def test_no_track_points_returns_400(self, mock_fetch, client):
        mock_fetch.return_value = {'name': 'Empty', 'distance': 100000, 'track_points': []}

        resp = client.post('/api/weather-map',
                           json={'rwgps_url': 'https://ridewithgps.com/routes/12345'},
                           content_type='application/json')
        assert resp.status_code == 400
        assert 'track data' in resp.get_json()['error'].lower()

    def test_date_beyond_forecast_window(self, client):
        """Date validation happens before RWGPS fetch — no API call needed."""
        far_future = (datetime.now() + timedelta(days=20)).strftime('%Y-%m-%dT07:00')
        resp = client.post('/api/weather-map',
                           json={
                               'rwgps_url': 'https://ridewithgps.com/routes/12345',
                               'start_datetime': far_future,
                           },
                           content_type='application/json')
        assert resp.status_code == 400
        assert '16 days' in resp.get_json()['error']

    @patch('routes.weather.get_cached_route_weather')
    @patch('routes.weather.fetch_route')
    def test_custom_start_datetime(self, mock_fetch, mock_weather, client):
        mock_fetch.return_value = SAMPLE_ROUTE_DATA
        mock_weather.return_value = SAMPLE_WEATHER_DATA
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%dT06:00')

        resp = client.post('/api/weather-map',
                           json={
                               'rwgps_url': 'https://ridewithgps.com/routes/12345',
                               'start_datetime': tomorrow,
                           },
                           content_type='application/json')
        assert resp.status_code == 200

    @patch('routes.weather.get_cached_route_weather')
    @patch('routes.weather.fetch_route')
    def test_weather_api_failure_returns_503(self, mock_fetch, mock_weather, client):
        mock_fetch.return_value = SAMPLE_ROUTE_DATA
        mock_weather.side_effect = Exception('Open-Meteo timeout')

        resp = client.post('/api/weather-map',
                           json={'rwgps_url': 'https://ridewithgps.com/routes/12345'},
                           content_type='application/json')
        assert resp.status_code == 503
        assert 'unavailable' in resp.get_json()['error'].lower()


class TestUnitConversions:
    def test_c_to_f(self):
        from routes.weather import _c_to_f
        assert _c_to_f(0) == 32.0
        assert _c_to_f(100) == 212.0

    def test_kmh_to_mph(self):
        from routes.weather import _kmh_to_mph
        assert abs(_kmh_to_mph(10) - 6.2) < 0.2

    def test_km_to_mi(self):
        from routes.weather import _km_to_mi
        assert abs(_km_to_mi(100) - 62.1) < 0.2
