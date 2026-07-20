"""Auto-attach (Mission 3, Feature 2) — cold-start ride resolution for the beacon.

The beacon resolves its target ride through a ladder (explicit ride_id → active
ride → cold-start resolver), and EVERY resolved ride is re-gated through the
public-OR-owned accessibility check before a point is stored. These tests drive
the route wiring; the resolver's own ordering (SQL) is covered by the model
docstring contract. Models are monkeypatched — no real DB.
"""
from unittest.mock import patch

_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}

_OWN_RIDE = {'id': 12, 'rider_id': 7, 'is_public': False, 'name': 'My 300',
             'distance_km': 300, 'start_at': None, 'rwgps_url': None}
_PUBLIC_OTHER = {'id': 12, 'rider_id': 99, 'is_public': True, 'name': 'Public 300',
                 'distance_km': 300, 'start_at': None, 'rwgps_url': None}
_PRIVATE_OTHER = {'id': 12, 'rider_id': 99, 'is_public': False, 'name': 'Private',
                  'distance_km': 300, 'start_at': None, 'rwgps_url': None}

# No explicit ride_id and no active ride → the cold-start resolver runs.
_NO_ACTIVE = {'rider_id': 7, 'enabled': True, 'garmin_session_url': None,
              'garmin_session_token': None, 'active_ride_id': None, 'updated_at': None}


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def _beacon(client):
    return client.post('/api/live/beacon', json={'lat': 37.5, 'lng': -122.3})


def test_cold_start_attaches_to_resolver_pick(client):
    """No explicit/active ride → the deterministic resolver pick (an owned ride) is
    re-gated, persisted to active_ride_id, and the fix is stored on it."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=_NO_ACTIVE), \
         patch('brevethub.models.get_auto_attach_ride_rp', return_value=_OWN_RIDE), \
         patch('brevethub.models.get_ride', return_value=_OWN_RIDE), \
         patch('brevethub.models.set_active_ride_rp') as setr, \
         patch('brevethub.models.insert_live_position_rp', return_value=True) as ins:
        resp = _beacon(client)
    assert resp.status_code == 200
    assert resp.get_json()['ride_id'] == 12
    setr.assert_called_once_with(7, 12)
    assert ins.call_args.kwargs['ride_id'] == 12


def test_cold_start_picks_public_ride_rider_streams_to(client):
    """The resolver may return a PUBLIC ride the rider already streams to; it passes
    the re-gate (public) and is attached."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=_NO_ACTIVE), \
         patch('brevethub.models.get_auto_attach_ride_rp', return_value=_PUBLIC_OTHER), \
         patch('brevethub.models.get_ride', return_value=_PUBLIC_OTHER), \
         patch('brevethub.models.set_active_ride_rp') as setr, \
         patch('brevethub.models.insert_live_position_rp', return_value=True) as ins:
        resp = _beacon(client)
    assert resp.status_code == 200
    setr.assert_called_once_with(7, 12)
    assert ins.call_args.kwargs['ride_id'] == 12


def test_cold_start_refuses_insert_when_attach_persist_fails(client):
    """A resolver pick is not enough: active_ride_id must persist before the point
    is stored, or the member map would not show the rider after a 200."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=_NO_ACTIVE), \
         patch('brevethub.models.get_auto_attach_ride_rp', return_value=_OWN_RIDE), \
         patch('brevethub.models.get_ride', return_value=_OWN_RIDE), \
         patch('brevethub.models.set_active_ride_rp', return_value=False) as setr, \
         patch('brevethub.models.insert_live_position_rp') as ins:
        resp = _beacon(client)
    assert resp.status_code == 500
    setr.assert_called_once_with(7, 12)
    ins.assert_not_called()


def test_cold_start_regate_refuses_inaccessible_pick(client):
    """Defense in depth: if the resolver ever returned an inaccessible ride (private,
    not owned), the re-gate refuses it — never attach, never store — and 400s."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=_NO_ACTIVE), \
         patch('brevethub.models.get_auto_attach_ride_rp', return_value=_PRIVATE_OTHER), \
         patch('brevethub.models.get_ride', return_value=_PRIVATE_OTHER), \
         patch('brevethub.models.set_active_ride_rp') as setr, \
         patch('brevethub.models.insert_live_position_rp') as ins:
        resp = _beacon(client)
    assert resp.status_code == 400
    setr.assert_not_called()
    ins.assert_not_called()


def test_cold_start_not_run_when_active_ride_present(client):
    """An accessible active ride short-circuits the ladder — the cold-start resolver
    is never consulted."""
    _login(client)
    tracking = dict(_NO_ACTIVE, active_ride_id=12)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_live_tracking_rp', return_value=tracking), \
         patch('brevethub.models.get_ride', return_value=_OWN_RIDE), \
         patch('brevethub.models.get_auto_attach_ride_rp') as resolver, \
         patch('brevethub.models.set_active_ride_rp') as setr, \
         patch('brevethub.models.insert_live_position_rp', return_value=True) as ins:
        resp = _beacon(client)
    assert resp.status_code == 200
    resolver.assert_not_called()             # ladder stopped at the active ride
    setr.assert_not_called()                 # already attached — no re-write
    assert ins.call_args.kwargs['ride_id'] == 12
