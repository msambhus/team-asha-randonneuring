"""Tests for public per-ride live-map invite codes: code format, validation
normalization, member code creation, and guest (unauthenticated) access to the
ride map + positions API. All DB/model calls patched — no database needed.
"""
import re
from datetime import datetime, timezone
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

def test_member_creates_invite_code_expires_after_cutoff(client):
    """A 600k (40h limit) starting 06:00 Pacific: the code expires 48h after the
    ride's cutoff. start = 06-27 13:00 UTC; cutoff = +40h = 06-29 05:00 UTC;
    expiry = +48h = 07-01 05:00 UTC — NOT a ride-day UTC midnight. The join_url
    embeds the code for one-click sharing."""
    _login(client)
    ride = {'id': 5, 'name': 'Surf City 600k', 'date': '2026-06-27',
            'start_time': '06:00', 'time_limit_hours': 40, 'distance_km': 600}
    with patch('routes.live.get_ride_by_id', return_value=ride), \
         patch('routes.live.get_or_create_ride_invite', return_value='ABCD-2K9P') as mk:
        resp = client.post('/ride/5/live/invite')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['code'] == 'ABCD-2K9P'
    assert 'code=ABCD-2K9P' in data['join_url'] and '/live/join' in data['join_url']
    assert mk.call_args.args[0] == 5
    assert mk.call_args.args[2] == datetime(2026, 7, 1, 5, 0, tzinfo=timezone.utc)


def test_member_creates_invite_code_uses_default_limit(client):
    """No explicit time limit → ACP default for the distance (200k → 13.5h):
    start 07-04 13:00 UTC + 13.5h + 48h = 07-07 02:30 UTC."""
    _login(client)
    ride = {'id': 5, 'name': 'SCR 200', 'date': '2026-07-04',
            'start_time': '06:00', 'distance_km': 200}
    with patch('routes.live.get_ride_by_id', return_value=ride), \
         patch('routes.live.get_or_create_ride_invite', return_value='ABCD-2K9P') as mk:
        resp = client.post('/ride/5/live/invite')
    assert resp.status_code == 200
    assert mk.call_args.args[2] == datetime(2026, 7, 7, 2, 30, tzinfo=timezone.utc)


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


def test_join_via_link_code_one_click(client):
    """A shared link /live/join?code=... grants access and redirects — no typing."""
    inv = {'code': 'ABCD-2K9P', 'ride_id': 5, 'expires_at': None}
    with patch('routes.live.get_valid_ride_invite', return_value=inv):
        resp = client.get('/live/join?code=abcd-2k9p')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/ride/5/live')
    with client.session_transaction() as s:
        assert s['live_guest'] == {'code': 'ABCD-2K9P', 'ride_id': 5}
        assert s.permanent is True


def test_member_join_via_link_opens_the_ride_not_the_hub(client):
    """A logged-in member clicking a shared link lands on the ride it points at
    (TA-229) — previously they were bounced to the hub and the code was dropped."""
    with client.session_transaction() as s:
        s['rider_id'] = 7
    inv = {'code': 'ABCD-2K9P', 'ride_id': 5, 'expires_at': None}
    with patch('routes.live.get_valid_ride_invite', return_value=inv):
        resp = client.get('/live/join?code=abcd-2k9p')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/ride/5/live')   # the ride, not /live/hub
    with client.session_transaction() as s:
        assert 'live_guest' not in s          # members don't need the guest grant


def test_member_join_no_code_goes_to_hub(client):
    with client.session_transaction() as s:
        s['rider_id'] = 7
    resp = client.get('/live/join')
    assert resp.status_code == 302 and '/live' in resp.headers['Location']


def test_join_via_link_bad_code_shows_form(client):
    """A link with an expired/invalid code shows the form (prefilled), no grant."""
    with patch('routes.live.get_valid_ride_invite', return_value=None):
        resp = client.get('/live/join?code=NOPE-0000')
    assert resp.status_code == 200
    assert b'invalid or has expired' in resp.data
    assert b'NOPE-0000' in resp.data            # prefilled for a retry
    with client.session_transaction() as s:
        assert 'live_guest' not in s


def test_join_plain_get_shows_form(client):
    resp = client.get('/live/join')
    assert resp.status_code == 200
    assert b'Invite code' in resp.data


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
         patch('routes.live._radial_track', return_value=[]):
        resp = client.get('/ride/5/live')
    assert resp.status_code == 200
    assert b'SCR 200' in resp.data                   # ride loads for the guest
    # member-only controls hidden (the Garmin link form)
    assert b'Garmin LiveTrack link for this ride' not in resp.data
    # The guest sees the SHARED Radial partial polling the public roster (no PII).
    assert b'radial-live' in resp.data
    assert b'/ride/5/live/roster.json' in resp.data


def test_anonymous_without_code_is_sent_to_join(client):
    resp = client.get('/ride/5/live')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/live/join')


# ── guest access to the positions API is scoped to the granted ride ────────

def test_guest_positions_allowed_for_granted_ride(client):
    _guest(client, ride_id=5)
    inv = {'code': 'ABCD-2K9P', 'ride_id': 5, 'expires_at': None}
    # The context is now built even with no active sharers, so stub it (no RWGPS/
    # weather calls) — this test only exercises invite scoping + the empty roster.
    with patch('routes.live.get_valid_ride_invite', return_value=inv), \
         patch('routes.live._ride_live_context', return_value={'has_route': False}), \
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
