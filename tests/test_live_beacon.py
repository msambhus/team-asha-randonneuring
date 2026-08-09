"""Tests for the browser-beacon live tracking (our own location tracking).

Covers POST /api/live/beacon, the /live/share page, and the relaxed
/live/settings opt-in (no Garmin link required). No external HTTP; models
patched so no database is needed.
"""
from unittest.mock import patch


def _login(client, rider_id=7):
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = rider_id


# ── POST /api/live/beacon ─────────────────────────────────────────────────

def test_beacon_requires_login(client):
    resp = client.post('/api/live/beacon', json={'lat': 37.8, 'lng': -122.2})
    assert resp.status_code == 401


def test_beacon_requires_profile(client):
    with client.session_transaction() as s:
        s['user_id'] = 1   # no rider_id
    resp = client.post('/api/live/beacon', json={'lat': 37.8, 'lng': -122.2})
    assert resp.status_code == 403


def test_beacon_requires_opt_in(client):
    _login(client)
    with patch('routes.live.get_live_tracking', return_value={'enabled': False}):
        resp = client.post('/api/live/beacon', json={'lat': 37.8, 'lng': -122.2})
    assert resp.status_code == 403


def test_beacon_requires_coordinates(client):
    _login(client)
    with patch('routes.live.get_live_tracking', return_value={'enabled': True}):
        resp = client.post('/api/live/beacon', json={'accuracy': 5})
    assert resp.status_code == 400


def test_beacon_rejects_invalid_coordinates(client):
    # Range validation itself lives in models.insert_live_position and is
    # exercised by test_live_tracking.test_insert_live_position_rejects_bad_coords;
    # here we only assert the route turns a False insert into a 400.
    _login(client)
    with patch('routes.live.get_live_tracking', return_value={'enabled': True}), \
         patch('routes.live.insert_live_position', return_value=False):
        resp = client.post('/api/live/beacon', json={'lat': 999, 'lng': 0})
    assert resp.status_code == 400


def test_beacon_happy_path_inserts_beacon_source(client):
    captured = {}

    def _insert(**kw):
        captured.update(kw)
        return True

    _login(client, rider_id=7)
    with patch('routes.live.get_live_tracking', return_value={'enabled': True}), \
         patch('routes.live.insert_live_position', side_effect=_insert):
        resp = client.post('/api/live/beacon',
                           json={'ride_id': 5, 'lat': 37.8044, 'lng': -122.2712, 'accuracy': 4.5})
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True
    assert captured['rider_id'] == 7
    assert captured['source'] == 'beacon'
    assert captured['lat'] == 37.8044
    assert captured['accuracy'] == 4.5
    assert captured['ride_id'] == 5      # beacon points are tagged to the ride


def test_beacon_requires_a_ride(client):
    """A beacon with no ride (and no active Garmin ride) can't be placed on a map."""
    _login(client)
    with patch('routes.live.get_live_tracking', return_value={'enabled': True}), \
         patch('routes.live.insert_live_position') as mock_insert:
        resp = client.post('/api/live/beacon', json={'lat': 37.8, 'lng': -122.2})
    assert resp.status_code == 400
    mock_insert.assert_not_called()


def test_beacon_falls_back_to_active_ride(client):
    """Without an explicit ride_id, the beacon uses the rider's active Garmin ride."""
    captured = {}
    _login(client, rider_id=7)
    with patch('routes.live.get_live_tracking',
               return_value={'enabled': True, 'active_ride_id': 42}), \
         patch('routes.live.insert_live_position',
               side_effect=lambda **kw: captured.update(kw) or True):
        resp = client.post('/api/live/beacon', json={'lat': 37.8, 'lng': -122.2})
    assert resp.status_code == 200
    assert captured['ride_id'] == 42


def test_beacon_ignores_client_supplied_rider_id(client):
    """Security: the rider is taken from the session, never the request body."""
    captured = {}
    _login(client, rider_id=7)
    with patch('routes.live.get_live_tracking', return_value={'enabled': True}), \
         patch('routes.live.insert_live_position',
               side_effect=lambda **kw: captured.update(kw) or True):
        resp = client.post('/api/live/beacon',
                           json={'ride_id': 5, 'lat': 37.8, 'lng': -122.2, 'rider_id': 999})
    assert resp.status_code == 200
    assert captured['rider_id'] == 7   # session rider, NOT 999


# ── /live hub ─────────────────────────────────────────────────────────────

def test_hub_renders_actions(client):
    _login(client)
    with patch('routes.live.get_live_tracking', return_value={'enabled': True, 'garmin_session_token': None}), \
         patch('routes.live.get_rider_upcoming_signups', return_value=[]):
        resp = client.get('/live')
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'Share from this phone' in html
    assert 'Live tracking settings' in html
    assert '/live/share' in html
    assert '/live/settings' in html


def test_hub_lists_upcoming_rides_with_live_links(client):
    _login(client)
    rides = [{'id': 131, 'name': 'Surf City 600k', 'date': '2026-06-27', 'signup_status': 'REGISTERED'}]
    with patch('routes.live.get_live_tracking', return_value=None), \
         patch('routes.live.get_rider_upcoming_signups', return_value=rides):
        resp = client.get('/live')
    assert resp.status_code == 200
    html = resp.data.decode()
    assert '/ride/131/live' in html
    assert 'Surf City 600k' in html


def test_hub_empty_when_no_upcoming(client):
    _login(client)
    with patch('routes.live.get_live_tracking', return_value=None), \
         patch('routes.live.get_rider_upcoming_signups', return_value=[]):
        resp = client.get('/live')
    assert resp.status_code == 200
    assert 'no upcoming rides' in resp.data.decode().lower()


def test_hub_requires_profile(client):
    with client.session_transaction() as s:
        s['user_id'] = 1   # no rider_id
    resp = client.get('/live')
    assert resp.status_code in (301, 302)


# ── /live/share page ──────────────────────────────────────────────────────

def test_share_page_shows_controls_when_opted_in(client):
    _login(client)
    with patch('routes.live.get_live_tracking', return_value={'enabled': True}):
        resp = client.get('/live/share')
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'Start sharing' in html
    assert '/api/live/beacon' in html   # the beacon JS is wired up


def test_share_page_always_shows_controls(client):
    """No opt-in detour: /live/share shows Start even when tracking is off."""
    _login(client)
    with patch('routes.live.get_live_tracking', return_value=None):
        resp = client.get('/live/share')
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'Start sharing' in html
    assert '/api/live/sharing' in html              # Start auto-enables
    assert 'Enable live tracking in settings' not in html


# ── POST /api/live/sharing (one-tap opt-in toggle) ─────────────────────────

def test_sharing_toggle_requires_login(client):
    resp = client.post('/api/live/sharing', json={'enabled': True})
    assert resp.status_code == 401


def test_sharing_toggle_requires_profile(client):
    with client.session_transaction() as s:
        s['user_id'] = 1   # no rider_id
    resp = client.post('/api/live/sharing', json={'enabled': True})
    assert resp.status_code == 403


def test_sharing_toggle_enables_preserving_garmin(client):
    captured = {}
    _login(client, rider_id=7)
    # set_live_tracking_enabled only touches the enabled flag — the per-ride
    # Garmin link/active ride are preserved by construction.
    with patch('routes.live.set_live_tracking_enabled',
               side_effect=lambda rid, en: captured.update(rid=rid, en=en) or True):
        resp = client.post('/api/live/sharing', json={'enabled': True})
    assert resp.status_code == 200
    assert resp.get_json()['enabled'] is True
    assert captured['rid'] == 7 and captured['en'] is True


# ── GET /api/live/sharing (read the opt-in state for the mobile toggle) ─────

def test_sharing_status_requires_login(client):
    resp = client.get('/api/live/sharing')
    assert resp.status_code == 401


def test_sharing_status_requires_profile(client):
    with client.session_transaction() as s:
        s['user_id'] = 1   # no rider_id
    resp = client.get('/api/live/sharing')
    assert resp.status_code == 403


def test_sharing_status_true_when_enabled(client):
    _login(client, rider_id=7)
    with patch('routes.live.get_live_tracking', return_value={'enabled': True}):
        resp = client.get('/api/live/sharing')
    assert resp.status_code == 200
    assert resp.get_json()['enabled'] is True


def test_sharing_status_false_when_never_set(client):
    _login(client, rider_id=7)
    with patch('routes.live.get_live_tracking', return_value=None):
        resp = client.get('/api/live/sharing')
    assert resp.status_code == 200
    assert resp.get_json()['enabled'] is False


def test_share_page_requires_profile(client):
    with client.session_transaction() as s:
        s['user_id'] = 1   # no rider_id
    resp = client.get('/live/share')
    assert resp.status_code in (301, 302)


# ── /live/settings master on/off (Garmin link is now per-ride) ─────────────

def test_settings_toggles_master_enable(client):
    captured = {}

    def _set(rider_id, enabled):
        captured.update(rider_id=rider_id, enabled=enabled)
        return True

    _login(client, rider_id=7)
    with patch('routes.live.set_live_tracking_enabled', side_effect=_set), \
         patch('routes.live.get_live_tracking', return_value=None):
        resp = client.post('/live/settings', data={'enabled': 'on'},
                           follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert captured['rider_id'] == 7
    assert captured['enabled'] is True
