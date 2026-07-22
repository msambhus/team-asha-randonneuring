"""Async route-weather cron + stored-read seams (TA-237).

Proves the request path never fetches Open-Meteo live: the hourly cron
(/api/cron/fetch-route-weather) fetches + stores weather, and the read sites serve
the stored payload. All Open-Meteo/RWGPS/DB access is mocked (no real calls, no DB).
"""
import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

CRON_SECRET = 'test-cron-secret'


def _auth():
    return {'Authorization': f'Bearer {CRON_SECRET}'}


def _cfg(app):
    app.config['CRON_SECRET'] = CRON_SECRET


_TRACK = [
    {'x': -122.0, 'y': 37.0, 'd': 0, 'e': 10},
    {'x': -121.8, 'y': 37.0, 'd': 20000, 'e': 40},
    {'x': -121.6, 'y': 37.0, 'd': 40000, 'e': 20},
]


def _forecasts(n=3):
    times = [f'2026-07-20T{h:02d}:00' for h in range(24)]
    return [{'hourly': {'time': times, 'temperature_2m': [15.0] * 24,
                        'wind_speed_10m': [20.0] * 24, 'wind_direction_10m': [270] * 24,
                        'wind_gusts_10m': [25.0] * 24, 'apparent_temperature': [14.0] * 24,
                        'relative_humidity_2m': [50] * 24, 'precipitation_probability': [10] * 24,
                        'precipitation': [0.0] * 24, 'cloud_cover': [20] * 24,
                        'weather_code': [1] * 24}}
            for _ in range(n)]


def _samples(n=3):
    return [{'lat': 37.0, 'lng': -122.0 + i * 0.2, 'distance_m': i * 20000} for i in range(n)]


# ── Cron: auth + horizon + fail-soft ────────────────────────────────

class TestFetchRouteWeatherCron:
    def test_requires_auth(self, client, app):
        _cfg(app)
        resp = client.post('/api/cron/fetch-route-weather')
        assert resp.status_code == 401

    def test_stores_in_horizon_ride(self, client, app):
        _cfg(app)
        ten_days = date.today() + timedelta(days=10)
        targets = [{'ride_id': 1, 'forecast_date': ten_days, 'name': 'SFR 200',
                    'rwgps_url': 'https://ridewithgps.com/routes/555', 'plan_id': 9}]
        saved = {}

        def fake_save(route_id, forecast_date, weather_data, sample_points,
                      elevation_track=None, **kwargs):
            saved['call'] = (route_id, forecast_date, weather_data, sample_points)
            saved['elevation_track'] = elevation_track

        with patch('models.get_upcoming_weather_targets', return_value=targets), \
             patch('services.rwgps.fetch_route', return_value={'track_points': _TRACK}), \
             patch('services.weather.fetch_route_weather', return_value=_forecasts(3)) as mock_fetch, \
             patch('models.save_route_weather_cache', side_effect=fake_save):
            resp = client.post('/api/cron/fetch-route-weather', headers=_auth())

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['succeeded'] == 1
        assert body['failed'] == 0
        # Stored the right route + date + payload.
        assert saved['call'][0] == 555
        assert saved['call'][1] == ten_days
        assert len(saved['call'][2]) == 3          # weather_data
        assert len(saved['call'][3]) >= 2          # sample_points
        # Requested enough forecast days to span a ride ~10 days out (not the 7-day default).
        _, kwargs = mock_fetch.call_args
        assert kwargs.get('forecast_days') is not None
        assert kwargs['forecast_days'] >= (ten_days - date.today()).days

    def test_skips_beyond_horizon_ride(self, client, app):
        _cfg(app)
        twenty_days = date.today() + timedelta(days=20)
        targets = [{'ride_id': 2, 'forecast_date': twenty_days, 'name': 'Far 400',
                    'rwgps_url': 'https://ridewithgps.com/routes/777', 'plan_id': 3}]

        with patch('models.get_upcoming_weather_targets', return_value=targets), \
             patch('services.rwgps.fetch_route', return_value={'track_points': _TRACK}), \
             patch('services.weather.fetch_route_weather') as mock_fetch, \
             patch('models.save_route_weather_cache') as mock_save:
            resp = client.post('/api/cron/fetch-route-weather', headers=_auth())

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['skipped_beyond_horizon'] == 1
        assert body['succeeded'] == 0
        mock_fetch.assert_not_called()       # never touched Open-Meteo for a 20-day ride
        mock_save.assert_not_called()

    def test_retains_last_good_on_failure(self, client, app):
        """A fetch error keeps the last-good row (upsert skipped), logs, returns 200."""
        _cfg(app)
        ten_days = date.today() + timedelta(days=10)
        targets = [{'ride_id': 3, 'forecast_date': ten_days, 'name': 'SFR 300',
                    'rwgps_url': 'https://ridewithgps.com/routes/888', 'plan_id': 1}]

        with patch('models.get_upcoming_weather_targets', return_value=targets), \
             patch('services.rwgps.fetch_route', return_value={'track_points': _TRACK}), \
             patch('services.weather.fetch_route_weather',
                   side_effect=Exception('TLS handshake timeout')), \
             patch('models.save_route_weather_cache') as mock_save:
            resp = client.post('/api/cron/fetch-route-weather', headers=_auth())

        assert resp.status_code == 200          # never raises
        body = resp.get_json()
        assert body['failed'] == 1
        assert body['succeeded'] == 0
        mock_save.assert_not_called()           # last-good row left untouched

    def test_limit_truncates_and_reports(self, client, app):
        _cfg(app)
        d = date.today() + timedelta(days=5)
        targets = [
            {'ride_id': 4, 'forecast_date': d, 'name': 'A',
             'rwgps_url': 'https://ridewithgps.com/routes/111', 'plan_id': 1},
            {'ride_id': 5, 'forecast_date': d, 'name': 'B',
             'rwgps_url': 'https://ridewithgps.com/routes/222', 'plan_id': 2},
        ]
        with patch('models.get_upcoming_weather_targets', return_value=targets), \
             patch('services.rwgps.fetch_route', return_value={'track_points': _TRACK}), \
             patch('services.weather.fetch_route_weather', return_value=_forecasts(3)), \
             patch('models.save_route_weather_cache'):
            resp = client.post('/api/cron/fetch-route-weather?limit=1', headers=_auth())

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['truncated'] is True
        assert body['processed'] == 1

    def test_no_route_url_is_reported_not_fetched(self, client, app):
        _cfg(app)
        d = date.today() + timedelta(days=5)
        targets = [{'ride_id': 6, 'forecast_date': d, 'name': 'No route',
                    'rwgps_url': None, 'plan_id': None}]
        with patch('models.get_upcoming_weather_targets', return_value=targets), \
             patch('services.weather.fetch_route_weather') as mock_fetch, \
             patch('models.save_route_weather_cache') as mock_save:
            resp = client.post('/api/cron/fetch-route-weather', headers=_auth())
        assert resp.status_code == 200
        assert resp.get_json()['skipped_no_route'] == 1
        mock_fetch.assert_not_called()
        mock_save.assert_not_called()


# ── Read seam: weather page serves stored, never fetches live ───────

class TestWeatherPageReadsStored:
    def test_weather_map_reads_stored_without_live_fetch(self, client):
        route = {'name': 'SFR 200', 'distance': 100000, 'elevation_gain': 2000,
                 'track_points': _TRACK, 'course_points': []}
        tomorrow = (date.today() + timedelta(days=1)).strftime('%Y-%m-%dT07:00')

        # If any code path tried a LIVE Open-Meteo fetch, this raises and the request 500s.
        with patch('routes.weather.fetch_route', return_value=route), \
             patch('routes.weather.load_stored_route_weather',
                   return_value=(_forecasts(3), _samples(3))), \
             patch('services.weather.fetch_route_weather',
                   side_effect=AssertionError('LIVE Open-Meteo fetch on request path!')):
            resp = client.post('/api/weather-map',
                               json={'rwgps_url': 'https://ridewithgps.com/routes/555',
                                     'start_datetime': tomorrow},
                               content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['route_name'] == 'SFR 200'
        assert len(data['map_segments']) >= 1

    def test_weather_map_miss_returns_available_false(self, client):
        """No stored forecast -> graceful 'not available yet' (HTTP 200), never a hang."""
        route = {'name': 'SFR 200', 'distance': 100000, 'elevation_gain': 2000,
                 'track_points': _TRACK, 'course_points': []}
        tomorrow = (date.today() + timedelta(days=1)).strftime('%Y-%m-%dT07:00')
        with patch('routes.weather.fetch_route', return_value=route), \
             patch('routes.weather.load_stored_route_weather', return_value=(None, None)):
            resp = client.post('/api/weather-map',
                               json={'rwgps_url': 'https://ridewithgps.com/routes/555',
                                     'start_datetime': tomorrow},
                               content_type='application/json')
        assert resp.status_code == 200
        assert resp.get_json()['available'] is False
