"""Cookie-free demo bearer mint (Mission 3, Feature 4 — review follow-on).

A native client (or Apple App Review) has no web session cookie, so /api/auth/demo
mints a Bearer token for a fixed DEMO_RIDER_ID — but ONLY when DEMO_MODE_ENABLED is
set (else it 404s). This is the one cookie-free sign-in path; full email/password +
email-OTP native sign-in is a documented follow-on. Models are monkeypatched.
"""
from unittest.mock import patch

_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}
_PUBLIC_RIDE = {'id': 1, 'rider_id': 99, 'is_public': True, 'name': 'Public 200',
                'distance_km': 200, 'start_at': None, 'rwgps_url': None}


def test_demo_disabled_404(app, client):
    app.config['DEMO_MODE_ENABLED'] = False
    resp = client.post('/api/auth/demo')
    assert resp.status_code == 404


def test_demo_enabled_mints_token_without_cookie(app, client):
    app.config['DEMO_MODE_ENABLED'] = True
    app.config['DEMO_RIDER_ID'] = '7'
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER):
        resp = client.post('/api/auth/demo')          # no session cookie
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['token'] and data['rider_id'] == 7 and data['profile_complete'] is True
    # The minted token authenticates the bearer API with no cookie.
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_PUBLIC_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=[]), \
         patch('brevethub.routes.live._resolve_ride_plan', return_value=None):
        poll = client.get('/api/live/positions?ride_id=1',
                          headers={'Authorization': 'Bearer ' + data['token']})
    assert poll.status_code == 200


def test_demo_enabled_but_unconfigured_rider_503(app, client):
    app.config['DEMO_MODE_ENABLED'] = True
    app.config['DEMO_RIDER_ID'] = None
    resp = client.post('/api/auth/demo')
    assert resp.status_code == 503


def test_demo_enabled_missing_rider_503(app, client):
    app.config['DEMO_MODE_ENABLED'] = True
    app.config['DEMO_RIDER_ID'] = '7'
    with patch('brevethub.models.get_rider_by_id', return_value=None):
        resp = client.post('/api/auth/demo')
    assert resp.status_code == 503
