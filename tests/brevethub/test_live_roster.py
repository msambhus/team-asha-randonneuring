"""Public, PII-safe Radial roster poll (GET /live/<id>/roster.json).

BrevetHub pattern: monkeypatch brevethub.models.*, use the `client` fixture, never a
real DB, mock every RWGPS HTTP call. The privacy contract is the crux: the public
roster is guest-reachable and carries a display_name + position + stats + an opaque
key — and NEVER rider_id / email / google_id. The two-tier contract is asserted
side by side: the same ride's authenticated live-positions.json STILL carries
rider_id (the mobile contract), while roster.json does not.
"""
from datetime import datetime, timezone
from unittest.mock import patch

_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}

_PUBLIC_RIDE = {'id': 1, 'rider_id': 99, 'is_public': True, 'name': 'Coastal 200',
                'distance_km': 200, 'start_at': None, 'rwgps_url': None, 'club_id': 3}
_PRIVATE_OWNED = {'id': 1, 'rider_id': 7, 'is_public': False, 'name': 'Private',
                  'distance_km': 200, 'start_at': None, 'rwgps_url': None, 'club_id': 3}
_PRIVATE_OTHER = {'id': 1, 'rider_id': 99, 'is_public': False, 'name': 'Private',
                  'distance_km': 200, 'start_at': None, 'rwgps_url': None, 'club_id': 3}


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def _pos(rider_id, name, display_name=None, **tel):
    # `name` = the authenticated member-tier name (display_name-or-email-local-part);
    # `display_name` = the raw column the PUBLIC roster uses. Default display_name to
    # `name` so a friendly-named fixture reads friendly in the public output.
    row = {'rider_id': rider_id, 'name': name,
           'display_name': name if display_name is None else display_name,
           'lat': 37.5, 'lng': -122.3, 'recorded_at': datetime.now(timezone.utc),
           'speed': 5.0, 'heart_rate': None, 'power': None, 'cadence': None,
           'source': 'garmin'}
    row.update(tel)
    return row


def _no_plan_ctx(**extra):
    """Patches that make _ride_live_context resolve no route + no plan (base telemetry)."""
    p = {
        'brevethub.models.get_live_positions_rp': None,
        'brevethub.models.get_rider_position_history_rp': [],
        'brevethub.models.get_brevet_route_plan_by_route_id_rp': None,
        'brevethub.models.get_brevet_route_plan_candidates_rp': [],
    }
    p.update(extra)
    return p


# --------------------------------------------------------------------------- #
# Guest access + privacy shape.
# --------------------------------------------------------------------------- #
def test_guest_can_read_public_roster_200(client):
    rows = [_pos(7, 'alice')]
    with patch('brevethub.models.get_ride', return_value=_PUBLIC_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=rows), \
         patch('brevethub.models.get_rider_position_history_rp', return_value=[]), \
         patch('brevethub.models.get_brevet_route_plan_by_route_id_rp', return_value=None), \
         patch('brevethub.models.get_brevet_route_plan_candidates_rp', return_value=[]):
        resp = client.get('/live/1/roster.json')   # no login → guest
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ride_id'] == 1
    assert len(data['roster']) == 1
    assert data['roster'][0]['display_name'] == 'alice'
    assert 'server_time' in data and 'poll_seconds' in data


def test_public_roster_has_no_pii_identifiers(client):
    rows = [_pos(7, 'alice'), _pos(8, 'bob')]
    with patch('brevethub.models.get_ride', return_value=_PUBLIC_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=rows), \
         patch('brevethub.models.get_rider_position_history_rp', return_value=[]), \
         patch('brevethub.models.get_brevet_route_plan_by_route_id_rp', return_value=None), \
         patch('brevethub.models.get_brevet_route_plan_candidates_rp', return_value=[]):
        resp = client.get('/live/1/roster.json')
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    # No PII key anywhere in the serialized payload.
    for leaked in ('rider_id', 'email', 'google_id', 'r@example.com'):
        assert leaked not in body
    for row in resp.get_json()['roster']:
        assert 'key' in row and len(row['key']) == 12
        for leaked in ('rider_id', 'email', 'google_id'):
            assert leaked not in row


def test_null_display_name_falls_back_to_rider_never_email(client):
    """A rider with no display_name set: the PUBLIC roster shows the neutral 'Rider'
    token, NEVER the email local-part that the authenticated `name` field falls back
    to. Guards the two-tier name contract at the endpoint boundary."""
    # display_name NULL; `name` carries the email local-part (member-tier fallback).
    rows = [_pos(7, 'aliceonymous', display_name='')]
    with patch('brevethub.models.get_ride', return_value=_PUBLIC_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=rows), \
         patch('brevethub.models.get_rider_position_history_rp', return_value=[]), \
         patch('brevethub.models.get_brevet_route_plan_by_route_id_rp', return_value=None), \
         patch('brevethub.models.get_brevet_route_plan_candidates_rp', return_value=[]):
        resp = client.get('/live/1/roster.json')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert resp.get_json()['roster'][0]['display_name'] == 'Rider'
    assert 'aliceonymous' not in body        # the email local-part never leaks


def test_public_roster_uses_opted_in_query_only(client):
    """The roster is built from get_live_positions_rp (opted-in + attached only), so
    a non-opted-in rider (absent from that query) never appears."""
    rows = [_pos(7, 'alice')]
    with patch('brevethub.models.get_ride', return_value=_PUBLIC_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=rows) as opted, \
         patch('brevethub.models.get_ride_positions') as anon, \
         patch('brevethub.models.get_rider_position_history_rp', return_value=[]), \
         patch('brevethub.models.get_brevet_route_plan_by_route_id_rp', return_value=None), \
         patch('brevethub.models.get_brevet_route_plan_candidates_rp', return_value=[]):
        resp = client.get('/live/1/roster.json')
    assert resp.status_code == 200
    opted.assert_called_once()
    anon.assert_not_called()   # never the anonymous lat/lng-only trail
    assert [r['display_name'] for r in resp.get_json()['roster']] == ['alice']


# --------------------------------------------------------------------------- #
# Access gate.
# --------------------------------------------------------------------------- #
def test_private_ride_guest_404s(client):
    with patch('brevethub.models.get_ride', return_value=_PRIVATE_OTHER):
        resp = client.get('/live/1/roster.json')   # guest, private ride
    assert resp.status_code == 404


def test_private_ride_owner_can_preview_200(client):
    _login(client, rider_id=7)
    rows = [_pos(7, 'alice')]
    with patch('brevethub.models.get_ride', return_value=_PRIVATE_OWNED), \
         patch('brevethub.models.get_live_positions_rp', return_value=rows), \
         patch('brevethub.models.get_rider_position_history_rp', return_value=[]), \
         patch('brevethub.models.get_brevet_route_plan_by_route_id_rp', return_value=None), \
         patch('brevethub.models.get_brevet_route_plan_candidates_rp', return_value=[]):
        resp = client.get('/live/1/roster.json')
    assert resp.status_code == 200


def test_unknown_ride_404s(client):
    with patch('brevethub.models.get_ride', return_value=None):
        resp = client.get('/live/1/roster.json')
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Two-tier contract: the authenticated member poll STILL carries rider_id.
# --------------------------------------------------------------------------- #
def test_authenticated_positions_still_carry_rider_id(client):
    """The member/mobile /live/<id>/live-positions.json contract is unchanged — it
    keeps rider_id behind auth, while the public roster.json drops it."""
    _login(client, rider_id=7)
    rows = [_pos(7, 'alice')]
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_PUBLIC_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=rows), \
         patch('brevethub.models.get_rider_position_history_rp', return_value=[]), \
         patch('brevethub.models.get_brevet_route_plan_by_route_id_rp', return_value=None), \
         patch('brevethub.models.get_brevet_route_plan_candidates_rp', return_value=[]):
        member = client.get('/live/1/live-positions.json')
        public = client.get('/live/1/roster.json')
    assert member.status_code == 200 and public.status_code == 200
    assert member.get_json()['positions'][0]['rider_id'] == 7      # auth tier keeps it
    assert 'rider_id' not in public.get_data(as_text=True)          # public tier drops it
