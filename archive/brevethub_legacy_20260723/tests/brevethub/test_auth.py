"""Google OAuth callback — rp_rider upsert + new/returning routing.

Google's userinfo is mocked (no live Google session, no network). The callback
must: create a `rp_rider` on first sign-in and send the new rider to /signup;
send a returning rider with a completed profile straight to the dashboard; and
reject a callback that yields no usable account info.
"""
from unittest.mock import MagicMock, patch

import brevethub.routes.auth as auth_routes


def _mock_google(userinfo):
    """A stand-in for the Authlib Google client whose token carries userinfo."""
    google = MagicMock()
    google.authorize_access_token.return_value = {'userinfo': userinfo} if userinfo else {}
    return google


def test_callback_creates_rider_and_routes_new_to_signup(client):
    new_rider = {'id': 7, 'email': 'new@example.com', 'google_id': 'g-sub-1',
                 'profile_completed': False, 'rusa_id': None, 'club_id': None,
                 'rusa_id_duplicate': False}
    with patch.object(auth_routes.oauth, 'google',
                      _mock_google({'sub': 'g-sub-1', 'email': 'new@example.com'}),
                      create=True), \
         patch.object(auth_routes.models, 'get_rider_by_google_id', return_value=None), \
         patch.object(auth_routes.models, 'create_rider',
                      return_value=new_rider) as mock_create:
        resp = client.get('/auth/google/callback')
    assert resp.status_code in (301, 302)
    assert resp.headers['Location'].endswith('/signup/')
    mock_create.assert_called_once_with('new@example.com', 'g-sub-1')
    with client.session_transaction() as sess:
        assert sess['rider_id'] == 7
        assert sess['google_id'] == 'g-sub-1'
        assert sess.permanent is True


def test_callback_routes_returning_rider_to_dashboard(client):
    rider = {'id': 3, 'email': 'r@example.com', 'google_id': 'g-sub-9',
             'profile_completed': True, 'rusa_id': '12345', 'club_id': 2,
             'rusa_id_duplicate': False}
    with patch.object(auth_routes.oauth, 'google',
                      _mock_google({'sub': 'g-sub-9', 'email': 'r@example.com'}),
                      create=True), \
         patch.object(auth_routes.models, 'get_rider_by_google_id', return_value=rider), \
         patch.object(auth_routes.models, 'update_rider_login') as mock_touch, \
         patch.object(auth_routes.models, 'create_rider') as mock_create:
        resp = client.get('/auth/google/callback')
    assert resp.status_code in (301, 302)
    assert resp.headers['Location'].endswith('/dashboard')
    mock_touch.assert_called_once_with(3)
    mock_create.assert_not_called()
    with client.session_transaction() as sess:
        assert sess['rider_id'] == 3


def test_callback_without_userinfo_bounces_to_login(client):
    with patch.object(auth_routes.oauth, 'google', _mock_google(None), create=True), \
         patch.object(auth_routes.models, 'create_rider') as mock_create:
        resp = client.get('/auth/google/callback')
    assert resp.status_code in (301, 302)
    assert resp.headers['Location'].endswith('/auth/login')
    mock_create.assert_not_called()
    with client.session_transaction() as sess:
        assert 'rider_id' not in sess


def test_callback_oauth_failure_bounces_to_login(client):
    google = MagicMock()
    google.authorize_access_token.side_effect = Exception('token exchange failed')
    with patch.object(auth_routes.oauth, 'google', google, create=True):
        resp = client.get('/auth/google/callback')
    assert resp.status_code in (301, 302)
    assert resp.headers['Location'].endswith('/auth/login')


def test_logout_clears_session(client):
    with client.session_transaction() as sess:
        sess['rider_id'] = 5
    resp = client.get('/auth/logout')
    assert resp.status_code in (301, 302)
    with client.session_transaction() as sess:
        assert 'rider_id' not in sess
