"""Tests for public per-ride live-map invite codes: code format, validation
normalization, member code creation, and guest (unauthenticated) access to the
ride map + positions API. All DB/model calls patched — no database needed.
"""
import re
from unittest.mock import patch

import models


def _login(client, rider_id=7):
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = rider_id


def _guest(client, code='ABCD-2K9P', ride_id=5):
    with client.session_transaction() as s:
        s['live_guest'] = {'code': code, 'ride_id': ride_id}


# ── code generation + validation ──────────────────────────────────────────

def test_generated_code_is_typeable_and_unambiguous():
    for _ in range(50):
        code = models._generate_invite_code()
        assert re.fullmatch(r'[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}', code)
        assert not set(code) & set('IO01')      # no ambiguous characters


def test_get_valid_ride_invite_normalizes_input():
    captured = {}

    class _Cur:
        def fetchone(self_):
            return {'code': 'ABCD-2K9P', 'ride_id': 5, 'expires_at': None}

    def fake_execute(sql, params=None):
        captured['params'] = params
        return _Cur()

    with patch('models._execute', side_effect=fake_execute):
        out = models.get_valid_ride_invite('  abcd-2k9p ')
    assert out['ride_id'] == 5
    assert captured['params'] == ('ABCD-2K9P',)      # upper + trimmed


def test_get_valid_ride_invite_none_for_blank():
    assert models.get_valid_ride_invite('') is None
    assert models.get_valid_ride_invite(None) is None


# ── member creates an invite code ──────────────────────────────────────────

def test_member_creates_invite_code(client):
    _login(client)
    ride = {'id': 5, 'name': 'SCR 200', 'date': '2026-07-04'}
    with patch('routes.live.get_ride_by_id', return_value=ride), \
         patch('routes.live.get_or_create_ride_invite', return_value='ABCD-2K9P') as mk:
        resp = client.post('/ride/5/live/invite')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['code'] == 'ABCD-2K9P'
    assert data['join_url'].endswith('/live/join')
    # expires ~24h after the ride day (ride date + 2 days at midnight UTC)
    assert mk.call_args.args[0] == 5
    assert '2026-07-06' in mk.call_args.args[2].isoformat()


def test_invite_requires_login(client):
    resp = client.post('/ride/5/live/invite')
    assert resp.status_code in (301, 302)            # redirected to login


# ── guest join flow ────────────────────────────────────────────────────────

def test_join_with_valid_code_grants_and_redirects(client):
    inv = {'code': 'ABCD-2K9P', 'ride_id': 5, 'expires_at': None}
    with patch('routes.live.get_valid_ride_invite', return_value=inv):
        resp = client.post('/live/join', data={'code': 'abcd-2k9p'})
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/ride/5/live')
    with client.session_transaction() as s:
        assert s['live_guest'] == {'code': 'ABCD-2K9P', 'ride_id': 5}
        assert s.permanent is True       # persistent cookie — no frequent re-entry


def test_join_with_bad_code_reprompts(client):
    with patch('routes.live.get_valid_ride_invite', return_value=None):
        resp = client.post('/live/join', data={'code': 'NOPE-0000'})
    assert resp.status_code == 200
    assert b'invalid or has expired' in resp.data
    with client.session_transaction() as s:
        assert 'live_guest' not in s


def test_guest_can_view_ride_map(client):
    _guest(client, ride_id=5)
    inv = {'code': 'ABCD-2K9P', 'ride_id': 5, 'expires_at': None}
    ride = {'id': 5, 'name': 'SCR 200', 'date': '2026-07-04'}
    with patch('routes.live.get_valid_ride_invite', return_value=inv), \
         patch('routes.live.get_ride_by_id', return_value=ride), \
         patch('routes.live._build_route_polyline', return_value=[]):
        resp = client.get('/ride/5/live')
    assert resp.status_code == 200
    assert b'guest' in resp.data.lower()             # guest note shown
    # member-only controls hidden (the Garmin link form)
    assert b'Garmin LiveTrack link for this ride' not in resp.data


def test_anonymous_without_code_is_sent_to_join(client):
    resp = client.get('/ride/5/live')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/live/join')


# ── guest access to the positions API is scoped to the granted ride ────────

def test_guest_positions_allowed_for_granted_ride(client):
    _guest(client, ride_id=5)
    inv = {'code': 'ABCD-2K9P', 'ride_id': 5, 'expires_at': None}
    with patch('routes.live.get_valid_ride_invite', return_value=inv), \
         patch('routes.live.get_latest_positions_for_ride', return_value=[]):
        resp = client.get('/api/live/positions?ride_id=5')
    assert resp.status_code == 200
    assert resp.get_json()['positions'] == []


def test_guest_positions_denied_for_other_ride(client):
    _guest(client, ride_id=5)
    inv = {'code': 'ABCD-2K9P', 'ride_id': 5, 'expires_at': None}
    with patch('routes.live.get_valid_ride_invite', return_value=inv):
        resp = client.get('/api/live/positions?ride_id=6')      # not the granted ride
    assert resp.status_code == 401


def test_positions_denied_without_auth_or_code(client):
    resp = client.get('/api/live/positions?ride_id=5')
    assert resp.status_code == 401
