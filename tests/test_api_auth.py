"""Tests for native-app JSON auth: POST /api/auth/google + the mobile bearer
token + dual session-or-token access to the live endpoints.

Google ID-token verification is mocked (no google-auth needed in the test env);
the user/rider model lookups are patched so no database is required.
"""
from unittest.mock import patch
from datetime import date, datetime

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


# ── POST /api/auth/apple (Sign in with Apple) ─────────────────────────────

def _apple_claims(sub='a-sub-1', email='rider@example.com'):
    c = {'sub': sub}
    if email is not None:
        c['email'] = email
    return c


def test_apple_signin_requires_identity_token(client, app):
    resp = client.post('/api/auth/apple', json={})
    assert resp.status_code == 400


def test_apple_signin_invalid_token_401(client, app):
    with patch('routes.api_auth._verify_apple_id_token', side_effect=ValueError('bad')):
        resp = client.post('/api/auth/apple', json={'identity_token': 'bad'})
    assert resp.status_code == 401


def test_apple_signin_existing_user_mints_token(client, app):
    user = {'id': 4, 'email': 'rider@example.com', 'google_id': None,
            'apple_sub': 'a-sub-1', 'profile_completed': True, 'rider_id': 7}
    with patch('routes.api_auth._verify_apple_id_token', return_value=_apple_claims()), \
         patch('models.get_user_by_apple_sub', return_value=user), \
         patch('models.update_user_login_time') as mock_touch, \
         patch('models.get_user_by_id', return_value=user):
        resp = client.post('/api/auth/apple', json={'identity_token': 'good'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['rider_id'] == 7 and data['profile_complete'] is True
    with app.app_context():
        assert auth_mod.load_mobile_token(data['token']) == {'user_id': 4, 'rider_id': 7}
    mock_touch.assert_called_once_with(4)


def test_apple_signin_creates_user_when_new(client, app):
    new_user = {'id': 11, 'email': 'new@example.com', 'google_id': None,
                'apple_sub': 'a-sub-2', 'profile_completed': False, 'rider_id': None}
    with patch('routes.api_auth._verify_apple_id_token',
               return_value=_apple_claims(sub='a-sub-2', email='new@example.com')), \
         patch('models.get_user_by_apple_sub', return_value=None), \
         patch('models.create_user_apple', return_value=new_user) as mock_create:
        resp = client.post('/api/auth/apple', json={'identity_token': 'good'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['rider_id'] is None and data['profile_complete'] is False
    mock_create.assert_called_once_with('new@example.com', 'a-sub-2')


def test_apple_signin_links_existing_account_by_verified_email(client, app):
    """First Apple sign-in whose VERIFIED email matches an existing (web) account
    links apple_sub to it and inherits its rider profile — no new empty account."""
    claims = _apple_claims(sub='a-sub-new', email='rider@example.com')
    claims['email_verified'] = 'true'
    existing = {'id': 3, 'email': 'rider@example.com', 'google_id': 'g-1',
                'apple_sub': None, 'profile_completed': True, 'rider_id': 6}
    linked = dict(existing, apple_sub='a-sub-new')
    with patch('routes.api_auth._verify_apple_id_token', return_value=claims), \
         patch('models.get_user_by_apple_sub', return_value=None), \
         patch('models.get_user_by_email', return_value=existing), \
         patch('models.link_apple_sub', return_value=1) as mock_link, \
         patch('models.update_user_login_time'), \
         patch('models.get_user_by_id', return_value=linked), \
         patch('models.create_user_apple') as mock_create:
        resp = client.post('/api/auth/apple', json={'identity_token': 'good'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['rider_id'] == 6 and data['profile_complete'] is True
    mock_link.assert_called_once_with(3, 'a-sub-new')
    mock_create.assert_not_called()


def test_apple_signin_links_with_boolean_email_verified(client, app):
    """Apple encodes email_verified as a JSON boolean (PyJWT → Python True); the
    link must fire for the boolean form, not just the string 'true'."""
    claims = _apple_claims(sub='a-sub-bool', email='rider@example.com')
    claims['email_verified'] = True  # boolean, as real Apple tokens send it
    existing = {'id': 3, 'email': 'rider@example.com', 'google_id': 'g-1',
                'apple_sub': None, 'profile_completed': True, 'rider_id': 6}
    linked = dict(existing, apple_sub='a-sub-bool')
    with patch('routes.api_auth._verify_apple_id_token', return_value=claims), \
         patch('models.get_user_by_apple_sub', return_value=None), \
         patch('models.get_user_by_email', return_value=existing), \
         patch('models.link_apple_sub', return_value=1) as mock_link, \
         patch('models.update_user_login_time'), \
         patch('models.get_user_by_id', return_value=linked), \
         patch('models.create_user_apple') as mock_create:
        resp = client.post('/api/auth/apple', json={'identity_token': 'good'})
    assert resp.status_code == 200
    assert resp.get_json()['rider_id'] == 6
    mock_link.assert_called_once_with(3, 'a-sub-bool')
    mock_create.assert_not_called()


def test_apple_signin_verified_email_no_match_creates(client, app):
    """Verified email that matches NO existing account → create a fresh account."""
    claims = _apple_claims(sub='a-sub-z', email='nobody@example.com')
    claims['email_verified'] = True
    new_user = {'id': 30, 'email': 'nobody@example.com', 'google_id': None,
                'apple_sub': 'a-sub-z', 'profile_completed': False, 'rider_id': None}
    with patch('routes.api_auth._verify_apple_id_token', return_value=claims), \
         patch('models.get_user_by_apple_sub', return_value=None), \
         patch('models.get_user_by_email', return_value=None), \
         patch('models.link_apple_sub') as mock_link, \
         patch('models.create_user_apple', return_value=new_user) as mock_create:
        resp = client.post('/api/auth/apple', json={'identity_token': 'good'})
    assert resp.status_code == 200
    mock_link.assert_not_called()
    mock_create.assert_called_once_with('nobody@example.com', 'a-sub-z')


def test_apple_signin_does_not_link_when_email_unverified(client, app):
    """An unverified (or absent email_verified) token must NOT link to an
    existing account — it creates a fresh Apple account instead."""
    claims = _apple_claims(sub='a-sub-x', email='rider@example.com')  # no email_verified
    new_user = {'id': 12, 'email': 'rider@example.com', 'google_id': None,
                'apple_sub': 'a-sub-x', 'profile_completed': False, 'rider_id': None}
    with patch('routes.api_auth._verify_apple_id_token', return_value=claims), \
         patch('models.get_user_by_apple_sub', return_value=None), \
         patch('models.get_user_by_email') as mock_by_email, \
         patch('models.link_apple_sub') as mock_link, \
         patch('models.create_user_apple', return_value=new_user) as mock_create:
        resp = client.post('/api/auth/apple', json={'identity_token': 'good'})
    assert resp.status_code == 200
    mock_by_email.assert_not_called()   # short-circuits before the email lookup
    mock_link.assert_not_called()
    mock_create.assert_called_once_with('rider@example.com', 'a-sub-x')


def test_apple_signin_does_not_link_when_existing_has_apple_sub(client, app):
    """Guard: if a verified-email match already carries a (different) apple_sub,
    do not relink — create a new account instead of hijacking it."""
    claims = _apple_claims(sub='a-sub-new', email='rider@example.com')
    claims['email_verified'] = 'true'
    existing = {'id': 3, 'email': 'rider@example.com', 'google_id': None,
                'apple_sub': 'a-sub-OLD', 'profile_completed': True, 'rider_id': 6}
    new_user = {'id': 20, 'email': 'rider@example.com', 'google_id': None,
                'apple_sub': 'a-sub-new', 'profile_completed': False, 'rider_id': None}
    with patch('routes.api_auth._verify_apple_id_token', return_value=claims), \
         patch('models.get_user_by_apple_sub', return_value=None), \
         patch('models.get_user_by_email', return_value=existing), \
         patch('models.link_apple_sub') as mock_link, \
         patch('models.create_user_apple', return_value=new_user) as mock_create:
        resp = client.post('/api/auth/apple', json={'identity_token': 'good'})
    assert resp.status_code == 200
    mock_link.assert_not_called()
    mock_create.assert_called_once()


def test_apple_signin_enforces_bundle_audience(client, app):
    """Security: the identity token is verified against OUR bundle id, so a token
    minted for a different app (different aud) can't be accepted."""
    app.config['APPLE_BUNDLE_ID'] = 'org.teamasha.randonneuring'
    user = {'id': 4, 'apple_sub': 'a-sub-1', 'profile_completed': True, 'rider_id': 7}
    with patch('routes.api_auth._verify_apple_id_token', return_value=_apple_claims()) as mock_verify, \
         patch('models.get_user_by_apple_sub', return_value=user), \
         patch('models.update_user_login_time'), \
         patch('models.get_user_by_id', return_value=user):
        client.post('/api/auth/apple', json={'identity_token': 'tok'})
    args, _ = mock_verify.call_args
    assert args[0] == 'tok'
    assert args[1] == 'org.teamasha.randonneuring'


def test_apple_signin_body_email_cannot_override_identity(client, app):
    """Security: identity is keyed on the verified token `sub` only. A body-
    supplied email can't map the request onto a different account, and the
    verified claim email wins over the body email for the stored address."""
    existing = {'id': 4, 'email': 'real@example.com', 'google_id': None,
                'apple_sub': 'a-sub-1', 'profile_completed': True, 'rider_id': 7}
    with patch('routes.api_auth._verify_apple_id_token',
               return_value=_apple_claims(sub='a-sub-1', email='real@example.com')), \
         patch('models.get_user_by_apple_sub', return_value=existing) as mock_lookup, \
         patch('models.create_user_apple') as mock_create, \
         patch('models.update_user_login_time'), \
         patch('models.get_user_by_id', return_value=existing):
        resp = client.post('/api/auth/apple',
                           json={'identity_token': 'good', 'email': 'attacker@evil.com'})
    assert resp.status_code == 200
    # Lookup was by the verified sub, not the body email; existing user reused.
    mock_lookup.assert_called_once_with('a-sub-1')
    mock_create.assert_not_called()
    with app.app_context():
        assert auth_mod.load_mobile_token(resp.get_json()['token']) == {'user_id': 4, 'rider_id': 7}


def test_apple_signin_synthesizes_email_when_hidden(client, app):
    """Apple omits email on later logins / when hidden; a new user still gets a
    non-null email (relay placeholder) so account creation succeeds."""
    new_user = {'id': 12, 'email': 'a-sub-3@privaterelay.appleid.com', 'google_id': None,
                'apple_sub': 'a-sub-3', 'profile_completed': False, 'rider_id': None}
    with patch('routes.api_auth._verify_apple_id_token',
               return_value=_apple_claims(sub='a-sub-3', email=None)), \
         patch('models.get_user_by_apple_sub', return_value=None), \
         patch('models.create_user_apple', return_value=new_user) as mock_create:
        resp = client.post('/api/auth/apple', json={'identity_token': 'good'})
    assert resp.status_code == 200
    mock_create.assert_called_once_with('a-sub-3@privaterelay.appleid.com', 'a-sub-3')


# ── DELETE /api/auth/account (account deletion, Guideline 5.1.1(v)) ────────

def test_delete_account_requires_auth(client):
    assert client.delete('/api/auth/account').status_code == 401


def test_delete_account_success(client, app):
    with patch('models.delete_account', return_value=True) as mock_del:
        resp = client.delete('/api/auth/account', headers=_bearer(app, user_id=3, rider_id=7))
    assert resp.status_code == 200
    assert resp.get_json() == {'deleted': True}
    mock_del.assert_called_once_with(3, preserve_rider=False)


def test_delete_account_not_found_404(client, app):
    with patch('models.delete_account', return_value=False):
        resp = client.delete('/api/auth/account', headers=_bearer(app, user_id=999, rider_id=None))
    assert resp.status_code == 404


def test_delete_account_db_error_500(client, app):
    with patch('models.delete_account', side_effect=Exception('boom')):
        resp = client.delete('/api/auth/account', headers=_bearer(app, user_id=3, rider_id=7))
    assert resp.status_code == 500


def test_delete_account_without_rider_still_works(client, app):
    """A user who never completed profile setup (rider_id None) can still delete."""
    with patch('models.delete_account', return_value=True) as mock_del:
        resp = client.delete('/api/auth/account', headers=_bearer(app, user_id=5, rider_id=None))
    assert resp.status_code == 200
    mock_del.assert_called_once_with(5, preserve_rider=False)


def test_delete_account_preserves_shared_demo_rider(client, app):
    """Deleting the demo/reviewer account removes the login but preserves the
    shared demo rider so App Review can re-exercise the demo login afterwards."""
    app.config['DEMO_RIDER_ID'] = '7'
    with patch('models.delete_account', return_value=True) as mock_del:
        resp = client.delete('/api/auth/account', headers=_bearer(app, user_id=42, rider_id=7))
    assert resp.status_code == 200
    mock_del.assert_called_once_with(42, preserve_rider=True)


# ── POST /api/auth/demo (reviewer login) ──────────────────────────────────

def test_demo_signin_disabled_returns_404(client, app):
    app.config['DEMO_MODE_ENABLED'] = False
    app.config['DEMO_RIDER_ID'] = '7'
    resp = client.post('/api/auth/demo')
    assert resp.status_code == 404


def test_demo_signin_unconfigured_rider_returns_503(client, app):
    app.config['DEMO_MODE_ENABLED'] = True
    app.config['DEMO_RIDER_ID'] = None
    resp = client.post('/api/auth/demo')
    assert resp.status_code == 503


def test_demo_signin_rider_not_found_returns_503(client, app):
    app.config['DEMO_MODE_ENABLED'] = True
    app.config['DEMO_RIDER_ID'] = '7'
    with patch('models.get_rider_by_id', return_value=None):
        resp = client.post('/api/auth/demo')
    assert resp.status_code == 503


def test_demo_signin_existing_user_mints_token(client, app):
    app.config['DEMO_MODE_ENABLED'] = True
    app.config['DEMO_RIDER_ID'] = '7'
    demo_user = {'id': 42, 'email': 'appreview@teamasha.demo',
                 'google_id': 'demo-reviewer', 'profile_completed': True, 'rider_id': 7}
    with patch('models.get_rider_by_id', return_value={'id': 7, 'first_name': 'Demo'}), \
         patch('models.get_user_by_google_id', return_value=demo_user), \
         patch('models.update_user_login_time') as mock_touch, \
         patch('models.complete_user_profile') as mock_link:
        resp = client.post('/api/auth/demo')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['rider_id'] == 7
    assert data['profile_complete'] is True
    with app.app_context():
        assert auth_mod.load_mobile_token(data['token']) == {'user_id': 42, 'rider_id': 7}
    mock_touch.assert_called_once_with(42)
    mock_link.assert_not_called()   # already linked to rider 7 → no relink


def test_demo_signin_creates_and_links_demo_user(client, app):
    app.config['DEMO_MODE_ENABLED'] = True
    app.config['DEMO_RIDER_ID'] = '7'
    new_user = {'id': 99, 'email': 'appreview@teamasha.demo',
                'google_id': 'demo-reviewer', 'profile_completed': False, 'rider_id': None}
    with patch('models.get_rider_by_id', return_value={'id': 7}), \
         patch('models.get_user_by_google_id', return_value=None), \
         patch('models.create_user', return_value=new_user) as mock_create, \
         patch('models.complete_user_profile') as mock_link:
        resp = client.post('/api/auth/demo')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['rider_id'] == 7
    mock_create.assert_called_once_with('appreview@teamasha.demo', 'demo-reviewer')
    mock_link.assert_called_once_with(99, 7)   # newly created → linked to the demo rider
    with app.app_context():
        assert auth_mod.load_mobile_token(data['token']) == {'user_id': 99, 'rider_id': 7}


def test_demo_delete_signin_uses_separate_resettable_identity(client, app):
    app.config['DEMO_MODE_ENABLED'] = True
    app.config['DEMO_RIDER_ID'] = '7'
    new_user = {'id': 100, 'email': 'delete-review@teamasha.demo',
                'google_id': 'demo-delete-reviewer', 'rider_id': None}
    with patch('models.get_rider_by_id', return_value={'id': 7}), \
         patch('models.get_user_by_google_id', return_value=None) as mock_find, \
         patch('models.create_user', return_value=new_user) as mock_create, \
         patch('models.complete_user_profile') as mock_link:
        resp = client.post('/api/auth/demo-delete')

    assert resp.status_code == 200
    mock_find.assert_called_once_with('demo-delete-reviewer')
    mock_create.assert_called_once_with(
        'delete-review@teamasha.demo', 'demo-delete-reviewer')
    mock_link.assert_called_once_with(100, 7)
    with app.app_context():
        assert auth_mod.load_mobile_token(resp.get_json()['token']) == {
            'user_id': 100, 'rider_id': 7,
        }


def test_demo_signin_ignores_request_body_rider(client, app):
    """Security: the rider is pinned to the server's DEMO_RIDER_ID — a caller
    cannot pick a different rider via the request body."""
    app.config['DEMO_MODE_ENABLED'] = True
    app.config['DEMO_RIDER_ID'] = '7'
    demo_user = {'id': 42, 'google_id': 'demo-reviewer',
                 'profile_completed': True, 'rider_id': 7}
    with patch('models.get_rider_by_id', return_value={'id': 7}), \
         patch('models.get_user_by_google_id', return_value=demo_user), \
         patch('models.update_user_login_time'):
        resp = client.post('/api/auth/demo', json={'rider_id': 1, 'user_id': 999})
    assert resp.status_code == 200
    with app.app_context():
        # Still rider 7 / the demo user — the attacker-supplied ids are ignored.
        assert auth_mod.load_mobile_token(resp.get_json()['token']) == {'user_id': 42, 'rider_id': 7}


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


def test_followed_live_rides_are_account_scoped(client, app):
    with patch('routes.live.get_followed_live_ride_ids', return_value=[9, 5]) as follows:
        resp = client.get('/api/me/followed-live-rides',
                          headers=_bearer(app, rider_id=7))
    assert resp.status_code == 200
    assert resp.get_json() == {'ride_ids': [9, 5]}
    follows.assert_called_once_with(7)


def test_followed_live_ride_update_uses_token_identity(client, app):
    with patch('routes.live.get_ride_by_id', return_value={'id': 9}), \
         patch('routes.live.set_followed_live_ride', return_value=[9]) as save:
        resp = client.put('/api/me/followed-live-rides/9',
                          json={'followed': True},
                          headers=_bearer(app, rider_id=7))
    assert resp.status_code == 200
    assert resp.get_json()['ride_ids'] == [9]
    save.assert_called_once_with(7, 9, True)


def test_followed_live_ride_update_validates_payload(client, app):
    with patch('routes.live.get_ride_by_id', return_value={'id': 9}):
        resp = client.put('/api/me/followed-live-rides/9', json={},
                          headers=_bearer(app, rider_id=7))
    assert resp.status_code == 400


def test_calendar_endpoint_token_authed(client, app):
    # get_all_upcoming_events: full calendar = Team Asha + external club brevets.
    events = [
        {'id': 5, 'route_name': 'Mt Hamilton 200K', 'name': 'Mt Hamilton 200K',
         'date_str': '2026-07-04', 'distance_km': 200, 'ride_type': 'Brevet',
         'start_location': 'San Jose', 'club_name': 'Team Asha', 'signup_count': 12,
         'is_team_ride': True, 'is_live': False},
        {'id': 9, 'route_name': 'Orr Springs 600k', 'name': 'Orr Springs 600k',
         'date_str': '2026-06-27', 'distance_km': 600, 'ride_type': 'Brevet',
         'start_location': None, 'club_name': 'San Francisco Randonneurs',
         'signup_count': 0, 'is_team_ride': False, 'is_live': True},
    ]
    with patch('models.get_all_upcoming_events', return_value=events) as calendar, \
         patch('models.get_rider_signup_statuses_batch', return_value={
             5: {'status': 'GOING'},
         }):
        resp = client.get('/api/calendar', headers=_bearer(app, rider_id=7))
    assert resp.status_code == 200
    data = resp.get_json()['rides']
    assert [r['id'] for r in data] == [5, 9]
    # Team Asha ride keeps its flag; external SFR brevet is included (the bug fix).
    assert data[0]['name'] == 'Mt Hamilton 200K' and data[0]['is_team_ride'] is True
    assert data[0]['signup_status'] == 'GOING'
    assert data[1]['signup_status'] is None
    assert data[1]['club_name'] == 'San Francisco Randonneurs' and data[1]['is_team_ride'] is False
    assert data[1]['is_live'] is True
    calendar.assert_called_once_with(include_active=True)


def test_calendar_endpoint_requires_auth(client):
    assert client.get('/api/calendar').status_code == 401


def test_mobile_calendar_status_uses_token_rider(client, app):
    with patch('models.get_ride_by_id', return_value={'id': 5}), \
         patch('models.signup_rider', return_value=True) as signup:
        resp = client.post('/api/calendar/5/status', json={'status': 'GOING'},
                           headers=_bearer(app, user_id=3, rider_id=7))
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'GOING'
    signup.assert_called_once_with(7, 5)


def test_mobile_calendar_not_going_removes_own_signup(client, app):
    with patch('models.get_ride_by_id', return_value={'id': 5}), \
         patch('models.remove_signup', return_value=True) as remove:
        resp = client.post('/api/calendar/5/status', json={'status': 'NONE'},
                           headers=_bearer(app, user_id=3, rider_id=7))
    assert resp.status_code == 200
    assert resp.get_json()['status'] is None
    remove.assert_called_once_with(7, 5)


def test_mobile_calendar_status_requires_profile(client, app):
    resp = client.post('/api/calendar/5/status', json={'status': 'GOING'},
                       headers=_bearer(app, rider_id=None))
    assert resp.status_code == 403


def test_mobile_profile_reuses_career_models(client, app):
    rider = {'id': 7, 'rusa_id': 14680, 'first_name': 'Mihir',
             'last_name': 'Sambhus'}
    with patch('models.get_rider_by_id', return_value=rider), \
         patch('models.get_rider_career_stats', return_value={
             'total_rides': 42, 'total_kms': 12345.6,
         }), \
         patch('models.get_rider_total_srs', return_value=3):
        resp = client.get('/api/me/profile', headers=_bearer(app, rider_id=7))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['rider']['rusa_id'] == 14680
    assert data['career'] == {
        'rides': 42, 'distance_km': 12346, 'super_randonneur': 3,
    }


def test_public_rider_directory_uses_only_public_career_models(client, app):
    seasons = [{'id': 2, 'name': '2025-2026', 'is_current': True}]
    rows = [{'id': 7, 'rusa_id': 14680, 'first_name': 'Mihir',
             'last_name': 'Sambhus', 'total_rides': 42, 'total_kms': 12345,
             'season_rides': 5, 'season_kms': 1500,
             'eddington_number_miles': 61,
             'sr_200': 1, 'sr_300': 1, 'sr_400': 0, 'sr_600': 0}]
    with patch('models.get_all_seasons', return_value=seasons), \
         patch('models.get_current_season', return_value=seasons[0]), \
         patch('models.get_all_riders_with_career_stats', return_value=rows):
        resp = client.get('/api/riders', headers=_bearer(app, rider_id=7))
    assert resp.status_code == 200
    rider = resp.get_json()['riders'][0]
    assert rider['display_name'] == 'Mihir Sambhus'
    assert rider['eddington_miles'] == 61
    assert rider['sr_progress'] == [200, 300]
    assert 'strava' not in rider and 'email' not in rider


def test_public_rider_profile_returns_brevet_history_not_training(client, app):
    rider = {'id': 7, 'rusa_id': 14680, 'first_name': 'Mihir', 'last_name': 'Sambhus'}
    season = {'id': 2, 'name': '2025-2026', 'is_current': True}
    history = [{'id': 5, 'name': 'Coast 200K', 'date': '2026-07-04',
                'distance_km': 200, 'status': 'FINISHED', 'ride_type': 'Brevet',
                'finish_time': None}]
    with patch('models.get_rider_by_rusa', return_value=rider), \
         patch('models.get_all_seasons', return_value=[season]), \
         patch('models.get_current_season', return_value=season), \
         patch('models.get_rider_participation', return_value=history), \
         patch('models.get_rider_season_stats', return_value={'rides': 1, 'kms': 200}), \
         patch('models.detect_sr_for_rider_season', return_value=0), \
         patch('models.get_rider_total_srs', return_value=2), \
         patch('models.detect_r12_awards', return_value=[]):
        resp = client.get('/api/riders/14680', headers=_bearer(app, rider_id=9))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['career']['distance_km'] == 200
    assert data['seasons'][0]['history'][0]['name'] == 'Coast 200K'
    assert 'training' not in data and 'strava' not in data


def test_training_log_is_owner_scoped_and_month_bounded(client, app):
    activity = {
        'strava_activity_id': 123, 'name': 'Morning Ride', 'activity_type': 'Ride',
        'distance': 32186.88, 'moving_time': 3600, 'elapsed_time': 3900,
        'total_elevation_gain': 304.8,
        'start_date_local': datetime(2026, 8, 7, 6, 30),
        'average_heartrate': 141, 'average_watts': 180,
        'suffer_score': 72, 'calories': 800, 'trainer': False,
        'commute': False, 'strava_url': 'https://www.strava.com/activities/123',
    }
    with patch('models.get_strava_connection', return_value={'rider_id': 7}), \
         patch('models.get_strava_activities_between', return_value=[activity]) as rows:
        resp = client.get('/api/me/training-log?month=2026-08',
                          headers=_bearer(app, rider_id=7))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['attribution'] == 'Powered by Strava'
    assert data['activities'][0]['distance_mi'] == 20.0
    assert data['activities'][0]['elevation_ft'] == 1000
    rows.assert_called_once_with(7, date(2026, 8, 1), date(2026, 9, 1))


def test_training_log_rejects_invalid_month(client, app):
    resp = client.get('/api/me/training-log?month=August',
                      headers=_bearer(app, rider_id=7))
    assert resp.status_code == 400


def test_ride_route_endpoint_token_authed(client, app):
    poly = [[-122.4, 37.8], [-122.41, 37.81], [-122.42, 37.82]]
    with patch('routes.live._ride_route_polyline', return_value=poly):
        resp = client.get('/api/ride/5/route', headers=_bearer(app, rider_id=7))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ride_id'] == 5 and data['polyline'] == poly


def test_ride_route_endpoint_empty_when_no_route(client, app):
    # Fail-soft: no resolvable route → empty polyline, not an error.
    with patch('routes.live._ride_route_polyline', return_value=None):
        resp = client.get('/api/ride/5/route', headers=_bearer(app, rider_id=7))
    assert resp.status_code == 200
    assert resp.get_json()['polyline'] == []


def test_ride_route_endpoint_requires_auth(client):
    assert client.get('/api/ride/5/route').status_code == 401


def test_ride_route_endpoint_no_profile_403(client, app):
    resp = client.get('/api/ride/5/route', headers=_bearer(app, rider_id=None))
    assert resp.status_code == 403


def _patch_season(**overrides):
    """Patch every model fn /api/me/season assembles. Override any return value
    by keyword (e.g. season=None, conn={...})."""
    season = overrides.get('season', {'id': 1, 'name': '2025-2026', 'is_current': True})
    seasons = overrides.get('seasons', [season] if season else [])
    stats = overrides.get('stats', {'rides': 5, 'kms': 1200})
    elevation = overrides.get('elevation', 42000)
    sr_count = overrides.get('sr_count', 1)
    distances = overrides.get('distances', [200, 300, 400, 600])
    sr_counts = overrides.get('sr_counts', {200: 1, 300: 1, 400: 1, 600: 1})
    rides_done = overrides.get('rides_done', [
        {'id': 5, 'name': 'Mt Hamilton 200K', 'date': '2026-05-04', 'distance_km': 200},
    ])
    r12 = overrides.get('r12', {'months': 8, 'active': True})
    career = overrides.get('career', {'total_rides': 30, 'total_kms': 9000})
    conn = overrides.get('conn', {'eddington_number_miles': 62, 'eddington_number_km': 70})
    badge = overrides.get('badge', {'level': 'strong', 'label': 'Strong', 'emoji': '💪'})
    return [
        patch('models.get_current_season', return_value=season),
        patch('models.get_all_seasons', return_value=seasons),
        patch('models.get_rider_season_stats', return_value=stats),
        patch('models.get_rider_season_elevation_ft', return_value=elevation),
        patch('models.detect_sr_for_rider_season', return_value=sr_count),
        patch('models.get_sr_distances_done', return_value=distances),
        patch('models.get_sr_counts_by_tier', return_value=sr_counts),
        patch('models.get_rider_finished_rides_for_season', return_value=rides_done),
        patch('models.get_r12_current_streak', return_value=r12),
        patch('models.get_rider_career_stats', return_value=career),
        patch('models.get_strava_connection', return_value=conn),
        patch('services.eddington.get_eddington_badge_level', return_value=badge),
    ]


def test_my_season_token_authed(client, app):
    import contextlib
    with contextlib.ExitStack() as stack:
        for p in _patch_season():
            stack.enter_context(p)
        resp = client.get('/api/me/season', headers=_bearer(app, rider_id=7))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['season']['name'] == '2025-2026'
    assert data['season']['is_current'] is True
    assert data['seasons'][0]['id'] == 1
    assert data['stats'] == {'distance_km': 1200, 'rides': 5, 'elevation_ft': 42000}
    assert data['sr'] == {
        'has_sr': True,
        'distances_done': [200, 300, 400, 600],
        'counts': {'200': 1, '300': 1, '400': 1, '600': 1},
    }
    assert data['rides_done'] == [
        {'id': 5, 'name': 'Mt Hamilton 200K', 'date': '2026-05-04', 'distance_km': 200},
    ]
    assert data['r12'] == {'months': 8, 'active': True}
    assert data['career'] == {'distance_km': 9000}
    assert data['eddington']['value'] == 62
    assert data['eddington']['badge']['label'] == 'Strong'


def test_my_season_no_sr_yet(client, app):
    import contextlib
    with contextlib.ExitStack() as stack:
        for p in _patch_season(sr_count=0, distances=[200]):
            stack.enter_context(p)
        resp = client.get('/api/me/season', headers=_bearer(app, rider_id=7))
    data = resp.get_json()
    assert data['sr']['has_sr'] is False
    assert data['sr']['distances_done'] == [200]


def test_my_season_can_select_historical_season(client, app):
    import contextlib
    current = {'id': 2, 'name': '2025-2026', 'is_current': True}
    past = {'id': 1, 'name': '2024-2025', 'is_current': False}
    with contextlib.ExitStack() as stack:
        for p in _patch_season(season=current, seasons=[current, past], stats={'rides': 2, 'kms': 500}):
            stack.enter_context(p)
        resp = client.get('/api/me/season?season_id=1', headers=_bearer(app, rider_id=7))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['season'] == {'id': 1, 'name': '2024-2025', 'is_current': False}
    assert data['stats']['distance_km'] == 500


def test_my_season_rejects_unknown_season(client, app):
    import contextlib
    with contextlib.ExitStack() as stack:
        for p in _patch_season():
            stack.enter_context(p)
        resp = client.get('/api/me/season?season_id=999', headers=_bearer(app, rider_id=7))
    assert resp.status_code == 404


def test_my_season_unescapes_ride_names_and_keys_counts(client, app):
    """rides_done names are HTML-unescaped; SR counts are returned keyed by str."""
    import contextlib
    rides = [{'id': 9, 'name': 'Paris&ndash;Brest&nbsp;600K', 'date': '2026-06-01', 'distance_km': 600}]
    with contextlib.ExitStack() as stack:
        for p in _patch_season(sr_counts={200: 2, 300: 0, 400: 1, 600: 1}, rides_done=rides):
            stack.enter_context(p)
        resp = client.get('/api/me/season', headers=_bearer(app, rider_id=7))
    data = resp.get_json()
    assert data['sr']['counts'] == {'200': 2, '300': 0, '400': 1, '600': 1}
    assert data['rides_done'][0]['name'] == 'Paris–Brest 600K'  # entities decoded, nbsp → space


def test_my_season_eddington_null_without_strava(client, app):
    import contextlib
    with contextlib.ExitStack() as stack:
        for p in _patch_season(conn=None):
            stack.enter_context(p)
        resp = client.get('/api/me/season', headers=_bearer(app, rider_id=7))
    assert resp.get_json()['eddington'] is None


def test_my_season_no_current_season_404(client, app):
    import contextlib
    with contextlib.ExitStack() as stack:
        for p in _patch_season(season=None):
            stack.enter_context(p)
        resp = client.get('/api/me/season', headers=_bearer(app, rider_id=7))
    assert resp.status_code == 404


def test_my_season_requires_auth(client):
    assert client.get('/api/me/season').status_code == 401


def test_my_season_token_without_rider_is_403(client, app):
    resp = client.get('/api/me/season', headers=_bearer(app, rider_id=None))
    assert resp.status_code == 403


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


# ── GET /api/ride/<id>/weather (mirrors the web /weather forecast) ──────────

from datetime import date as _wx_date, timedelta as _wx_td

_RIDE_WX = {
    'id': 5, 'name': 'Hamilton 200K',
    'rwgps_url': 'https://ridewithgps.com/routes/12345',
    'rwgps_url_team': None, 'plan_slug': 'hamilton-200k', 'plan_start_time': '07:00',
}


def _ride_wx(**over):
    r = dict(_RIDE_WX)
    r.update(over)
    return r


def test_ride_weather_requires_auth(client):
    assert client.get('/api/ride/5/weather').status_code == 401


def test_ride_weather_happy_path(client, app):
    ride = _ride_wx(date=_wx_date.today() + _wx_td(days=3))
    payload = {'route_name': 'Hamilton 200K', 'table_segments': [], 'map_segments': [],
               'chart_data': {}, 'polyline': [], 'ride_summary': 'mild',
               'temp_range': {'min_f': 50, 'max_f': 70},
               'attribution': '*Weather data: Open-Meteo*'}
    with patch('routes.live.get_ride_by_id', return_value=ride), \
         patch('models.get_ride_plan_by_slug', return_value={'slug': 'hamilton-200k'}), \
         patch('routes.weather.build_weather_payload',
               return_value=(payload, None)) as mock_b:
        resp = client.get('/api/ride/5/weather', headers=_bearer(app))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['available'] is True
    assert data['route_name'] == 'Hamilton 200K'
    # plan resolved (FK slug) and passed through with the rider for custom-plan timing
    args, kwargs = mock_b.call_args
    assert str(args[0]) == '12345'
    assert kwargs['plan_slug'] == 'hamilton-200k'
    assert kwargs['rider_id'] == 7


def test_ride_weather_no_route(client, app):
    ride = _ride_wx(rwgps_url=None, rwgps_url_team=None,
                    date=_wx_date.today() + _wx_td(days=3))
    with patch('routes.live.get_ride_by_id', return_value=ride):
        resp = client.get('/api/ride/5/weather', headers=_bearer(app))
    assert resp.status_code == 200
    assert resp.get_json() == {'available': False, 'reason': 'no_route',
                               'message': 'No route is attached to this ride yet.'}


def test_ride_weather_past_ride(client, app):
    ride = _ride_wx(date=_wx_date.today() - _wx_td(days=1))
    with patch('routes.live.get_ride_by_id', return_value=ride):
        resp = client.get('/api/ride/5/weather', headers=_bearer(app))
    assert resp.get_json()['reason'] == 'past_ride'


def test_ride_weather_beyond_forecast_horizon(client, app):
    ride = _ride_wx(date=_wx_date.today() + _wx_td(days=30))
    with patch('routes.live.get_ride_by_id', return_value=ride):
        resp = client.get('/api/ride/5/weather', headers=_bearer(app))
    body = resp.get_json()
    assert body['available'] is False and body['reason'] == 'forecast_horizon'


def test_ride_weather_404_unknown_ride(client, app):
    with patch('routes.live.get_ride_by_id', return_value=None):
        resp = client.get('/api/ride/999/weather', headers=_bearer(app))
    assert resp.status_code == 404


# ── GET /api/ride/<id>/plan (ride plan stops + timing) ──────────────────────

from datetime import date as _pl_date

_PLAN = {
    'id': 7, 'name': 'SFR 100', 'slug': 'sfr-100',
    'total_distance_miles': 100, 'total_elevation_ft': 5000, 'distance_km': 160,
    'cutoff_hours': 10, 'start_time': '06:00', 'overall_ft_per_mile': 50,
    'rwgps_url': None, 'rwgps_url_team': None,
}
_PLAN_STOPS = [
    {'stop_order': 1, 'location': 'Start', 'stop_type': 'start', 'distance_miles': 0,
     'segment_time_min': 0, 'stop_duration_min': 0, 'elevation_gain': 0, 'stop_name': None, 'notes': None},
    {'stop_order': 2, 'location': 'Control 1', 'stop_type': 'control', 'distance_miles': 50,
     'segment_time_min': 180, 'stop_duration_min': 15, 'elevation_gain': 2000, 'stop_name': 'Lunch', 'notes': 'Cafe'},
    {'stop_order': 3, 'location': 'Finish', 'stop_type': 'finish', 'distance_miles': 100,
     'segment_time_min': 180, 'stop_duration_min': 0, 'elevation_gain': 1000, 'stop_name': None, 'notes': None},
]


def _ride_pl(**over):
    r = {'id': 5, 'date': _pl_date(2026, 7, 4), 'plan_slug': 'sfr-100',
         'rwgps_url': None, 'rwgps_url_team': None, 'plan_start_time': '06:00'}
    r.update(over)
    return r


def test_ride_plan_requires_auth(client):
    assert client.get('/api/ride/5/plan').status_code == 401


def test_ride_plan_no_plan(client, app):
    # No FK slug and no name match → no plan.
    with patch('routes.live.get_ride_by_id', return_value=_ride_pl(plan_slug=None, name='Mystery Ride')), \
         patch('models.get_all_ride_plans', return_value=[]):
        resp = client.get('/api/ride/5/plan', headers=_bearer(app))
    assert resp.status_code == 200
    assert resp.get_json()['reason'] == 'no_plan'


def test_ride_plan_resolves_by_name_when_fk_missing(client, app):
    """SCR 600k case: ride_plan_id is null, but the plan is found by route-name match
    (same matcher the web uses)."""
    ride = _ride_pl(plan_slug=None, name='Surf City 600k VI Brevet (#3141)')
    matched = dict(_PLAN); matched['name'] = 'SCR Surf City VI 600k #3141'; matched['slug'] = 'scr-surf-city-vi-600k-3141'
    with patch('routes.live.get_ride_by_id', return_value=ride), \
         patch('models.get_all_ride_plans', return_value=[
             {'name': 'Mendocino Coast 600K', 'slug': 'mendocino-coast-600k'},
             {'name': 'SCR Surf City VI 600k #3141', 'slug': 'scr-surf-city-vi-600k-3141'}]), \
         patch('models.get_ride_plan_by_slug', return_value=matched), \
         patch('models.get_custom_plan', return_value=None), \
         patch('models.get_ride_plan_stops', return_value=[dict(s) for s in _PLAN_STOPS]):
        resp = client.get('/api/ride/5/plan', headers=_bearer(app))
    data = resp.get_json()
    assert data['available'] is True
    assert data['plan']['slug'] == 'scr-surf-city-vi-600k-3141'
    assert data['using_custom'] is False and data['has_custom'] is False


def test_ride_plan_404_unknown_ride(client, app):
    with patch('routes.live.get_ride_by_id', return_value=None):
        resp = client.get('/api/ride/999/plan', headers=_bearer(app))
    assert resp.status_code == 404


def test_ride_plan_computes_timing_and_time_bank(client, app):
    with patch('routes.live.get_ride_by_id', return_value=_ride_pl()), \
         patch('models.get_ride_plan_by_slug', return_value=dict(_PLAN)), \
         patch('models.get_custom_plan', return_value=None), \
         patch('models.get_ride_plan_stops', return_value=[dict(s) for s in _PLAN_STOPS]):
        resp = client.get('/api/ride/5/plan', headers=_bearer(app))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['available'] is True
    assert data['plan']['name'] == 'SFR 100'
    stops = data['stops']
    assert len(stops) == 3
    s2 = stops[1]
    assert s2['cum_time_min'] == 195 and s2['arrival_time_min'] == 180
    assert s2['time_bank_min'] == 120          # bookend 300 − arrival 180
    assert s2['eta'] == '9:00 AM'              # 06:00 + 180 min
    assert s2['ft_per_mi'] == 40               # 2000 ft / 50 mi
    assert s2['stop_name'] == 'Lunch'
    s3 = stops[2]
    assert s3['cum_time_min'] == 375 and s3['time_bank_min'] == 225
    assert s3['eta'] == '12:15 PM'
    assert 'wind_speed_mph' not in s2          # no rwgps url → wind skipped


def test_ride_plan_prefers_ride_cutoff_and_start_time(client, app):
    """Canonical event fields (ride.time_limit_hours / ride.start_time) win over the
    deprecated ride_plan columns, so ETA + time bank match the web page."""
    plan = dict(_PLAN)
    plan['cutoff_hours'] = None         # deprecated plan column absent
    plan['start_time'] = None
    ride = _ride_pl(time_limit_hours=10, start_time='06:00', plan_start_time='09:99')
    with patch('routes.live.get_ride_by_id', return_value=ride), \
         patch('models.get_ride_plan_by_slug', return_value=plan), \
         patch('models.get_custom_plan', return_value=None), \
         patch('models.get_ride_plan_stops', return_value=[dict(s) for s in _PLAN_STOPS]):
        resp = client.get('/api/ride/5/plan', headers=_bearer(app))
    data = resp.get_json()
    s2 = data['stops'][1]
    assert s2['eta'] == '9:00 AM'        # ride.start_time 06:00 used, not the bogus alias
    assert s2['time_bank_min'] == 120    # ride.time_limit_hours 10 used for the bookend


def test_ride_plan_attaches_wind_best_effort(client, app):
    winds = [
        {'wind_speed_mph': 5, 'label': 'tailwind', 'wind_direction_deg': 10, 'temperature_f': 60},
        {'wind_speed_mph': 12, 'label': 'headwind', 'wind_direction_deg': 200, 'temperature_f': 64},
        {'wind_speed_mph': 8, 'label': 'crosswind', 'wind_direction_deg': 90, 'temperature_f': 70},
    ]
    with patch('routes.live.get_ride_by_id',
               return_value=_ride_pl(rwgps_url='https://ridewithgps.com/routes/123')), \
         patch('models.get_ride_plan_by_slug', return_value=dict(_PLAN)), \
         patch('models.get_custom_plan', return_value=None), \
         patch('models.get_ride_plan_stops', return_value=[dict(s) for s in _PLAN_STOPS]), \
         patch('routes.live.fetch_route', return_value={'track_points': [{'x': -122, 'y': 37, 'd': 0}]}), \
         patch('services.weather.fetch_stop_wind', return_value=winds):
        resp = client.get('/api/ride/5/plan', headers=_bearer(app))
    data = resp.get_json()
    assert data['stops'][1]['wind_speed_mph'] == 12
    assert data['stops'][1]['wind_label'] == 'headwind'
    assert data['stops'][1]['temperature_f'] == 64


def test_ride_plan_uses_custom_by_default_and_view_base_toggles(client, app):
    """When the rider has a custom plan it's used by default (merged + recomputed);
    ?view=base forces the base plan."""
    custom = {'id': 9, 'name': 'My SCR 100', 'base_plan_id': 7}
    custom_stops = [
        {'stop_order': 1, 'location': 'Start', 'stop_type': 'start', 'distance_miles': 0,
         'segment_time_min': 0, 'stop_duration_min': 0, 'elevation_gain': 0, 'seg_dist': 0,
         'ft_per_mi': 0, 'cum_time_min': 0, 'arrival_time_min': 0, 'time_bank_min': None,
         'stop_name': None, 'notes': None},
        {'stop_order': 2, 'location': 'Control 1', 'stop_type': 'control', 'distance_miles': 50,
         'segment_time_min': 170, 'stop_duration_min': 20, 'elevation_gain': 2000, 'seg_dist': 50,
         'ft_per_mi': 40, 'cum_time_min': 190, 'arrival_time_min': 170, 'time_bank_min': 130,
         'stop_name': 'My Lunch', 'notes': None, 'is_modified': True},
    ]
    # Default → custom
    with patch('routes.live.get_ride_by_id', return_value=_ride_pl()), \
         patch('models.get_ride_plan_by_slug', return_value=dict(_PLAN)), \
         patch('models.get_custom_plan', return_value=custom), \
         patch('services.custom_plan_service.get_merged_plan_stops', return_value=(custom_stops, custom)), \
         patch('services.custom_plan_service.recalculate_cumulative_values', return_value=custom_stops):
        resp = client.get('/api/ride/5/plan', headers=_bearer(app))
    data = resp.get_json()
    assert data['has_custom'] is True and data['using_custom'] is True
    assert data['custom_name'] == 'My SCR 100'
    assert data['stops'][1]['stop_name'] == 'My Lunch'
    assert data['stops'][1]['eta'] == '8:50 AM'   # 06:00 + arrival 170 min

    # ?view=base → base plan even though a custom exists
    with patch('routes.live.get_ride_by_id', return_value=_ride_pl()), \
         patch('models.get_ride_plan_by_slug', return_value=dict(_PLAN)), \
         patch('models.get_custom_plan', return_value=custom), \
         patch('models.get_ride_plan_stops', return_value=[dict(s) for s in _PLAN_STOPS]):
        resp = client.get('/api/ride/5/plan?view=base', headers=_bearer(app))
    data = resp.get_json()
    assert data['has_custom'] is True and data['using_custom'] is False
    assert data['stops'][1]['stop_name'] == 'Lunch'   # base stop name


# ── Web login session persistence (fixes frequent logouts) ─────────────────

def test_session_lifetime_is_30_days():
    """Web logins persist for 30 days (matches the native bearer token)."""
    from datetime import timedelta
    from config import Config
    assert Config.PERMANENT_SESSION_LIFETIME == timedelta(days=30)


def test_google_callback_makes_session_permanent(client):
    """The OAuth callback marks the session permanent so the cookie is a
    persistent 30-day cookie, not a transient one mobile browsers drop."""
    from unittest.mock import MagicMock
    import routes.auth as auth_routes
    user = {'id': 5, 'email': 'a@b.com', 'google_id': 'g123',
            'profile_completed': True, 'rider_id': None}
    mock_google = MagicMock()
    mock_google.authorize_access_token.return_value = {
        'userinfo': {'sub': 'g123', 'email': 'a@b.com'}}
    with patch.object(auth_routes.oauth, 'google', mock_google, create=True), \
         patch.object(auth_routes.models, 'get_user_by_google_id', return_value=user), \
         patch.object(auth_routes.models, 'update_user_login_time'), \
         patch.object(auth_routes.models, 'get_user_by_id', return_value=user):
        resp = client.get('/auth/google/callback')
    assert resp.status_code in (301, 302)
    with client.session_transaction() as sess:
        assert sess.get('user_id') == 5
        assert sess.permanent is True


# ── Email + password (3rd login option) ──────────────────────────────────
from werkzeug.security import generate_password_hash


def test_password_signup_creates_account_and_mints_token(client, app):
    created = {'id': 9, 'email': 'new@example.com', 'password_hash': 'x',
               'profile_completed': False, 'rider_id': None}
    with patch('models.get_user_by_email', return_value=None), \
         patch('models.create_user_password', return_value=created) as mock_create:
        resp = client.post('/api/auth/signup',
                           json={'email': 'New@Example.com', 'password': 'sup3rsecret'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['rider_id'] is None and data['profile_complete'] is False
    # Email is normalized to lowercase; the stored hash is NOT the plaintext.
    args = mock_create.call_args[0]
    assert args[0] == 'new@example.com'
    assert args[1] != 'sup3rsecret' and args[1]
    with app.app_context():
        assert auth_mod.load_mobile_token(data['token'])['user_id'] == 9


def test_password_signup_rejects_existing_email(client, app):
    existing = {'id': 3, 'email': 'taken@example.com', 'google_id': 'g1'}
    with patch('models.get_user_by_email', return_value=existing), \
         patch('models.create_user_password') as mock_create:
        resp = client.post('/api/auth/signup',
                           json={'email': 'taken@example.com', 'password': 'longenough1'})
    assert resp.status_code == 409          # no takeover of a Google/Apple account
    mock_create.assert_not_called()


def test_password_signup_validates_input(client, app):
    bad_email = client.post('/api/auth/signup',
                            json={'email': 'not-an-email', 'password': 'longenough1'})
    assert bad_email.status_code == 400
    short_pw = client.post('/api/auth/signup',
                           json={'email': 'a@b.com', 'password': 'short'})
    assert short_pw.status_code == 400


def test_password_login_succeeds_with_correct_password(client, app):
    user = {'id': 5, 'email': 'rider@example.com',
            'password_hash': generate_password_hash('correct-horse'),
            'profile_completed': True, 'rider_id': 7}
    with patch('models.get_user_by_email', return_value=user), \
         patch('models.update_user_login_time') as mock_touch, \
         patch('models.get_user_by_id', return_value=user):
        resp = client.post('/api/auth/login',
                           json={'email': 'Rider@Example.com', 'password': 'correct-horse'})
    assert resp.status_code == 200
    assert resp.get_json()['rider_id'] == 7
    mock_touch.assert_called_once_with(5)


def test_password_login_wrong_password_401(client, app):
    user = {'id': 5, 'email': 'rider@example.com',
            'password_hash': generate_password_hash('correct-horse'), 'rider_id': 7}
    with patch('models.get_user_by_email', return_value=user):
        resp = client.post('/api/auth/login',
                           json={'email': 'rider@example.com', 'password': 'wrong'})
    assert resp.status_code == 401


def test_password_login_unknown_email_401(client, app):
    with patch('models.get_user_by_email', return_value=None):
        resp = client.post('/api/auth/login',
                           json={'email': 'ghost@example.com', 'password': 'whatever12'})
    assert resp.status_code == 401


def test_password_login_google_only_account_401(client, app):
    # A Google/Apple account (no password_hash) can't be logged into by password.
    user = {'id': 3, 'email': 'g@example.com', 'google_id': 'g1', 'password_hash': None}
    with patch('models.get_user_by_email', return_value=user):
        resp = client.post('/api/auth/login',
                           json={'email': 'g@example.com', 'password': 'anything12'})
    assert resp.status_code == 401


def test_password_signup_rejects_overlong_password(client, app):
    # A multi-KB password must be rejected before hashing (scrypt-CPU DoS guard).
    resp = client.post('/api/auth/signup',
                       json={'email': 'a@b.com', 'password': 'x' * 5000})
    assert resp.status_code == 400


def test_password_login_rejects_overlong_password_without_hashing(client, app):
    import routes.api_auth as api_auth_mod
    with patch('models.get_user_by_email') as mock_get, \
         patch.object(api_auth_mod, 'check_password_hash', create=True) as mock_check:
        resp = client.post('/api/auth/login',
                           json={'email': 'a@b.com', 'password': 'x' * 5000})
    assert resp.status_code == 401
    mock_check.assert_not_called()   # never reached the hash check
    mock_get.assert_not_called()     # never even hit the DB


def test_password_signup_toctou_unique_violation_returns_409(client, app):
    import psycopg2
    with patch('models.get_user_by_email', return_value=None), \
         patch('models.create_user_password',
               side_effect=psycopg2.errors.UniqueViolation('dup')):
        resp = client.post('/api/auth/signup',
                           json={'email': 'race@example.com', 'password': 'longenough1'})
    assert resp.status_code == 409


# ── POST /api/auth/setup-profile (native RUSA onboarding) ──────────────────

_RUSA_OK = {'valid': True, 'first_name': 'Mihir', 'last_name': 'Sambhus',
            'rusa_name': 'Mihir Sambhus', 'rusa_club': 'Team Asha'}


def test_setup_profile_requires_auth(client):
    assert client.post('/api/auth/setup-profile', json={'rusa_id': 12345}).status_code == 401


def test_setup_profile_links_new_rider_and_mints_rider_token(client, app):
    """Creates the rider, links it to the SIGNED-IN account, and returns a token
    that now carries the rider_id (so live endpoints stop 403-ing)."""
    user = {'id': 5, 'email': 'r@example.com', 'profile_completed': False, 'rider_id': None}
    rider = {'id': 88, 'first_name': 'Mihir', 'last_name': 'Sambhus', 'rusa_id': 12345}
    with patch('models.get_user_by_id', return_value=user), \
         patch('models.get_rider_by_rusa_id', return_value=None), \
         patch('routes.api_auth.get_rusa_info', return_value=_RUSA_OK), \
         patch('models.get_rider_by_name_and_rusa', return_value=None), \
         patch('models.create_rider', return_value=rider) as mock_create, \
         patch('models.complete_user_profile', return_value=True) as mock_link:
        resp = client.post('/api/auth/setup-profile',
                           headers=_bearer(app, user_id=5, rider_id=None),
                           json={'rusa_id': 12345})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['rider_id'] == 88 and data['profile_complete'] is True
    assert data['rider_name'] == 'Mihir Sambhus'
    mock_create.assert_called_once_with('Mihir', 'Sambhus', 12345)
    mock_link.assert_called_once_with(5, 88)
    with app.app_context():
        assert auth_mod.load_mobile_token(data['token']) == {'user_id': 5, 'rider_id': 88}


def test_setup_profile_identity_from_token_not_body(client, app):
    """Security: the account linked is the token's user_id — a body-supplied
    user_id can't retarget another account."""
    user = {'id': 5, 'email': 'r@example.com', 'profile_completed': False, 'rider_id': None}
    rider = {'id': 88, 'first_name': 'Mihir', 'last_name': 'Sambhus', 'rusa_id': 12345}
    with patch('models.get_user_by_id', return_value=user) as mock_get, \
         patch('models.get_rider_by_rusa_id', return_value=None), \
         patch('routes.api_auth.get_rusa_info', return_value=_RUSA_OK), \
         patch('models.get_rider_by_name_and_rusa', return_value=None), \
         patch('models.create_rider', return_value=rider), \
         patch('models.complete_user_profile', return_value=True) as mock_link:
        client.post('/api/auth/setup-profile',
                    headers=_bearer(app, user_id=5, rider_id=None),
                    json={'rusa_id': 12345, 'user_id': 999})
    mock_get.assert_called_once_with(5)          # token identity, not body
    mock_link.assert_called_once_with(5, 88)


def test_setup_profile_idempotent_when_already_linked(client, app):
    user = {'id': 5, 'email': 'r@example.com', 'profile_completed': True, 'rider_id': 7}
    with patch('models.get_user_by_id', return_value=user), \
         patch('routes.api_auth.get_rusa_info') as mock_rusa, \
         patch('models.complete_user_profile') as mock_link:
        resp = client.post('/api/auth/setup-profile',
                           headers=_bearer(app, user_id=5, rider_id=7),
                           json={'rusa_id': 12345})
    assert resp.status_code == 200
    assert resp.get_json()['rider_id'] == 7
    mock_rusa.assert_not_called()                # no RUSA hit; already set up
    mock_link.assert_not_called()


def test_setup_profile_rejects_rusa_claimed_by_another(client, app):
    user = {'id': 5, 'profile_completed': False, 'rider_id': None}
    with patch('models.get_user_by_id', return_value=user), \
         patch('models.get_rider_by_rusa_id', return_value={'id': 88}), \
         patch('models.is_rider_linked_to_user', return_value=True), \
         patch('models.complete_user_profile') as mock_link:
        resp = client.post('/api/auth/setup-profile',
                           headers=_bearer(app, user_id=5, rider_id=None),
                           json={'rusa_id': 12345})
    assert resp.status_code == 409
    mock_link.assert_not_called()


def test_setup_profile_invalid_rusa_id_400(client, app):
    user = {'id': 5, 'profile_completed': False, 'rider_id': None}
    with patch('models.get_user_by_id', return_value=user):
        resp = client.post('/api/auth/setup-profile',
                           headers=_bearer(app, user_id=5, rider_id=None),
                           json={'rusa_id': 'not-a-number'})
    assert resp.status_code == 400


def test_setup_profile_rusa_not_found_404(client, app):
    user = {'id': 5, 'profile_completed': False, 'rider_id': None}
    with patch('models.get_user_by_id', return_value=user), \
         patch('models.get_rider_by_rusa_id', return_value=None), \
         patch('routes.api_auth.get_rusa_info',
               return_value={'valid': False, 'error': 'RUSA ID not found on rusa.org'}):
        resp = client.post('/api/auth/setup-profile',
                           headers=_bearer(app, user_id=5, rider_id=None),
                           json={'rusa_id': 999999})
    assert resp.status_code == 404


def test_setup_profile_non_positive_rusa_id_400(client, app):
    user = {'id': 5, 'profile_completed': False, 'rider_id': None}
    with patch('models.get_user_by_id', return_value=user):
        resp = client.post('/api/auth/setup-profile',
                           headers=_bearer(app, user_id=5, rider_id=None),
                           json={'rusa_id': 0})
    assert resp.status_code == 400


def test_setup_profile_link_failure_500(client, app):
    """A DB failure to link (complete_user_profile → False) returns 500, not a
    success, and never mints a rider token."""
    user = {'id': 5, 'profile_completed': False, 'rider_id': None}
    rider = {'id': 88, 'first_name': 'Mihir', 'last_name': 'Sambhus', 'rusa_id': 12345}
    with patch('models.get_user_by_id', return_value=user), \
         patch('models.get_rider_by_rusa_id', return_value=None), \
         patch('routes.api_auth.get_rusa_info', return_value=_RUSA_OK), \
         patch('models.get_rider_by_name_and_rusa', return_value=None), \
         patch('models.create_rider', return_value=rider), \
         patch('models.complete_user_profile', return_value=False):
        resp = client.post('/api/auth/setup-profile',
                           headers=_bearer(app, user_id=5, rider_id=None),
                           json={'rusa_id': 12345})
    assert resp.status_code == 500
    assert 'token' not in resp.get_json()
