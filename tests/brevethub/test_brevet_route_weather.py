"""BrevetHub per-stop wind — shared compute, warm cron, plan/analysis injection,
migration contract.

Covers the mission's along-route wind feature, all HTTP (Open-Meteo + RWGPS) mocked
and no real DB (the conftest monkeypatch-models pattern):
  (a) the extracted pure ``shared.weather.compute_stop_winds`` — headwind / tailwind /
      crosswind / calm / unresolvable,
  (b) the /cron/warm-brevet-route-weather warmer — auth ladder, idempotent fresh-skip,
      fail-soft, counts, GET, pinned single-prefix route,
  (c) the /plan real-plan route — Wind column present when a route-weather row exists,
      absent when not,
  (d) the /analysis detail route + the historical assembler — wind arrows injected, and
      the redteam-flagged missing-coordinate index regression (a middle coordinate-less
      stop must not shift forecasts onto the wrong displayed stop),
  (e) the migration 043 static SQL contract.
"""
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def _const_forecast(day, wind_speed=20.0, wind_dir=270, temp=15.0):
    """A minimal Open-Meteo forecast with constant hourly arrays keyed to ``day``."""
    times = [f"{day:%Y-%m-%d}T{h:02d}:00" for h in range(24)]
    return {'hourly': {
        'time': times,
        'wind_speed_10m': [wind_speed] * 24,
        'wind_direction_10m': [wind_dir] * 24,
        'wind_gusts_10m': [wind_speed + 5] * 24,
        'temperature_2m': [temp] * 24,
    }}


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


_RIDER = {'id': 7, 'email': 'rider@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}


# =========================================================================== #
# (a) Shared pure compute — compute_stop_winds
# =========================================================================== #
class TestComputeStopWinds:
    """A due-north route (increasing lat, constant lng → bearing 0). The wind's FROM
    direction relative to that heading decides head/tail/crosswind."""

    _SAMPLES = [
        {'lat': 37.00, 'lng': -122.00, 'distance_m': 0},
        {'lat': 37.10, 'lng': -122.00, 'distance_m': 16000},
    ]
    _STOPS = [{'distance_miles': 0.0, 'arrival_time_min': 0}]

    def _compute(self, wind_speed, wind_dir):
        from shared.weather import compute_stop_winds
        today = date.today()
        weather = [_const_forecast(today, wind_speed, wind_dir),
                   _const_forecast(today, wind_speed, wind_dir)]
        return compute_stop_winds(self._STOPS, weather, self._SAMPLES, today, '07:00')

    def test_headwind(self):
        # Wind FROM the north (0°) while heading north → straight headwind.
        r = self._compute(20.0, 0)[0]
        assert r['wind_type'] == 'headwind'
        assert r['headwind_kmh'] > 0
        assert r['arrow_rotation'] == 180        # arrow points down (into the face)
        assert r['wind_speed_kmh'] == 20.0

    def test_tailwind(self):
        # Wind FROM the south (180°) while heading north → tailwind.
        r = self._compute(20.0, 180)[0]
        assert r['wind_type'] == 'tailwind'
        assert r['headwind_kmh'] < 0
        assert r['arrow_rotation'] == 0          # arrow points up (from behind)

    def test_crosswind(self):
        # Wind FROM the east (90°) while heading north → crosswind.
        r = self._compute(20.0, 90)[0]
        assert r['wind_type'] == 'crosswind'
        assert abs(r['crosswind_kmh']) > abs(r['headwind_kmh'])
        assert r['arrow_rotation'] in (90, 270)

    def test_calm(self):
        r = self._compute(0.0, 0)[0]
        assert r['wind_speed_kmh'] == 0.0
        assert r['headwind_kmh'] == 0
        assert r['crosswind_kmh'] == 0

    def test_required_keys_present(self):
        r = self._compute(20.0, 0)[0]
        for key in ('headwind_kmh', 'crosswind_kmh', 'wind_speed_kmh', 'wind_type',
                    'arrow_rotation', 'arrow_glyph', 'compass'):
            assert key in r

    def test_unresolvable_stop_is_none(self):
        """No forecast entry for the mapped sample → a None slot (same length)."""
        from shared.weather import compute_stop_winds
        today = date.today()
        result = compute_stop_winds(self._STOPS, [], self._SAMPLES, today, '07:00')
        assert result == [None]

    def test_no_db_or_http(self):
        """Pure: it must not import requests-driven fetch — a plain call with data
        returns without touching the network (proven by no patch being needed)."""
        assert self._compute(15.0, 45)[0] is not None


# =========================================================================== #
# (b) Warm cron — /cron/warm-brevet-route-weather
# =========================================================================== #
_SECRET = 'test-cron-secret-value'
_PATH = '/cron/warm-brevet-route-weather'

_D1 = date.today() + timedelta(days=3)
_D2 = date.today() + timedelta(days=6)
_TARGETS = [
    {'id': 11, 'date': _D1, 'rwgps_url': 'https://ridewithgps.com/routes/123'},
    {'id': 12, 'date': _D2, 'rwgps_url': 'https://example.com/not-a-route'},
]
_ROUTE = {'name': 'R', 'track_points': [{'y': 37.0, 'x': -122.0, 'd': 0}]}
_SAMPLES = [{'lat': 37.0, 'lng': -122.0, 'distance_m': 0}]
_WEATHER = [_const_forecast(_D1)]


def _auth(secret=_SECRET):
    return {'Authorization': f'Bearer {secret}'}


def _with_secret(app):
    app.config['CRON_SECRET'] = _SECRET


def test_route_weather_requires_auth(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_route_weather_warm_targets') as mt:
        resp = client.post(_PATH)
    assert resp.status_code == 401
    mt.assert_not_called()


def test_route_weather_rejects_wrong_secret(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_route_weather_warm_targets') as mt:
        resp = client.post(_PATH, headers=_auth('nope'))
    assert resp.status_code == 401
    mt.assert_not_called()


def test_route_weather_secret_unset_is_500(app, client):
    app.config['CRON_SECRET'] = None
    resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 500


def test_route_weather_warms_with_url_skips_without(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_route_weather_warm_targets', return_value=_TARGETS), \
         patch('brevethub.models.get_brevet_route_weather', return_value=None), \
         patch('brevethub.routes.cron.fetch_route', return_value=_ROUTE), \
         patch('brevethub.routes.cron.sample_track_points', return_value=_SAMPLES) as msamp, \
         patch('brevethub.routes.cron.fetch_route_weather', return_value=_WEATHER), \
         patch('brevethub.models.upsert_brevet_route_weather') as mup:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['warmed'] == 1       # event 11 (valid RWGPS route)
    assert data['skipped'] == 1      # event 12 (unparseable url)
    assert data['failed'] == 0
    assert data['considered'] == 2
    mup.assert_called_once()
    # Dense 15 km sampling — the pinned interval.
    _args, kwargs = msamp.call_args
    assert kwargs.get('interval_m') == 15000


def test_route_weather_uses_plan_route_not_event_url(app, client):
    """Council regression: warm the cache from the PERSISTED PLAN's route, not the
    event's rwgps_url. An admin can generate the plan from a different RWGPS URL than
    the event's; the /plan page renders THAT route, so the sampled weather must match
    it. The target carries the plan's rwgps_route_id (999) while its rwgps_url points at
    the event's other route (111) — the cron must fetch 999."""
    _with_secret(app)
    target = [{'id': 11, 'date': _D1,
               'rwgps_url': 'https://ridewithgps.com/routes/111',   # event's route
               'rwgps_route_id': '999'}]                             # plan's route (wins)
    with patch('brevethub.models.get_route_weather_warm_targets', return_value=target), \
         patch('brevethub.models.get_brevet_route_weather', return_value=None), \
         patch('brevethub.routes.cron.fetch_route', return_value=_ROUTE) as mfetch, \
         patch('brevethub.routes.cron.sample_track_points', return_value=_SAMPLES), \
         patch('brevethub.routes.cron.fetch_route_weather', return_value=_WEATHER), \
         patch('brevethub.models.upsert_brevet_route_weather'):
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()['warmed'] == 1
    (called_route_id, *_rest), _kw = mfetch.call_args
    assert called_route_id == '999', 'cron must sample the plan route, not the event URL'


def test_route_weather_get_verb_works(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_route_weather_warm_targets', return_value=[]):
        resp = client.get(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()['warmed'] == 0


def test_route_weather_idempotent_skips_fresh_rows(app, client):
    """A row already fetched recently is SKIPPED on a re-run — no redundant fetch."""
    _with_secret(app)
    target = [{'id': 11, 'date': _D1, 'rwgps_url': 'https://ridewithgps.com/routes/123'}]
    fresh_row = {'fetched_at': datetime.now(), 'weather_data': _WEATHER,
                 'sample_points': _SAMPLES}
    with patch('brevethub.models.get_route_weather_warm_targets', return_value=target), \
         patch('brevethub.models.get_brevet_route_weather', return_value=fresh_row), \
         patch('brevethub.routes.cron.fetch_route') as mfetch, \
         patch('brevethub.models.upsert_brevet_route_weather') as mup:
        resp = client.post(_PATH, headers=_auth())
    data = resp.get_json()
    assert data['warmed'] == 0 and data['skipped'] == 1
    mfetch.assert_not_called()       # fresh → no route fetch
    mup.assert_not_called()


def test_route_weather_fail_soft_per_event(app, client):
    _with_secret(app)
    targets = [
        {'id': 11, 'date': _D1, 'rwgps_url': 'https://ridewithgps.com/routes/111'},
        {'id': 22, 'date': _D2, 'rwgps_url': 'https://ridewithgps.com/routes/222'},
    ]

    def _fetch(route_id, api_key, auth_token):
        if route_id == '111':
            raise Exception('RWGPS 500')
        return _ROUTE

    with patch('brevethub.models.get_route_weather_warm_targets', return_value=targets), \
         patch('brevethub.models.get_brevet_route_weather', return_value=None), \
         patch('brevethub.routes.cron.fetch_route', side_effect=_fetch), \
         patch('brevethub.routes.cron.sample_track_points', return_value=_SAMPLES), \
         patch('brevethub.routes.cron.fetch_route_weather', return_value=_WEATHER), \
         patch('brevethub.models.upsert_brevet_route_weather'):
        resp = client.post(_PATH, headers=_auth())
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['warmed'] == 1 and data['failed'] == 1   # one boom, one warmed


def test_route_weather_skips_empty_samples(app, client):
    """A route with no usable geometry (no samples) is skipped, not upserted."""
    _with_secret(app)
    target = [{'id': 11, 'date': _D1, 'rwgps_url': 'https://ridewithgps.com/routes/123'}]
    with patch('brevethub.models.get_route_weather_warm_targets', return_value=target), \
         patch('brevethub.models.get_brevet_route_weather', return_value=None), \
         patch('brevethub.routes.cron.fetch_route', return_value={'track_points': []}), \
         patch('brevethub.routes.cron.sample_track_points', return_value=[]), \
         patch('brevethub.routes.cron.fetch_route_weather') as mweather, \
         patch('brevethub.models.upsert_brevet_route_weather') as mup:
        resp = client.post(_PATH, headers=_auth())
    data = resp.get_json()
    assert data['skipped'] == 1 and data['warmed'] == 0
    mweather.assert_not_called()
    mup.assert_not_called()


def test_route_weather_target_load_failure_no_500(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_route_weather_warm_targets',
               side_effect=OSError('db down')), \
         patch('brevethub.routes.cron.fetch_route') as mfetch:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is False
    mfetch.assert_not_called()


def test_route_weather_path_is_single_prefixed(app):
    rules = [r.rule for r in app.url_map.iter_rules()
             if 'warm-brevet-route-weather' in r.rule]
    assert rules == ['/cron/warm-brevet-route-weather'], \
        f"route-weather cron must be exactly /cron/warm-brevet-route-weather, got {rules}"


def test_route_weather_missing_and_double_prefix_404(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_route_weather_warm_targets', return_value=[]):
        assert client.post('/warm-brevet-route-weather', headers=_auth()).status_code == 404
        assert client.post('/cron/cron/warm-brevet-route-weather',
                           headers=_auth()).status_code == 404
        assert client.post(_PATH, headers=_auth()).status_code == 200


# =========================================================================== #
# (c) Plan route — Wind column present when cached weather exists, absent when not
# =========================================================================== #
_PLAN_EVENT = {
    'id': 11, 'rusa_route_id': '1234', 'name': 'Point Reyes 200',
    'date': _D1, 'distance_km': 200, 'region': 'CA: San Francisco',
    'ride_type': 'ACP brevet', 'elevation_ft': 4200,
    'rwgps_url': 'https://ridewithgps.com/routes/1',
    'start_location': None, 'start_time': '07:00', 'time_limit_hours': 13.5,
    'club_id': None,
}
_PLAN = {'name': 'Point Reyes 200', 'rwgps_url': 'https://ridewithgps.com/routes/1',
         'total_distance_miles': 124.3, 'total_elevation_ft': 4000,
         'overall_ft_per_mile': 32.0, 'avg_moving_speed': 13.0, 'start_time': '07:00'}
_PLAN_STOPS = [
    {'stop_order': 1, 'location': 'Start', 'stop_type': 'start', 'distance_miles': 0.0,
     'seg_dist': 0.0, 'elevation_gain': 0, 'ft_per_mi': None, 'avg_speed': None,
     'difficulty_score': 0.0, 'cum_time_min': 0, 'time_bank_min': 0},
    {'stop_order': 2, 'location': 'Control 1', 'stop_type': 'control',
     'distance_miles': 62.0, 'seg_dist': 62.0, 'elevation_gain': 2000, 'ft_per_mi': 32,
     'avg_speed': 13.0, 'difficulty_score': 3.0, 'cum_time_min': 300,
     'time_bank_min': 60},
    {'stop_order': 3, 'location': 'Finish', 'stop_type': 'finish',
     'distance_miles': 124.3, 'seg_dist': 62.3, 'elevation_gain': 2000, 'ft_per_mi': 32,
     'avg_speed': 13.0, 'difficulty_score': 3.0, 'cum_time_min': 600,
     'time_bank_min': 30},
]
_PLAN_SAMPLES = [
    {'lat': 37.00, 'lng': -122.00, 'distance_m': 0},
    {'lat': 37.10, 'lng': -122.00, 'distance_m': 100000},
    {'lat': 37.20, 'lng': -122.00, 'distance_m': 200000},
]


def test_plan_wind_column_present_when_cached(client):
    weather_row = {'weather_data': [_const_forecast(_D1) for _ in range(3)],
                   'sample_points': _PLAN_SAMPLES, 'forecast_date': _D1,
                   'fetched_at': datetime.now()}
    with patch('brevethub.models.get_brevet_event_full', return_value=_PLAN_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops',
               return_value={'plan': _PLAN, 'stops': _PLAN_STOPS}), \
         patch('brevethub.models.get_brevet_route_weather', return_value=weather_row):
        resp = client.get('/plan/11')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'plan-wind-head' in body       # the Wind column header rendered
    assert 'wind-arrow' in body           # at least one wind-arrow SVG


def test_plan_wind_column_absent_without_cached(client):
    with patch('brevethub.models.get_brevet_event_full', return_value=_PLAN_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops',
               return_value={'plan': _PLAN, 'stops': _PLAN_STOPS}), \
         patch('brevethub.models.get_brevet_route_weather', return_value=None):
        resp = client.get('/plan/11')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'plan-wind-head' not in body   # graceful fallback: no Wind column
    assert 'Point Reyes 200' in body      # page still renders the real plan


# =========================================================================== #
# (d) Analysis — historical assembler + route injection
# =========================================================================== #
def _analysis(stops, ride_date='2026-07-01'):
    return {
        'activity': {'name': 'Coastal 200', 'date': ride_date, 'distance_km': 200.0},
        'stops': stops,
    }


class TestHistoricalStopWinds:
    def test_empty_stops_returns_empty(self):
        from brevethub.routes.analysis import _historical_stop_winds
        assert _historical_stop_winds(_analysis([])) == []

    def test_all_coordinate_less_returns_all_none_no_fetch(self):
        from brevethub.routes.analysis import _historical_stop_winds
        stops = [{'distance_km': 50.0, 'lat': None, 'lng': None},
                 {'distance_km': 100.0, 'lat': None, 'lng': None}]
        with patch('brevethub.routes.analysis.fetch_historical_wind') as mf:
            result = _historical_stop_winds(_analysis(stops))
        assert result == [None, None]
        mf.assert_not_called()

    def test_fetch_error_fails_soft(self, app):
        from brevethub.routes.analysis import _historical_stop_winds
        stops = [{'distance_km': 50.0, 'lat': 37.0, 'lng': -122.0}]
        # The fail-soft path logs via current_app.logger, which needs an app context
        # (always present on the real request path); provide one here.
        with app.app_context(), \
             patch('brevethub.routes.analysis.fetch_historical_wind',
                   side_effect=Exception('open-meteo down')):
            assert _historical_stop_winds(_analysis(stops)) is None

    def test_middle_coordinate_less_stop_does_not_shift_forecasts(self):
        """The redteam-flagged trap: a middle coordinate-less stop must yield a None
        placeholder AND must NOT shift later forecasts onto the wrong displayed stop.

        5 stops, index 2 coordinate-less → 4 valid coords. The mock returns
        ascending-speed forecasts [10, 20, 30, 40] for those 4 coords. The result must
        map speeds to original stops 0,1,(None),3,4 — NOT slide 30/40 up onto 2/3.
        """
        from brevethub.routes.analysis import _historical_stop_winds
        ride_date = date.today() - timedelta(days=10)
        stops = [
            {'distance_km': 0.0, 'lat': 37.0, 'lng': -122.0},
            {'distance_km': 50.0, 'lat': 37.1, 'lng': -122.0},
            {'distance_km': 100.0, 'lat': None, 'lng': None},   # coordinate-less
            {'distance_km': 150.0, 'lat': 37.3, 'lng': -122.0},
            {'distance_km': 200.0, 'lat': 37.4, 'lng': -122.0},
        ]
        speeds = [10.0, 20.0, 30.0, 40.0]
        weather = [_const_forecast(ride_date, wind_speed=s) for s in speeds]

        captured = {}

        def _fetch(coords, when):
            captured['n'] = len(coords)
            return weather, 'archive'

        with patch('brevethub.routes.analysis.fetch_historical_wind',
                   side_effect=_fetch):
            result = _historical_stop_winds(
                _analysis(stops, ride_date=ride_date.strftime('%Y-%m-%d')))

        assert captured['n'] == 4                       # only coordinate-bearing stops
        assert len(result) == len(stops)                # 1:1 with original stops
        assert result[2] is None                        # the coordinate-less stop
        assert result[0]['wind_speed_kmh'] == 10.0
        assert result[1]['wind_speed_kmh'] == 20.0
        assert result[3]['wind_speed_kmh'] == 30.0      # NOT shifted up to 20/30
        assert result[4]['wind_speed_kmh'] == 40.0


def test_analysis_detail_injects_wind_arrows(client):
    """The completed-ride detail view renders per-stop wind arrows in the Stops table."""
    _login(client)
    stops = [{'distance_km': 100.0, 'duration_min': 18.0, 'lat': 37.5, 'lng': -122.3},
             {'distance_km': 150.0, 'duration_min': 7.5, 'lat': 37.7, 'lng': -122.1}]
    sample = {
        'activity': {'name': 'Coastal 200', 'date': '2026-07-01', 'distance_km': 203.4,
                     'elevation_ft': 6800, 'moving_time': '9h 12m',
                     'elapsed_time': '11h 40m', 'avg_speed_kmh': 22.1},
        'summary': {'moving_speed_kmh': 23.4, 'avg_hr': 138, 'max_hr': 171,
                    'avg_watts': 165, 'max_watts': 520},
        'stop_count': 2, 'stops': stops, 'legs': [], 'map': None,
    }
    ride_date = date(2026, 7, 1)
    weather = [_const_forecast(ride_date), _const_forecast(ride_date)]
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride_analysis',
               return_value={'analysis': sample, 'activity_streams': b'x',
                             'computed_at': None}), \
         patch('brevethub.routes.analysis.fetch_historical_wind',
               return_value=(weather, 'archive')):
        resp = client.get('/analysis/555')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'analysis-wind-head' in body   # the Wind column header rendered
    assert 'wind-arrow' in body           # per-stop arrow SVG injected


def test_analysis_detail_no_wind_still_renders(client):
    """When the historical fetch returns nothing, the page renders with no Wind column."""
    _login(client)
    stops = [{'distance_km': 100.0, 'duration_min': 18.0, 'lat': 37.5, 'lng': -122.3}]
    sample = {
        'activity': {'name': 'Coastal 200', 'date': '2026-07-01', 'distance_km': 203.4,
                     'elevation_ft': 6800, 'moving_time': '9h 12m',
                     'elapsed_time': '11h 40m', 'avg_speed_kmh': 22.1},
        'summary': {'moving_speed_kmh': None, 'avg_hr': None, 'max_hr': None,
                    'avg_watts': None, 'max_watts': None},
        'stop_count': 1, 'stops': stops, 'legs': [], 'map': None,
    }
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride_analysis',
               return_value={'analysis': sample, 'activity_streams': b'x',
                             'computed_at': None}), \
         patch('brevethub.routes.analysis.fetch_historical_wind',
               return_value=(None, None)):
        resp = client.get('/analysis/555')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'analysis-wind-head' not in body
    assert 'Coastal 200' in body


# =========================================================================== #
# (e) Migration 043 static SQL contract
# =========================================================================== #
import os  # noqa: E402
import re  # noqa: E402

_MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'migrations', '043_brevethub_brevet_route_weather.sql')


def _migration_sql():
    with open(_MIGRATION, 'r', encoding='utf-8') as fh:
        return fh.read()


def test_migration_043_exists():
    assert os.path.exists(_MIGRATION), 'migration 043 is missing'


def test_migration_043_creates_table_idempotently():
    sql = _migration_sql()
    assert re.search(
        r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+rp_brevet_route_weather\b',
        sql, re.IGNORECASE), 'must CREATE TABLE IF NOT EXISTS rp_brevet_route_weather'
    assert 'DROP' not in sql.upper(), 'migration 043 must be strictly additive'
    for m in re.finditer(r'CREATE\s+INDEX\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), 'a CREATE INDEX in migration 043 lacks IF NOT EXISTS'


def test_migration_043_has_unique_and_jsonb_columns():
    sql = _migration_sql()
    assert re.search(r'UNIQUE\s*\(\s*event_id\s*,\s*forecast_date\s*\)', sql, re.IGNORECASE), \
        'migration 043 must UNIQUE (event_id, forecast_date)'
    lower = sql.lower()
    assert 'weather_data   jsonb' in lower or re.search(r'weather_data\s+jsonb', lower), \
        'weather_data must be JSONB'
    assert re.search(r'sample_points\s+jsonb', lower), 'sample_points must be JSONB'
    assert re.search(r'forecast_date\s+date', lower), 'forecast_date must be DATE'
    assert re.search(r'fetched_at\s+timestamptz', lower), 'fetched_at must be TIMESTAMPTZ'


def test_migration_043_fk_and_rp_only():
    sql = _migration_sql()
    assert re.search(r'REFERENCES\s+rp_brevet_event\s*\(\s*id\s*\)', sql, re.IGNORECASE), \
        'event_id must FK rp_brevet_event(id)'
    patterns = [
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)',
        r'REFERENCES\s+([A-Za-z_][A-Za-z0-9_]*)',
        r'CREATE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?[A-Za-z_][A-Za-z0-9_]*\s+ON\s+([A-Za-z_][A-Za-z0-9_]*)',
    ]
    offenders = set()
    for pat in patterns:
        for name in re.findall(pat, sql, re.IGNORECASE):
            if not name.lower().startswith('rp_'):
                offenders.add(name.lower())
    assert not offenders, f'migration 043 touches non-rp_ tables: {sorted(offenders)}'
