"""Tests for live rider location tracking (PR 1 — Garmin LiveTrack).

All external HTTP is mocked; no real Garmin/RWGPS calls. Route tests mock the
models layer so they don't require a seeded database.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

import requests

from services import garmin_livetrack
import models


_FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures',
                        'garmin_livetrack_trackpoints.json')


def _load_fixture_points():
    with open(_FIXTURE) as f:
        return json.load(f)['trackPoints']


def _wrap_share_html(points):
    """Embed `points` into a fake LiveTrack share page the way Garmin's Next.js
    app server-renders them: an escaped ``"trackPoints":[ ... ]`` JSON inside a
    JS string. Mirrors the real RSC/flight payload our scraper parses out.
    """
    raw = json.dumps(points)
    esc = raw.replace('\\', '\\\\').replace('"', '\\"')   # JS-string escaping
    return (
        '<!DOCTYPE html><html><body><script>'
        'self.__next_f.push([1,"a:{\\"data\\":{\\"pages\\":[{\\"trackPoints\\":'
        + esc + '}]}}"])'
        '</script></body></html>'
    )


def _mock_response(status_code=200, text='', json_data=None, raise_json=False):
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 300
    resp.text = text
    if raise_json:
        resp.json.side_effect = ValueError('no json')
    else:
        resp.json.return_value = json_data if json_data is not None else {}
    return resp


# ── parse_session ────────────────────────────────────────────────────────

def test_parse_session_valid():
    url = 'https://livetrack.garmin.com/session/abc123/token/XYZ789'
    parsed = garmin_livetrack.parse_session(url)
    assert parsed == {'session_id': 'abc123', 'token': 'XYZ789'}


def test_parse_session_with_query_and_trailing():
    url = 'https://livetrack.garmin.com/session/s-1/token/t-2?lang=en#map'
    parsed = garmin_livetrack.parse_session(url)
    assert parsed['session_id'] == 's-1'
    assert parsed['token'] == 't-2'


@pytest.mark.parametrize('bad', ['', None, 'https://example.com/foo', 'garbage', 123])
def test_parse_session_invalid(bad):
    assert garmin_livetrack.parse_session(bad) is None


# ── fetch_positions ──────────────────────────────────────────────────────

def test_fetch_positions_success(app):
    html = _wrap_share_html(_load_fixture_points())
    with app.app_context():
        with patch.object(requests, 'get', return_value=_mock_response(200, text=html)):
            points = garmin_livetrack.fetch_positions('tok', 'sess')
    # 3 of 4 points have coordinates (the 4th is missing position).
    assert len(points) == 3
    assert points[0]['lat'] == pytest.approx(37.8044)
    assert points[0]['lng'] == pytest.approx(-122.2712)
    assert isinstance(points[0]['recorded_at'], datetime)
    assert points[0]['recorded_at'].tzinfo is not None


def test_fetch_positions_accepts_alt_keys(app):
    data = [
        {'latitude': 40.0, 'longitude': -73.0, 'timestamp': 1750684800000},
        {'lat': 41.0, 'lng': -74.0, 'dateTime': '2026-06-23T15:00:00Z'},
    ]
    html = _wrap_share_html(data)
    with app.app_context():
        with patch.object(requests, 'get', return_value=_mock_response(200, text=html)):
            points = garmin_livetrack.fetch_positions('tok', 'sess')
    assert len(points) == 2
    assert points[0]['lat'] == 40.0 and points[0]['lng'] == -73.0
    assert points[1]['lat'] == 41.0


def test_fetch_positions_parses_garmin_fitness_fields(app):
    # Real Garmin point shape: speedMetersPerSec is preferred over the legacy
    # `speed` field, and HR/power/cadence use Garmin's verbose key names.
    data = [{
        'position': {'lat': 37.5, 'lon': -121.9},
        'dateTime': '2026-06-24T18:00:00.000Z',
        'speed': 0, 'speedMetersPerSec': 5.5,
        'heartRateBeatsPerMin': 142, 'powerWatts': 210, 'cadenceCyclesPerMin': 88,
    }]
    html = _wrap_share_html(data)
    with app.app_context():
        with patch.object(requests, 'get', return_value=_mock_response(200, text=html)):
            points = garmin_livetrack.fetch_positions('tok', 'sess')
    assert len(points) == 1
    p = points[0]
    assert p['speed'] == pytest.approx(5.5)   # speedMetersPerSec wins over speed=0
    assert p['heart_rate'] == 142
    assert p['power'] == 210
    assert p['cadence'] == 88


def test_fetch_positions_uses_share_page_url(app):
    captured = {}

    def _get(url, **kwargs):
        captured['url'] = url
        captured['headers'] = kwargs.get('headers', {})
        return _mock_response(200, text=_wrap_share_html([]))

    with app.app_context():
        with patch.object(requests, 'get', side_effect=_get):
            garmin_livetrack.fetch_positions('TOK', 'SID')
    # The share page a human opens — NOT the deleted REST trackpoints endpoint.
    assert captured['url'] == 'https://livetrack.garmin.com/session/SID/token/TOK'
    assert 'trackpoints' not in captured['url']
    assert 'Mozilla' in captured['headers'].get('User-Agent', '')


def test_fetch_positions_sends_browser_headers(app):
    # The fetch should look like a real browser opening the public share page:
    # a current Chrome UA plus Accept / Accept-Language and a consistent
    # Sec-Fetch-* navigation set. Accept-Encoding is intentionally left to
    # `requests` (no manual Brotli advert that we couldn't decode).
    captured = {}

    def _get(url, **kwargs):
        captured['headers'] = kwargs.get('headers', {})
        return _mock_response(200, text=_wrap_share_html([]))

    with app.app_context():
        with patch.object(requests, 'get', side_effect=_get):
            garmin_livetrack.fetch_positions('TOK', 'SID')

    headers = captured['headers']
    assert 'Mozilla' in headers.get('User-Agent', '')
    assert 'Chrome/' in headers.get('User-Agent', '')
    assert headers.get('Accept', '').startswith('text/html')
    assert headers.get('Accept-Language', '')
    assert headers.get('Sec-Fetch-Mode') == 'navigate'
    # Must not advertise an encoding we can't guarantee decoding (Brotli).
    assert 'Accept-Encoding' not in headers


def test_fetch_positions_no_trackpoints_returns_empty(app):
    # A page that loads but embeds no trackpoints (session hasn't reported yet)
    # yields [] rather than raising.
    html = '<html><body>no live data yet</body></html>'
    with app.app_context():
        with patch.object(requests, 'get', return_value=_mock_response(200, text=html)):
            points = garmin_livetrack.fetch_positions('tok', 'sess')
    assert points == []


def test_extract_trackpoints_html_multiple_arrays():
    # Garmin's RSC stream can split trackpoints across several pushes; we must
    # collect every embedded array, not just the first.
    a = _wrap_share_html([{'position': {'lat': 1.0, 'lon': 2.0},
                           'dateTime': '2026-06-24T00:00:00Z'}])
    b = _wrap_share_html([{'position': {'lat': 3.0, 'lon': 4.0},
                           'dateTime': '2026-06-24T00:01:00Z'}])
    raw = garmin_livetrack._extract_trackpoints_html(a + b)
    assert len(raw) == 2
    assert raw[0]['position']['lat'] == 1.0
    assert raw[1]['position']['lat'] == 3.0


def test_extract_trackpoints_html_empty_or_absent():
    assert garmin_livetrack._extract_trackpoints_html('') == []
    assert garmin_livetrack._extract_trackpoints_html('<html>nope</html>') == []


@pytest.mark.parametrize('code,needle', [
    (404, 'not found'), (401, 'unauthorized'),
    (403, 'unauthorized'), (429, 'rate limited'), (500, 'error'),
])
def test_fetch_positions_http_errors(app, code, needle):
    with app.app_context():
        with patch.object(requests, 'get', return_value=_mock_response(code)):
            with pytest.raises(Exception) as exc:
                garmin_livetrack.fetch_positions('tok', 'sess')
    assert needle in str(exc.value).lower()


def test_fetch_positions_timeout(app):
    with app.app_context():
        with patch.object(requests, 'get', side_effect=requests.Timeout()):
            with pytest.raises(Exception) as exc:
                garmin_livetrack.fetch_positions('tok', 'sess')
    assert 'timed out' in str(exc.value).lower()


def test_fetch_positions_requires_token_and_session(app):
    with app.app_context():
        with pytest.raises(ValueError):
            garmin_livetrack.fetch_positions('', 'sess')
        with pytest.raises(ValueError):
            garmin_livetrack.fetch_positions('tok', '')


# ── insert_live_position coordinate validation ────────────────────────────
# These exercise the reject-before-DB path, so no database is required.

@pytest.mark.parametrize('lat,lng', [
    (91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0),
    ('not-a-number', 0.0), (None, 0.0),
])
def test_insert_live_position_rejects_bad_coords(app, lat, lng):
    with app.app_context():
        ok = models.insert_live_position(
            rider_id=7, lat=lat, lng=lng,
            recorded_at=_now(), source='garmin',
        )
    assert ok is False


# ── /api/live/positions ──────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


def test_positions_requires_login(client):
    resp = client.get('/api/live/positions?ride_id=1')
    assert resp.status_code == 401


def test_positions_requires_profile(client):
    with client.session_transaction() as s:
        s['user_id'] = 1   # logged in but no rider_id (incomplete profile)
    resp = client.get('/api/live/positions?ride_id=1')
    assert resp.status_code == 403


def test_positions_requires_ride_id(client):
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = 1
    resp = client.get('/api/live/positions')
    assert resp.status_code == 400


def test_positions_shape_color_and_stale(client):
    rows = [
        {'rider_id': 7, 'name': 'Asha Rider', 'lat': 37.8, 'lng': -122.2,
         'recorded_at': _now() - timedelta(minutes=2), 'status': 'GOING',
         'source': 'garmin'},
        # No 'source' key → API should default it to 'beacon'.
        {'rider_id': 9, 'name': 'Stale Rider', 'lat': 37.9, 'lng': -122.3,
         'recorded_at': _now() - timedelta(minutes=45), 'status': 'GOING'},
    ]
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = 1
    # No route context here — telemetry is exercised in test_live_metrics.py.
    with patch('routes.live.get_latest_positions_for_ride', return_value=rows), \
         patch('routes.live._ride_live_context', return_value={'has_route': False}), \
         patch('routes.live.get_positions_for_rider_since', return_value=[]):
        resp = client.get('/api/live/positions?ride_id=5')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ride_id'] == 5
    assert len(data['positions']) == 2
    fresh, stale = data['positions']
    assert fresh['color'] == '#16a34a'      # GOING → green
    assert fresh['stale'] is False
    assert fresh['source'] == 'garmin'      # passed through from the latest point
    assert stale['stale'] is True
    assert stale['minutes_ago'] >= 10
    assert stale['source'] == 'beacon'      # defaulted when the row has no source


def test_positions_includes_sharer_without_signup(client):
    """A rider sharing for this ride shows even with no signup (status None)."""
    rows = [
        {'rider_id': 7, 'name': 'Walk-up Rider', 'lat': 37.8, 'lng': -122.2,
         'recorded_at': _now() - timedelta(minutes=1), 'status': None,
         'source': 'garmin'},
    ]
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = 1
    with patch('routes.live.get_latest_positions_for_ride', return_value=rows), \
         patch('routes.live._ride_live_context', return_value={'has_route': False}), \
         patch('routes.live.get_positions_for_rider_since', return_value=[]):
        resp = client.get('/api/live/positions?ride_id=5')
    assert resp.status_code == 200
    pos = resp.get_json()['positions']
    assert len(pos) == 1                      # shown despite no signup status
    assert pos[0]['color'] == '#16a34a'       # falls back to the default colour


# ── cross-ride isolation: the latest-positions query is ride-scoped ────────

def test_latest_positions_query_filters_by_ride_id():
    """The leak fix: get_latest_positions_for_ride must constrain p.ride_id so a
    rider Going on several rides only shows on the ride their points belong to.

    Exercises the real query builder (not the route, which other tests mock) by
    capturing the SQL + params handed to _execute.
    """
    import models
    from datetime import datetime, timezone

    captured = {}

    class _FakeCur:
        def fetchall(self):
            return []

    def _fake_execute(sql, params=None):
        captured['sql'] = sql
        captured['params'] = params
        return _FakeCur()

    since = datetime(2026, 6, 24, tzinfo=timezone.utc)
    with patch('models._execute', side_effect=_fake_execute):
        models.get_latest_positions_for_ride(42, since)

    # The position row must be constrained to THIS ride, not just the rider.
    assert 'p.ride_id = %s' in captured['sql']
    # Signup status no longer gates the map — rider_ride is a LEFT JOIN used only
    # for the dot colour, and GOING is not required.
    assert 'LEFT JOIN rider_ride' in captured['sql']
    assert 'rr.status = %s' not in captured['sql']
    # ride_id is bound twice (LEFT JOIN rr.ride_id + p.ride_id), then since.
    assert captured['params'] == (42, 42, since)
    assert models.RideStatus.GOING.value not in captured['params']


# ── /ride/<id>/live map page ──────────────────────────────────────────────

_RIDE = {'id': 5, 'name': 'Mt Hamilton 200K', 'date': '2026-07-04',
         'rwgps_url': None, 'rwgps_url_team': None}


def test_map_page_renders_for_profile_rider(client):
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = 1
    with patch('routes.live.get_ride_by_id', return_value=dict(_RIDE)), \
         patch('routes.live.get_live_tracking', return_value=None):
        resp = client.get('/ride/5/live')
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'Live Map' in html
    assert 'live-map' in html
    assert 'ROUTE_POLYLINE = null' in html   # no RWGPS route on this ride
    # Two ways to appear on the map: Garmin (screen-off) + phone beacon (screen-on)
    assert 'Garmin LiveTrack' in html
    assert 'Share from this phone' in html
    assert 'Share my location' in html       # beacon Start control on the map
    assert '/ride/5/live/garmin' in html     # form posts to the per-ride link endpoint
    assert 'Route conditions' in html
    assert 'Headwind / tailwind by mile' in html
    assert "metricGroup('Now'" in html
    assert "metric('Gradient'" in html
    assert "metric('Wind ahead'" in html


def test_map_page_draws_rwgps_route(client):
    ride = dict(_RIDE, rwgps_url='https://ridewithgps.com/routes/12345')
    route_data = {'track_points': [
        {'x': -122.27, 'y': 37.80, 'd': 0},
        {'x': -122.26, 'y': 37.81, 'd': 100},
        {'x': -122.25, 'y': 37.82, 'd': 200},
    ]}
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = 1
    with patch('routes.live.get_ride_by_id', return_value=ride), \
         patch('routes.live.fetch_route', return_value=route_data), \
         patch('routes.live.get_live_tracking', return_value=None):
        resp = client.get('/ride/5/live')
    assert resp.status_code == 200
    html = resp.data.decode()
    # Polyline rendered as [lng, lat] pairs.
    assert '[-122.27, 37.8]' in html or '[-122.27,37.8]' in html
    assert 'ROUTE_POLYLINE = null' not in html


def test_map_page_route_fetch_failsoft(client):
    ride = dict(_RIDE, rwgps_url='https://ridewithgps.com/routes/12345')
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = 1
    with patch('routes.live.get_ride_by_id', return_value=ride), \
         patch('routes.live.fetch_route', side_effect=Exception('RWGPS down')), \
         patch('routes.live.get_live_tracking', return_value=None):
        resp = client.get('/ride/5/live')
    # Fail-soft: page still renders, just without the route line.
    assert resp.status_code == 200
    assert 'ROUTE_POLYLINE = null' in resp.data.decode()


def test_map_page_404_unknown_ride(client):
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = 1
    with patch('routes.live.get_ride_by_id', return_value=None):
        resp = client.get('/ride/999/live')
    assert resp.status_code == 404


def test_map_page_redirects_without_profile(client):
    with client.session_transaction() as s:
        s['user_id'] = 1   # no rider_id
    resp = client.get('/ride/5/live')
    assert resp.status_code in (301, 302)


# ── /live/settings ────────────────────────────────────────────────────────

def test_settings_get_renders(client):
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = 1
    with patch('routes.live.get_live_tracking', return_value=None):
        resp = client.get('/live/settings')
    assert resp.status_code == 200
    assert 'Live Tracking' in resp.data.decode()


# ── POST /ride/<id>/live/garmin (per-ride Garmin link) ────────────────────

def test_ride_garmin_link_saves_for_this_ride(client):
    captured = {}

    def _set(rider_id, ride_id, url, token):
        captured.update(rider_id=rider_id, ride_id=ride_id, url=url, token=token)
        return True

    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = 7
    with patch('routes.live.get_ride_by_id', return_value=dict(_RIDE)), \
         patch('routes.live.set_ride_garmin', side_effect=_set):
        resp = client.post('/ride/5/live/garmin', data={
            'action': 'save',
            'garmin_session_url': 'https://livetrack.garmin.com/session/s1/token/t1',
        }, follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert captured['rider_id'] == 7
    assert captured['ride_id'] == 5      # scoped to THIS ride, not global
    assert captured['token'] == 't1'


def test_ride_garmin_link_rejects_bad_url(client):
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = 7
    with patch('routes.live.get_ride_by_id', return_value=dict(_RIDE)), \
         patch('routes.live.set_ride_garmin') as mock_set:
        resp = client.post('/ride/5/live/garmin', data={
            'action': 'save',
            'garmin_session_url': 'https://example.com/not-livetrack',
        }, follow_redirects=False)
    assert resp.status_code in (301, 302)   # redirects back with a flash
    mock_set.assert_not_called()


def test_ride_garmin_link_clear(client):
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = 7
    with patch('routes.live.get_ride_by_id', return_value=dict(_RIDE)), \
         patch('routes.live.clear_ride_garmin', return_value=True) as mock_clear:
        resp = client.post('/ride/5/live/garmin', data={'action': 'clear'},
                           follow_redirects=False)
    assert resp.status_code in (301, 302)
    mock_clear.assert_called_once_with(7, 5)


# ── /api/cron/poll-garmin-livetrack ───────────────────────────────────────

def test_cron_requires_auth(client):
    resp = client.post('/api/cron/poll-garmin-livetrack')
    assert resp.status_code in (401, 500)   # 500 if CRON_SECRET unset, 401 if set


def test_cron_polls_inserts_and_purges(client, app):
    app.config['CRON_SECRET'] = 'testsecret'
    tracked = [{
        'rider_id': 7,
        'garmin_session_url': 'https://livetrack.garmin.com/session/s1/token/t1',
        'garmin_session_token': 't1',
        'active_ride_id': 5,
    }]
    # Two points 4 min apart → both kept (history-append, downsample ≥30s).
    points = [
        {'lat': 37.8, 'lng': -122.2, 'recorded_at': _now() - timedelta(minutes=5)},
        {'lat': 37.9, 'lng': -122.3, 'recorded_at': _now() - timedelta(minutes=1)},
    ]
    inserts = []
    with patch('models.get_enabled_live_tracking', return_value=tracked), \
         patch('models.get_last_position_recorded_at', return_value=None), \
         patch('services.garmin_livetrack.fetch_positions', return_value=points), \
         patch('models.insert_live_position', side_effect=lambda **kw: inserts.append(kw) or True), \
         patch('models.purge_old_positions', return_value=3) as mock_purge:
        resp = client.post('/api/cron/poll-garmin-livetrack',
                           headers={'Authorization': 'Bearer testsecret'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['polled'] == 1
    assert data['inserted'] == 2          # full history appended, not latest-only
    assert data['purged'] == 3
    mock_purge.assert_called_once_with(7)
    assert len(inserts) == 2
    assert {i['recorded_at'] for i in inserts} == {p['recorded_at'] for p in points}
    assert inserts[0]['source'] == 'garmin'
    assert all(i['ride_id'] == 5 for i in inserts)   # tagged to the active ride


def test_cron_failsoft_on_fetch_error(client, app):
    app.config['CRON_SECRET'] = 'testsecret'
    tracked = [{
        'rider_id': 7,
        'garmin_session_url': 'https://livetrack.garmin.com/session/s1/token/t1',
        'garmin_session_token': 't1',
        'active_ride_id': 5,
    }]
    with patch('models.get_enabled_live_tracking', return_value=tracked), \
         patch('services.garmin_livetrack.fetch_positions', side_effect=Exception('expired')), \
         patch('models.insert_live_position') as mock_insert, \
         patch('models.purge_old_positions', return_value=0):
        resp = client.post('/api/cron/poll-garmin-livetrack',
                           headers={'Authorization': 'Bearer testsecret'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['polled'] == 1
    assert data['inserted'] == 0
    assert len(data['errors']) == 1
    mock_insert.assert_not_called()


def test_cron_skips_points_that_fail_to_insert(client, app):
    app.config['CRON_SECRET'] = 'testsecret'
    tracked = [{
        'rider_id': 7,
        'garmin_session_url': 'https://livetrack.garmin.com/session/s1/token/t1',
        'garmin_session_token': 't1',
        'active_ride_id': 5,
    }]
    points = [{'lat': 999.0, 'lng': 0.0, 'recorded_at': _now()}]   # bad coords → insert returns False
    with patch('models.get_enabled_live_tracking', return_value=tracked), \
         patch('models.get_last_position_recorded_at', return_value=None), \
         patch('services.garmin_livetrack.fetch_positions', return_value=points), \
         patch('models.insert_live_position', return_value=False), \
         patch('models.purge_old_positions', return_value=0):
        resp = client.post('/api/cron/poll-garmin-livetrack',
                           headers={'Authorization': 'Bearer testsecret'})
    assert resp.status_code == 200
    assert resp.get_json()['inserted'] == 0   # failed inserts are not counted
