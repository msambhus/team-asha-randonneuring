"""Member live map (Surface B) — access matrix, render, telemetry, route overlay.

BrevetHub pattern: monkeypatch brevethub.models.*, use the `client` fixture, never
a real DB, mock every HTTP call (fetch_route is patched at the route's import
site). The endpoint-split privacy contract is first-class: names + telemetry live
ONLY on this @profile_required member surface; the anonymous positions.json poll
never touches the named query.
"""
from datetime import datetime, timezone
from unittest.mock import patch

_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}

_PUBLIC_RIDE = {'id': 1, 'rider_id': 99, 'is_public': True, 'name': 'Public 200',
                'distance_km': 200, 'start_at': None, 'rwgps_url': None}
_PRIVATE_OTHER = {'id': 1, 'rider_id': 99, 'is_public': False, 'name': 'Private',
                  'distance_km': 200, 'start_at': None, 'rwgps_url': None}


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def _pos(rider_id, name, **tel):
    row = {'rider_id': rider_id, 'name': name, 'lat': 37.5, 'lng': -122.3,
           'recorded_at': datetime.now(timezone.utc),
           'speed': None, 'heart_rate': None, 'power': None, 'cadence': None,
           'source': 'garmin'}
    row.update(tel)
    return row


# --------------------------------------------------------------------------- #
# Member map PAGE — access + render
# --------------------------------------------------------------------------- #
def test_map_page_anonymous_redirects_to_login(client):
    with patch('brevethub.models.get_rider_by_id', return_value=None):
        resp = client.get('/live/1/map')
    assert resp.status_code == 302
    assert '/auth/login' in resp.headers['Location']


def test_map_page_inaccessible_private_ride_404s(app, client):
    _login(client)
    app.config['MAPBOX_ACCESS_TOKEN'] = 'pk.test'
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_PRIVATE_OTHER):
        resp = client.get('/live/1/map')
    assert resp.status_code == 404


def test_map_page_unknown_ride_404s(app, client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=None):
        resp = client.get('/live/1/map')
    assert resp.status_code == 404


def test_map_page_renders_mapbox_with_token(app, client):
    _login(client)
    app.config['MAPBOX_ACCESS_TOKEN'] = 'pk.test-token'
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_PUBLIC_RIDE), \
         patch('brevethub.models.get_live_tracking_rp', return_value=None):
        resp = client.get('/live/1/map')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'style.css' in body
    # The member map is the SHARED Mapbox GL Radial partial (one map implementation).
    assert 'radial-live' in body and 'radial-map' in body and 'mapbox-gl' in body
    assert 'pk.test-token' in body
    assert '/live/1/roster.json' in body      # polls the shared public roster
    assert 'unpkg.com/leaflet' not in body    # no Leaflet on the live path


def test_map_page_no_token_renders_unavailable_without_500(app, client):
    _login(client)
    app.config['MAPBOX_ACCESS_TOKEN'] = None
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_PUBLIC_RIDE), \
         patch('brevethub.models.get_live_tracking_rp', return_value=None):
        resp = client.get('/live/1/map')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Live map unavailable' in body      # shared partial's token-less fallback
    assert 'mapbox-gl.js' not in body          # the Mapbox script is not loaded


def test_map_page_includes_route_polyline_when_ride_has_rwgps(app, client):
    _login(client)
    app.config['MAPBOX_ACCESS_TOKEN'] = 'pk.test-token'
    ride = dict(_PUBLIC_RIDE, rwgps_url='https://ridewithgps.com/routes/123')
    route_data = {'track_points': [{'x': -122.0, 'y': 37.0}, {'x': -122.1, 'y': 37.1}]}
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=ride), \
         patch('brevethub.models.get_live_tracking_rp', return_value=None), \
         patch('brevethub.routes.live.fetch_route', return_value=route_data) as fr:
        resp = client.get('/live/1/map')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    fr.assert_called_once()
    assert '-122.0' in body and '37.0' in body   # ROUTE_POLYLINE injected


def test_map_page_polyline_failsoft_when_route_fetch_errors(app, client):
    _login(client)
    app.config['MAPBOX_ACCESS_TOKEN'] = 'pk.test-token'
    ride = dict(_PUBLIC_RIDE, rwgps_url='https://ridewithgps.com/routes/123')
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=ride), \
         patch('brevethub.models.get_live_tracking_rp', return_value=None), \
         patch('brevethub.routes.live.fetch_route', side_effect=Exception('rwgps down')):
        resp = client.get('/live/1/map')
    assert resp.status_code == 200               # never 500 — dots-only fallback
    assert 'null' in resp.get_data(as_text=True) # ROUTE_POLYLINE = null


# --------------------------------------------------------------------------- #
# Member positions API — access + payload
# --------------------------------------------------------------------------- #
def test_positions_api_anonymous_401(client):
    with patch('brevethub.models.get_ride') as get_ride, \
         patch('brevethub.models.get_live_positions_rp') as get_pos:
        resp = client.get('/live/1/live-positions.json')
    assert resp.status_code == 401
    get_ride.assert_not_called()
    get_pos.assert_not_called()


def test_positions_api_incomplete_profile_403(client):
    """OAuth sets rider_id before signup finishes; an incomplete profile must NOT
    read named locations/telemetry — same bar as the @profile_required page."""
    _login(client)
    incomplete = dict(_RIDER, profile_completed=False)
    with patch('brevethub.models.get_rider_by_id', return_value=incomplete), \
         patch('brevethub.models.get_ride') as get_ride, \
         patch('brevethub.models.get_live_positions_rp') as get_pos:
        resp = client.get('/live/1/live-positions.json')
    assert resp.status_code == 403
    get_ride.assert_not_called()      # gated before any ride lookup
    get_pos.assert_not_called()


def test_positions_api_inaccessible_private_404(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_PRIVATE_OTHER), \
         patch('brevethub.models.get_live_positions_rp') as get_pos:
        resp = client.get('/live/1/live-positions.json')
    assert resp.status_code == 404
    get_pos.assert_not_called()


def test_positions_api_returns_named_telemetry_payload(client):
    _login(client)
    rows = [_pos(7, 'alice', speed=8.3, heart_rate=142, power=210, cadence=88)]
    hist = [{'lat': 37.5, 'lng': -122.3, 'speed': 8.3,
             'recorded_at': datetime.now(timezone.utc)}]
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_PUBLIC_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=rows), \
         patch('brevethub.models.get_rider_position_history_rp', return_value=hist), \
         patch('brevethub.models.get_brevet_route_plan_candidates_rp', return_value=[]):
        resp = client.get('/live/1/live-positions.json')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data['positions']) == 1
    p = data['positions'][0]
    assert p['rider_id'] == 7 and p['name'] == 'alice'
    assert p['status'] == 'going' and p['color'] == '#16a34a'
    assert p['source'] == 'garmin' and p['stale'] is False
    assert 'plan_color' in p                       # plan-timing dot color present
    # Telemetry is now the parent-app-shaped block: source-agnostic 'now' present,
    # plan-aware fields absent (no route/plan on this ride) — M1 basics preserved.
    tel = p['telemetry']
    assert tel['now']['speed_mph'] == round(8.3 * 2.236936, 1)
    assert tel['now']['heart_rate'] == 142
    assert tel['now']['power'] == 210 and tel['now']['cadence'] == 88
    assert tel['on_route'] is None                 # no route → route fields omitted
    assert tel['plan'] is None and tel['next_control'] is None
    assert tel['time_banked_plan_min'] is None


def test_positions_api_multi_rider_two_named_dots(client):
    """Two riders attached to one public ride → two distinct named entries."""
    _login(client)
    rows = [_pos(7, 'alice'), _pos(9, 'bob')]
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_PUBLIC_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=rows), \
         patch('brevethub.models.get_rider_position_history_rp', return_value=[]), \
         patch('brevethub.models.get_brevet_route_plan_candidates_rp', return_value=[]):
        resp = client.get('/live/1/live-positions.json')
    data = resp.get_json()
    names = sorted(p['name'] for p in data['positions'])
    ids = sorted(p['rider_id'] for p in data['positions'])
    assert names == ['alice', 'bob'] and ids == [7, 9]


def test_positions_api_marks_stale_when_old(client):
    _login(client)
    from datetime import datetime as _dt
    # A point far in the past → minutes_ago > STALE_AFTER_MINUTES.
    old = _pos(7, 'alice')
    old['recorded_at'] = _dt(2020, 1, 1, tzinfo=timezone.utc)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_PUBLIC_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=[old]), \
         patch('brevethub.models.get_rider_position_history_rp', return_value=[]), \
         patch('brevethub.models.get_brevet_route_plan_candidates_rp', return_value=[]):
        resp = client.get('/live/1/live-positions.json')
    p = resp.get_json()['positions'][0]
    assert p['stale'] is True and p['minutes_ago'] > 10


# --------------------------------------------------------------------------- #
# Surface-A privacy regression — names never reach the anonymous poll
# --------------------------------------------------------------------------- #
def test_anonymous_poll_never_uses_named_query(client):
    """The world-viewable /live/<id>/positions.json must use the nameless
    get_ride_positions, never the named get_live_positions_rp."""
    ride = {'id': 1, 'name': 'X', 'club_name': 'C'}
    rows = [{'lat': 37.77, 'lng': -122.41,
             'recorded_at': datetime.now(timezone.utc)}]
    with patch('brevethub.models.get_public_ride', return_value=ride), \
         patch('brevethub.models.get_ride_positions', return_value=rows), \
         patch('brevethub.models.get_live_positions_rp') as named:
        resp = client.get('/live/1/positions.json')
    assert resp.status_code == 200
    named.assert_not_called()                    # named query never touched here
    body = resp.get_data(as_text=True)
    assert 'name' not in body and 'email' not in body
