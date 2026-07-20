"""Browser beacon + sharing consent (Mission 3, Feature 1).

BrevetHub pattern: monkeypatch brevethub.models.*, use the `client` fixture, never
a real DB. The beacon is doubly gated — CONSENT (rp_live_tracking.enabled) and
SELF-SCOPE (the rider is always the trusted session identity) — and every resolved
ride is accessibility-gated (public OR owned) before a point is stored.
"""
from unittest.mock import patch

_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}
_INCOMPLETE = dict(_RIDER, profile_completed=False)

_PUBLIC_OTHER = {'id': 5, 'rider_id': 99, 'is_public': True, 'name': 'Public 200',
                 'distance_km': 200, 'start_at': None, 'rwgps_url': None}
_PRIVATE_OTHER = {'id': 5, 'rider_id': 99, 'is_public': False, 'name': 'Private',
                  'distance_km': 200, 'start_at': None, 'rwgps_url': None}
_OWN_RIDE = {'id': 5, 'rider_id': 7, 'is_public': False, 'name': 'My private',
             'distance_km': 200, 'start_at': None, 'rwgps_url': None}

_ENABLED = {'rider_id': 7, 'enabled': True, 'garmin_session_url': None,
            'garmin_session_token': None, 'active_ride_id': 5, 'updated_at': None}
_DISABLED = dict(_ENABLED, enabled=False)


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def _post(client, body):
    return client.post('/api/live/beacon', json=body)


# --------------------------------------------------------------------------- #
# Beacon page
# --------------------------------------------------------------------------- #
def test_beacon_page_anonymous_redirects_to_login(client):
    with patch('brevethub.models.get_rider_by_id', return_value=None):
        resp = client.get('/live/share')
    assert resp.status_code == 302
    assert '/auth/login' in resp.headers['Location']


def test_beacon_page_renders_with_ride_context(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=None):
        resp = client.get('/live/share?ride_id=5')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Start sharing' in body
    assert 'watchPosition' in body          # geolocation streaming wired
    assert '/api/live/beacon' in body       # posts to the beacon endpoint
    assert 'var RIDE_ID = 5' in body        # ride context carried into the body


def test_beacon_page_without_ride_shows_hint(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=None):
        resp = client.get('/live/share')
    body = resp.get_data(as_text=True)
    assert 'var RIDE_ID = null' in body
    assert 'share-noride' in body            # the "open a ride's map" hint


# --------------------------------------------------------------------------- #
# Sharing consent read/write
# --------------------------------------------------------------------------- #
def test_sharing_status_anonymous_401(client):
    with patch('brevethub.models.get_rider_by_id', return_value=None):
        resp = client.get('/api/live/sharing')
    assert resp.status_code == 401


def test_sharing_status_incomplete_profile_403(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_INCOMPLETE):
        resp = client.get('/api/live/sharing')
    assert resp.status_code == 403


def test_sharing_status_reflects_enabled(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=_ENABLED):
        resp = client.get('/api/live/sharing')
    assert resp.status_code == 200
    assert resp.get_json()['enabled'] is True


def test_sharing_toggle_on_persists_consent(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.upsert_rider_live_tracking_rp', return_value=True) as up:
        resp = client.post('/api/live/sharing', json={'enabled': True})
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'enabled': True}
    up.assert_called_once_with(7, True)


def test_sharing_toggle_off_revokes_consent(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.upsert_rider_live_tracking_rp', return_value=True) as up:
        resp = client.post('/api/live/sharing', json={'enabled': False})
    assert resp.get_json()['enabled'] is False
    up.assert_called_once_with(7, False)


# --------------------------------------------------------------------------- #
# Beacon insert — auth + consent + self-scope
# --------------------------------------------------------------------------- #
def test_beacon_anonymous_401(client):
    with patch('brevethub.models.get_rider_by_id', return_value=None), \
         patch('brevethub.models.insert_live_position_rp') as ins:
        resp = _post(client, {'lat': 37.5, 'lng': -122.3, 'ride_id': 5})
    assert resp.status_code == 401
    ins.assert_not_called()


def test_beacon_incomplete_profile_403(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_INCOMPLETE), \
         patch('brevethub.models.insert_live_position_rp') as ins:
        resp = _post(client, {'lat': 37.5, 'lng': -122.3, 'ride_id': 5})
    assert resp.status_code == 403
    ins.assert_not_called()


def test_beacon_without_consent_403(client):
    """No consent (enabled False) → the rider is never inserted, so they can never
    appear in the member poll/map."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=_DISABLED), \
         patch('brevethub.models.insert_live_position_rp') as ins:
        resp = _post(client, {'lat': 37.5, 'lng': -122.3, 'ride_id': 5})
    assert resp.status_code == 403
    ins.assert_not_called()


def test_beacon_missing_coords_400(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=_ENABLED), \
         patch('brevethub.models.insert_live_position_rp') as ins:
        resp = _post(client, {'ride_id': 5})
    assert resp.status_code == 400
    ins.assert_not_called()


def test_beacon_out_of_range_coords_400(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=_ENABLED), \
         patch('brevethub.models.insert_live_position_rp') as ins:
        resp = _post(client, {'lat': 999, 'lng': -122.3, 'ride_id': 5})
    assert resp.status_code == 400
    ins.assert_not_called()


def test_beacon_happy_path_stores_source_beacon(client):
    """Authenticated + consented rider posts to their active ride → stored as
    source='beacon' with the trusted session rider_id, never a client value."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=_ENABLED), \
         patch('brevethub.models.get_ride', return_value=_OWN_RIDE), \
         patch('brevethub.models.set_active_ride_rp') as setr, \
         patch('brevethub.models.insert_live_position_rp', return_value=True) as ins:
        resp = _post(client, {'lat': 37.5, 'lng': -122.3, 'ride_id': 5,
                              'accuracy': 12, 'speed': 6.1, 'rider_id': 999})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True and data['ride_id'] == 5
    kwargs = ins.call_args.kwargs
    assert kwargs['rider_id'] == 7           # trusted session identity, not 999
    assert kwargs['source'] == 'beacon'
    assert kwargs['ride_id'] == 5
    # active_ride_id already == 5 → no redundant re-attach write.
    setr.assert_not_called()


# --------------------------------------------------------------------------- #
# Ride resolution — accessibility gate (the multi-rider join + private refusal)
# --------------------------------------------------------------------------- #
def test_beacon_joins_public_ride_they_do_not_own(client):
    """A non-owner beaconing to a PUBLIC ride passes the accessibility gate and is
    attached to it (the multi-rider public-ride join)."""
    _login(client)
    tracking = dict(_ENABLED, active_ride_id=None)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=tracking), \
         patch('brevethub.models.get_ride', return_value=_PUBLIC_OTHER), \
         patch('brevethub.models.set_active_ride_rp') as setr, \
         patch('brevethub.models.insert_live_position_rp', return_value=True) as ins:
        resp = _post(client, {'lat': 37.5, 'lng': -122.3, 'ride_id': 5})
    assert resp.status_code == 200
    setr.assert_called_once_with(7, 5)       # attached to the public ride
    assert ins.call_args.kwargs['ride_id'] == 5


def test_beacon_refuses_private_ride_they_do_not_own(client):
    """A private ride the rider does not own is inaccessible → 403, no insert."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=_ENABLED), \
         patch('brevethub.models.get_ride', return_value=_PRIVATE_OTHER), \
         patch('brevethub.models.set_active_ride_rp') as setr, \
         patch('brevethub.models.insert_live_position_rp') as ins:
        resp = _post(client, {'lat': 37.5, 'lng': -122.3, 'ride_id': 5})
    assert resp.status_code == 403
    setr.assert_not_called()
    ins.assert_not_called()


def test_beacon_malformed_ride_id_400(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=_ENABLED), \
         patch('brevethub.models.insert_live_position_rp') as ins:
        resp = _post(client, {'lat': 37.5, 'lng': -122.3, 'ride_id': 'abc'})
    assert resp.status_code == 400
    ins.assert_not_called()


def test_beacon_no_ride_context_400(client):
    """No explicit ride, no active ride, and no cold-start candidate → 400."""
    _login(client)
    tracking = dict(_ENABLED, active_ride_id=None)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=tracking), \
         patch('brevethub.models.get_auto_attach_ride_rp', return_value=None), \
         patch('brevethub.models.insert_live_position_rp') as ins:
        resp = _post(client, {'lat': 37.5, 'lng': -122.3})
    assert resp.status_code == 400
    ins.assert_not_called()
