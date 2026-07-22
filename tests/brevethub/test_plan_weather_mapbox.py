"""The full Mapbox weather tab on the guest /plan page (Phase 3).

Proves the load-bearing invariant and the graceful degradations:

  * with a Mapbox token AND a warm cache, the Weather tab embeds the interactive
    Mapbox container + map-init JS + the cached-data endpoint URL,
  * GET /plan/<id>/weather-data serves the pre-formatted forecast/map/table/chart
    payload built from a mocked rp_brevet_route_weather row via the shared pure
    formatters — asserting specific values,
  * cache miss → 200 {available:false}; unknown event / no plan → 404,
  * NO live Open-Meteo/RWGPS fetcher runs on the guest plan page OR the weather-data
    endpoint (the #1 guest-safety invariant),
  * a missing token, or a cache miss with a token set, degrades to the lean per-stop
    list / empty state — no broken map, no empty token leaked, no 500,
  * migration 049 is an additive, rp_*-only ADD COLUMN.

Follows the BrevetHub test pattern: monkeypatch `brevethub.models.*`, use the
`client` fixture, never touch a real DB, network, or Mapbox.
"""
import os
from unittest.mock import patch

import pytest


_EVENT = {
    'id': 11, 'rusa_route_id': '1234', 'name': 'Cascade Lakes 200',
    'date': '2026-08-15', 'distance_km': 200, 'region': 'OR: Bend',
    'ride_type': 'ACP brevet', 'elevation_ft': 3280, 'rwgps_url': None,
    'start_location': None, 'start_time': '06:00', 'time_limit_hours': 13.5,
}

_PLAN = {
    'id': 5, 'event_id': 11, 'variant': 'conservative', 'name': 'Cascade Lakes 200',
    'slug': 'cascade-lakes-200', 'total_distance_miles': 124.3, 'total_elevation_ft': 3280,
    'rwgps_url': 'https://ridewithgps.com/routes/1', 'rwgps_route_id': '1',
    'distance_km': 200, 'cutoff_hours': 13.5, 'start_time': '06:00',
    'avg_moving_speed': 12.0, 'avg_elapsed_speed': 11.5,
    'total_moving_time_min': 534, 'total_elapsed_time_min': 564,
    'total_break_time_min': 30, 'overall_ft_per_mile': 26,
}
_STOPS = [
    {'stop_order': 1, 'location': 'Downtown Start', 'stop_type': 'start',
     'distance_miles': 0.0, 'seg_dist': 0.0, 'elevation_gain': 0, 'ft_per_mi': None,
     'avg_speed': None, 'segment_time_min': 0, 'cum_time_min': 0, 'time_bank_min': None,
     'difficulty_score': 0.0, 'notes': None},
    {'stop_order': 2, 'location': 'Midway Control', 'stop_type': 'control',
     'distance_miles': 62.1, 'seg_dist': 62.1, 'elevation_gain': 1600, 'ft_per_mi': 26,
     'avg_speed': 12.0, 'segment_time_min': 266, 'cum_time_min': 266, 'time_bank_min': 120,
     'difficulty_score': 2.6, 'notes': None},
    {'stop_order': 4, 'location': 'Downtown Finish', 'stop_type': 'finish',
     'distance_miles': 124.3, 'seg_dist': 62.2, 'elevation_gain': 1680, 'ft_per_mi': 27,
     'avg_speed': 11.9, 'segment_time_min': 268, 'cum_time_min': 564, 'time_bank_min': 150,
     'difficulty_score': 2.7, 'notes': None},
]
_BUNDLE = {'plan': _PLAN, 'stops': _STOPS}
_ROSTER = [{'name': 'alice', 'status': 'going'}]

# The decimated route line the cron caches (matches migration 049's polyline column).
_POLYLINE = [[44.0, -121.0], [44.1, -121.2], [44.2, -121.4]]


def _sample(ws, wd, tc):
    """A constant hourly forecast for one route sample so per-hour index selection is
    value-neutral — the assertions stay deterministic regardless of arrival hour."""
    times = [f"2026-08-15T{h:02d}:00" for h in range(24)]
    return {'hourly': {
        'time': times,
        'temperature_2m': [tc] * 24,
        'apparent_temperature': [tc] * 24,
        'wind_speed_10m': [ws] * 24,
        'wind_gusts_10m': [ws + 5] * 24,
        'wind_direction_10m': [wd] * 24,
        'precipitation_probability': [10] * 24,
        'precipitation': [0.0] * 24,
        'cloud_cover': [40] * 24,
        'relative_humidity_2m': [55] * 24,
        'weather_code': [1] * 24,
    }}


def _cached_weather(polyline=_POLYLINE):
    """A warm rp_brevet_route_weather row: per-sample Open-Meteo forecast, the aligned
    sample points, and the decimated polyline (nullable — pass None for an old row)."""
    row = {
        'event_id': 11, 'forecast_date': '2026-08-15',
        'weather_data': [_sample(20, 270, 28), _sample(25, 90, 30), _sample(10, 180, 24)],
        'sample_points': [{'lat': 44.0, 'lng': -121.0, 'distance_m': 0},
                          {'lat': 44.1, 'lng': -121.2, 'distance_m': 100000},
                          {'lat': 44.2, 'lng': -121.4, 'distance_m': 200000}],
        'polyline': polyline,
    }
    return row


def _patch_models(weather=None, event=_EVENT, bundle=_BUNDLE):
    return patch.multiple(
        'brevethub.models',
        get_brevet_event_full=lambda *a, **k: event,
        get_brevet_route_plan_with_stops=lambda *a, **k: bundle,
        get_brevet_route_weather=lambda *a, **k: weather,
        get_event_going_riders=lambda *a, **k: _ROSTER,
    )


# --------------------------------------------------------------------------- #
# Weather tab render — map when token + cache, lean list otherwise
# --------------------------------------------------------------------------- #
def test_weather_tab_renders_mapbox_when_token_and_cache(app, client):
    app.config['MAPBOX_ACCESS_TOKEN'] = 'pk.test.token'
    with _patch_models(weather=_cached_weather()):
        resp = client.get('/plan/11?tab=weather')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The Mapbox container + GL assets + map-init JS are present…
    assert 'id="wind-map"' in body
    assert 'mapbox-gl.js' in body
    assert 'mapboxgl.Map' in body
    assert 'Weather Along Route' in body          # the along-route forecast table
    # …the publishable token and the CACHED data endpoint are wired in…
    assert 'pk.test.token' in body
    assert '/plan/11/weather-data' in body
    assert "var AUTO_FETCH = '1'" in body
    # …and no live-fetch endpoint leaks in.
    assert '/api/weather-map' not in body


def test_weather_tab_degrades_to_lean_list_without_token(app, client):
    app.config['MAPBOX_ACCESS_TOKEN'] = None
    with _patch_models(weather=_cached_weather()):
        resp = client.get('/plan/11?tab=weather')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Lean per-stop list, no Mapbox, no empty token leaked into JS.
    assert 'Midway Control' in body
    assert '°F' in body
    assert 'mapbox' not in body.lower()
    assert 'wind-map' not in body


def test_weather_tab_cache_miss_with_token_degrades_gracefully(app, client):
    app.config['MAPBOX_ACCESS_TOKEN'] = 'pk.test.token'
    with _patch_models(weather=None):
        resp = client.get('/plan/11?tab=weather')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # No auto-init map, a graceful "check back" message, no 500.
    assert 'No forecast cached' in body
    assert 'wind-map' not in body
    assert 'mapbox' not in body.lower()


# --------------------------------------------------------------------------- #
# Cached-data endpoint — pre-formatted payload from the cache, no live call
# --------------------------------------------------------------------------- #
def test_weather_data_endpoint_returns_cached_payload(app, client):
    with _patch_models(weather=_cached_weather()):
        resp = client.get('/plan/11/weather-data')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['available'] is True
    assert data['route_name'] == 'Cascade Lakes 200'
    assert data['total_distance_mi'] == 124.3
    assert data['total_elevation_ft'] == 3280
    # Polyline served straight from the cached decimated column.
    assert data['polyline'] == _POLYLINE
    assert data['attribution'] == '*Weather data: Open-Meteo*'
    assert data['ride_summary'] == ''            # no OpenAI on the guest path
    # One map segment per cached sample, imperial units from the shared formatter.
    segs = data['map_segments']
    assert len(segs) == 3
    assert segs[0]['temperature_f'] == 82.4      # 28 °C
    assert segs[0]['wind_speed_mph'] == 12.4     # 20 km/h
    assert segs[1]['distance_mi'] == 62.1        # 100 km
    assert segs[2]['distance_mi'] == 124.3       # 200 km
    # Chart + table payloads present and aligned.
    assert data['chart_data']['labels'] == [0.0, 62.1, 124.3]
    assert [s['distance_mi'] for s in data['table_segments']] == [0.0, 124.3]
    assert data['temp_range']['min_f'] <= data['temp_range']['max_f']


def test_weather_data_polyline_falls_back_to_sample_points(app, client):
    # An old cache row (warmed before the polyline column) has polyline=None.
    with _patch_models(weather=_cached_weather(polyline=None)):
        resp = client.get('/plan/11/weather-data')
    data = resp.get_json()
    assert data['available'] is True
    assert data['polyline'] == [[44.0, -121.0], [44.1, -121.2], [44.2, -121.4]]


def test_weather_data_cache_miss_is_graceful_200(app, client):
    with _patch_models(weather=None):
        resp = client.get('/plan/11/weather-data')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['available'] is False
    assert data['reason'] == 'not_cached'
    assert 'closer to the ride' in data['message']


def test_weather_data_honors_selected_variant(app, client):
    # /plan/<id>?variant=aggressive&tab=weather must time its payload off the aggressive
    # plan, not the conservative one — the endpoint loads the requested variant.
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops',
               return_value=_BUNDLE) as mbundle, \
         patch('brevethub.models.get_brevet_route_weather', return_value=_cached_weather()):
        resp = client.get('/plan/11/weather-data?variant=aggressive')
    assert resp.status_code == 200
    mbundle.assert_called_once_with(11, 'aggressive')


def test_weather_data_bad_variant_coerced_to_conservative(app, client):
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops',
               return_value=_BUNDLE) as mbundle, \
         patch('brevethub.models.get_brevet_route_weather', return_value=_cached_weather()):
        resp = client.get('/plan/11/weather-data?variant=bogus')
    assert resp.status_code == 200
    mbundle.assert_called_once_with(11, 'conservative')


def test_weather_tab_autofetch_url_carries_variant(app, client):
    app.config['MAPBOX_ACCESS_TOKEN'] = 'pk.test.token'
    with _patch_models(weather=_cached_weather()):
        resp = client.get('/plan/11?variant=aggressive&tab=weather')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # auto-fetch pulls the aggressive-timed payload, matching the embedded plan.
    assert '/plan/11/weather-data?variant=aggressive' in body


def test_weather_data_unknown_event_404(app, client):
    with _patch_models(weather=_cached_weather(), event=None):
        resp = client.get('/plan/999/weather-data')
    assert resp.status_code == 404


def test_weather_data_no_plan_404(app, client):
    with _patch_models(weather=_cached_weather(), bundle=None):
        resp = client.get('/plan/11/weather-data')
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Guest-safety invariant — no live Open-Meteo/RWGPS call on any guest path
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('url', ['/plan/11?tab=weather', '/plan/11/weather-data'])
def test_no_live_fetcher_on_guest_paths(app, client, url):
    app.config['MAPBOX_ACCESS_TOKEN'] = 'pk.test.token'
    # Patch the live fetchers at the module the guest path resolves — a stray import
    # into plan.py would route through here. They must never fire on a guest read.
    with _patch_models(weather=_cached_weather()), \
         patch('shared.weather.fetch_route_weather') as mfw, \
         patch('shared.rwgps.fetch_route') as mfr:
        resp = client.get(url)
    assert resp.status_code == 200
    mfw.assert_not_called()
    mfr.assert_not_called()


# --------------------------------------------------------------------------- #
# Migration 049 — additive, rp_*-only ADD COLUMN
# --------------------------------------------------------------------------- #
def test_migration_049_is_additive_polyline_column():
    import re
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, 'migrations', '049_brevethub_route_weather_polyline.sql')
    with open(path, 'r', encoding='utf-8') as fh:
        raw = fh.read()
    # Strip `-- ...` comment prose so the property scanners match executable SQL, not
    # doc lines (same convention as the other migration tests).
    code = '\n'.join(re.sub(r'--.*$', '', line) for line in raw.splitlines())
    assert re.search(r'ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+polyline\s+JSONB',
                     code, re.IGNORECASE)
    assert re.search(r'ALTER\s+TABLE\s+rp_brevet_route_weather', code, re.IGNORECASE)
    # Additive only — no destructive statements.
    assert not re.search(r'\bDROP\b', code, re.IGNORECASE)
    # rp_*-only: every table the executable SQL names is rp_-prefixed.
    for ref in re.findall(r'\b(?:TABLE|INTO|UPDATE|FROM|JOIN)\s+("?\w+"?)',
                          code, re.IGNORECASE):
        assert ref.strip('"').lower().startswith('rp_'), \
            f"non-rp table {ref!r} in migration 049"
