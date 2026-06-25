"""Tests for native-app JSON auth: POST /api/auth/google + the mobile bearer
token + dual session-or-token access to the live endpoints.

Google ID-token verification is mocked (no google-auth needed in the test env);
the user/rider model lookups are patched so no database is required.
"""
from unittest.mock import patch

import pytest

import auth as auth_mod


# ── token helpers (auth.py) ───────────────────────────────────────────────

def test_mint_and_load_round_trip(app):
    with app.app_context():
        token = auth_mod.mint_mobile_token(user_id=3, rider_id=7)
        data = auth_mod.load_mobile_token(token)
    assert data == {'user_id': 3, 'rider_id': 7}


@pytest.mark.parametrize('bad', ['', None, 'not-a-token', 'a.b.c'])
def test_load_mobile_token_rejects_garbage(app, bad):
    with app.app_context():
        assert auth_mod.load_mobile_token(bad) is None


def test_load_mobile_token_rejects_wrong_secret(app):
    with app.app_context():
        token = auth_mod.mint_mobile_token(1, 1)
    # A token signed under a different SECRET_KEY must not validate.
    app.config['SECRET_KEY'] = 'a-different-secret-key-value'
    with app.app_context():
        assert auth_mod.load_mobile_token(token) is None


def test_load_mobile_token_expired(app):
    with app.app_context():
        token = auth_mod.mint_mobile_token(1, 1)
        # max_age=0 → already expired.
        with patch.object(auth_mod, 'MOBILE_TOKEN_MAX_AGE', 0):
            import time
            time.sleep(1)
            assert auth_mod.load_mobile_token(token) is None


# ── POST /api/auth/google ─────────────────────────────────────────────────

def _claims(sub='g-sub-1', email='rider@example.com'):
    return {'sub': sub, 'email': email}


def test_google_signin_not_configured_returns_503(client, app):
    app.config['GOOGLE_IOS_CLIENT_ID'] = None
    resp = client.post('/api/auth/google', json={'id_token': 'x'})
    assert resp.status_code == 503


def test_google_signin_requires_id_token(client, app):
    app.config['GOOGLE_IOS_CLIENT_ID'] = 'ios-client-id'
    resp = client.post('/api/auth/google', json={})
    assert resp.status_code == 400


def test_google_signin_invalid_token_401(client, app):
    app.config['GOOGLE_IOS_CLIENT_ID'] = 'ios-client-id'
    with patch('routes.api_auth._verify_google_id_token', side_effect=ValueError('bad token')):
        resp = client.post('/api/auth/google', json={'id_token': 'bad'})
    assert resp.status_code == 401


def test_google_signin_existing_user_mints_token(client, app):
    app.config['GOOGLE_IOS_CLIENT_ID'] = 'ios-client-id'
    user = {'id': 3, 'email': 'rider@example.com', 'google_id': 'g-sub-1',
            'profile_completed': True, 'rider_id': 7}
    with patch('routes.api_auth._verify_google_id_token', return_value=_claims()), \
         patch('models.get_user_by_google_id', return_value=user), \
         patch('models.update_user_login_time') as mock_touch, \
         patch('models.get_user_by_id', return_value=user):
        resp = client.post('/api/auth/google', json={'id_token': 'good'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['rider_id'] == 7
    assert data['profile_complete'] is True
    # The minted token round-trips to the same identity.
    with app.app_context():
        assert auth_mod.load_mobile_token(data['token']) == {'user_id': 3, 'rider_id': 7}
    mock_touch.assert_called_once_with(3)


def test_google_signin_enforces_ios_audience(client, app):
    """Security: the route verifies the ID token against OUR iOS client id, so a
    token minted for a different Google app (different aud) can't be accepted.
    We assert the configured audience is passed to the verifier (the crypto
    enforcement itself is google-auth's, exercised against the real lib in prod)."""
    app.config['GOOGLE_IOS_CLIENT_ID'] = 'the-ios-client-id'
    user = {'id': 3, 'email': 'r@example.com', 'google_id': 'g-sub-1',
            'profile_completed': True, 'rider_id': 7}
    with patch('routes.api_auth._verify_google_id_token', return_value=_claims()) as mock_verify, \
         patch('models.get_user_by_google_id', return_value=user), \
         patch('models.update_user_login_time'), \
         patch('models.get_user_by_id', return_value=user):
        client.post('/api/auth/google', json={'id_token': 'tok'})
    # audience (2nd positional arg) must be our iOS client id, not anything else.
    args, _ = mock_verify.call_args
    assert args[0] == 'tok'
    assert args[1] == 'the-ios-client-id'


def test_google_signin_creates_user_when_new(client, app):
    app.config['GOOGLE_IOS_CLIENT_ID'] = 'ios-client-id'
    new_user = {'id': 9, 'email': 'new@example.com', 'google_id': 'g-sub-2',
                'profile_completed': False, 'rider_id': None}
    with patch('routes.api_auth._verify_google_id_token',
               return_value=_claims(sub='g-sub-2', email='new@example.com')), \
         patch('models.get_user_by_google_id', return_value=None), \
         patch('models.create_user', return_value=new_user) as mock_create:
        resp = client.post('/api/auth/google', json={'id_token': 'good'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['rider_id'] is None
    assert data['profile_complete'] is False     # no rider yet → app prompts setup
    mock_create.assert_called_once_with('new@example.com', 'g-sub-2')


# ── dual session-or-token auth on the live endpoints ──────────────────────

def _bearer(app, user_id=1, rider_id=7):
    with app.app_context():
        return {'Authorization': 'Bearer ' + auth_mod.mint_mobile_token(user_id, rider_id)}


def test_positions_accepts_bearer_token_without_session(client, app):
    rows = [{'rider_id': 7, 'name': 'Tok Rider', 'lat': 37.8, 'lng': -122.2,
             'recorded_at': __import__('datetime').datetime.now(
                 __import__('datetime').timezone.utc), 'status': 'GOING', 'source': 'garmin'}]
    with patch('routes.live.get_latest_positions_for_ride', return_value=rows), \
         patch('routes.live._ride_live_context', return_value={'has_route': False}), \
         patch('routes.live.get_positions_for_rider_since', return_value=[]):
        resp = client.get('/api/live/positions?ride_id=5', headers=_bearer(app))
    assert resp.status_code == 200            # authed purely by the token, no session
    assert len(resp.get_json()['positions']) == 1


def test_positions_no_session_no_token_is_401(client):
    resp = client.get('/api/live/positions?ride_id=5')
    assert resp.status_code == 401            # unchanged: no identity at all


def test_positions_token_without_rider_is_403(client, app):
    # A token for a user who hasn't completed profile (rider_id None) → 403.
    resp = client.get('/api/live/positions?ride_id=5', headers=_bearer(app, rider_id=None))
    assert resp.status_code == 403


def test_beacon_accepts_bearer_token_without_session(client, app):
    captured = {}
    with patch('routes.live.get_live_tracking', return_value={'enabled': True}), \
         patch('routes.live.insert_live_position',
               side_effect=lambda **kw: captured.update(kw) or True):
        resp = client.post('/api/live/beacon',
                           json={'ride_id': 5, 'lat': 37.8, 'lng': -122.2},
                           headers=_bearer(app, rider_id=7))
    assert resp.status_code == 200
    assert captured['rider_id'] == 7          # rider taken from the signed token
    assert captured['ride_id'] == 5


def test_rides_endpoint_token_authed(client, app):
    rides = [
        {'id': 5, 'name': 'Mt Hamilton 200K', 'date': '2026-07-04',
         'distance_km': 200, 'signup_status': 'GOING'},
        {'id': 6, 'name': 'Coast 300K', 'date': '2026-07-18',
         'distance_km': 300, 'signup_status': 'INTERESTED'},
    ]
    with patch('models.get_rider_upcoming_signups', return_value=rides):
        resp = client.get('/api/live/rides', headers=_bearer(app, rider_id=7))
    assert resp.status_code == 200
    data = resp.get_json()['rides']
    assert [r['id'] for r in data] == [5, 6]
    assert data[0]['name'] == 'Mt Hamilton 200K'
    assert data[0]['signup_status'] == 'GOING'


def test_rides_endpoint_requires_auth(client):
    assert client.get('/api/live/rides').status_code == 401


def test_calendar_endpoint_token_authed(client, app):
    rides = [
        {'id': 5, 'name': 'Mt Hamilton 200K', 'date': '2026-07-04', 'distance_km': 200,
         'ride_type': 'Brevet', 'start_location': 'San Jose', 'club_name': 'Team Asha',
         'signup_count': 12},
    ]
    with patch('models.get_upcoming_rides', return_value=rides):
        resp = client.get('/api/calendar', headers=_bearer(app, rider_id=7))
    assert resp.status_code == 200
    data = resp.get_json()['rides']
    assert data[0]['id'] == 5 and data[0]['club_name'] == 'Team Asha'
    assert data[0]['signup_count'] == 12


def test_calendar_endpoint_requires_auth(client):
    assert client.get('/api/calendar').status_code == 401


def test_beacon_still_works_with_session_and_no_token(client, app):
    """No regression: the web session path is unchanged."""
    captured = {}
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = 7
    with patch('routes.live.get_live_tracking', return_value={'enabled': True}), \
         patch('routes.live.insert_live_position',
               side_effect=lambda **kw: captured.update(kw) or True):
        resp = client.post('/api/live/beacon', json={'ride_id': 5, 'lat': 37.8, 'lng': -122.2})
    assert resp.status_code == 200
    assert captured['rider_id'] == 7
