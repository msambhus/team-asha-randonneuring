"""Mobile Bearer live API (Mission 3, Feature 4).

A BH-native, stateless signed token (no DB, no new secret) authenticates the
member live-positions endpoint for a future BrevetHub mobile client. The mint
endpoint is web-login-gated; the member endpoint accepts a session OR a valid
Bearer token and enforces the SAME accessibility gate + no-PII rules as Surface B.
Models are monkeypatched — no real DB.
"""
from datetime import datetime, timezone
from unittest.mock import patch

_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}
_INCOMPLETE = dict(_RIDER, profile_completed=False)
_PUBLIC_RIDE = {'id': 1, 'rider_id': 99, 'is_public': True, 'name': 'Public 200',
                'distance_km': 200, 'start_at': None, 'rwgps_url': None}
_PRIVATE_OTHER = {'id': 1, 'rider_id': 99, 'is_public': False, 'name': 'Private',
                  'distance_km': 200, 'start_at': None, 'rwgps_url': None}


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def _token(app, rider_id=7):
    from brevethub.auth_api import mint_token
    with app.app_context():
        return mint_token(rider_id)


def _bearer(tok):
    return {'Authorization': 'Bearer ' + tok}


# --------------------------------------------------------------------------- #
# Token mint — web-login-gated
# --------------------------------------------------------------------------- #
def test_mint_anonymous_401(client):
    with patch('brevethub.models.get_rider_by_id', return_value=None):
        resp = client.post('/api/auth/token')
    assert resp.status_code == 401


def test_mint_incomplete_profile_403(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_INCOMPLETE):
        resp = client.post('/api/auth/token')
    assert resp.status_code == 403


def test_mint_returns_bearer_token(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER):
        resp = client.post('/api/auth/token')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['token'] and data['token_type'] == 'Bearer'
    assert data['expires_in'] == 30 * 24 * 3600


def test_mint_does_not_accept_a_bearer_token(app, client):
    """Mint is web-login-gated only: a Bearer token cannot mint another token."""
    tok = _token(app)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER):
        resp = client.post('/api/auth/token', headers=_bearer(tok))
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Token roundtrip — sign / verify / tamper
# --------------------------------------------------------------------------- #
def test_token_roundtrip_and_tamper(app):
    from brevethub.auth_api import mint_token, load_token
    with app.app_context():
        tok = mint_token(7)
        assert load_token(tok) == {'rider_id': 7}
        assert load_token(tok + 'x') is None      # tampered
        assert load_token('garbage') is None
        assert load_token('') is None


# --------------------------------------------------------------------------- #
# Member positions endpoint — Bearer OR session, same gate + no-PII
# --------------------------------------------------------------------------- #
def test_member_positions_valid_bearer_no_cookie_200(app, client):
    """A valid Bearer token authenticates the member endpoint with NO session
    cookie — the future mobile client path."""
    tok = _token(app)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_PUBLIC_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=[]), \
         patch('brevethub.routes.live._resolve_ride_plan', return_value=None):
        resp = client.get('/live/1/live-positions.json', headers=_bearer(tok))
    assert resp.status_code == 200
    assert 'positions' in resp.get_json()


def test_member_positions_garbage_bearer_401(client):
    with patch('brevethub.models.get_ride') as get_ride, \
         patch('brevethub.models.get_live_positions_rp') as get_pos:
        resp = client.get('/live/1/live-positions.json',
                          headers={'Authorization': 'Bearer not-a-real-token'})
    assert resp.status_code == 401
    get_ride.assert_not_called()
    get_pos.assert_not_called()


def test_member_positions_no_auth_401(client):
    resp = client.get('/live/1/live-positions.json')
    assert resp.status_code == 401


def test_member_positions_bearer_inaccessible_ride_404(app, client):
    """A valid Bearer still cannot read a private ride the rider does not own."""
    tok = _token(app)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_PRIVATE_OTHER), \
         patch('brevethub.models.get_live_positions_rp') as get_pos:
        resp = client.get('/live/1/live-positions.json', headers=_bearer(tok))
    assert resp.status_code == 404
    get_pos.assert_not_called()


def test_member_positions_bearer_incomplete_profile_403(app, client):
    tok = _token(app, rider_id=8)
    with patch('brevethub.models.get_rider_by_id', return_value=_INCOMPLETE):
        resp = client.get('/live/1/live-positions.json', headers=_bearer(tok))
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# Beacon accepts Bearer too
# --------------------------------------------------------------------------- #
def test_beacon_accepts_bearer(app, client):
    tok = _token(app)
    tracking = {'rider_id': 7, 'enabled': True, 'garmin_session_url': None,
                'garmin_session_token': None, 'active_ride_id': 1, 'updated_at': None}
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=tracking), \
         patch('brevethub.models.get_ride', return_value=_PUBLIC_RIDE), \
         patch('brevethub.models.insert_live_position_rp', return_value=True) as ins:
        resp = client.post('/api/live/beacon',
                           json={'lat': 37.5, 'lng': -122.3, 'ride_id': 1},
                           headers=_bearer(tok))
    assert resp.status_code == 200
    assert ins.call_args.kwargs['rider_id'] == 7


# --------------------------------------------------------------------------- #
# No-PII regression — the anonymous poll leaks no name regardless of consent
# --------------------------------------------------------------------------- #
def _anon_poll(client):
    ride = {'id': 1, 'name': 'X', 'club_name': 'C'}
    rows = [{'lat': 37.77, 'lng': -122.41,
             'recorded_at': datetime(2026, 7, 20, 6, 5, tzinfo=timezone.utc)}]
    with patch('brevethub.models.get_public_ride', return_value=ride), \
         patch('brevethub.models.get_ride_positions', return_value=rows), \
         patch('brevethub.models.get_live_positions_rp') as named:
        resp = client.get('/live/1/positions.json')
    return resp, named


def test_anonymous_poll_no_pii_regardless_of_consent(client):
    """The world-viewable poll never selects a name/email and never touches the
    named query — independent of any rider's consent state (it does not read it)."""
    resp, named = _anon_poll(client)
    assert resp.status_code == 200
    named.assert_not_called()
    body = resp.get_data(as_text=True)
    assert 'name' not in body and 'email' not in body
    assert 'r@example.com' not in body
