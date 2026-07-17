"""BrevetHub club-admin route — owner gating + fail-soft generation.

Follows the BrevetHub test pattern: monkeypatch brevethub.models.*, use the client
fixture, never touch a real DB or network (RWGPS is mocked). Contracts:
  - the console + generate are OWNER-gated: a signed-in non-owner gets 403, the
    club owner gets 200 (console) / a redirect (generate),
  - generation FAILS SOFT: a missing RWGPS credential (fetch_route raises) flashes
    and redirects — never 500,
  - a successful generate persists via the model and redirects to the plan page.
"""
from unittest.mock import patch


_RIDER = {'id': 7, 'email': 'rider@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}
_OWNED_CLUB = {'id': 3, 'name': 'San Francisco Randonneurs', 'city': 'SF',
               'state': 'CA', 'rusa_club_id': 'SFR', 'owner_rider_id': 7}
_EVENT = {'id': 11, 'name': 'Point Reyes 200', 'date': '2026-08-15',
          'distance_km': 200, 'region': 'CA', 'rwgps_url':
          'https://ridewithgps.com/routes/123', 'time_limit_hours': 13.5}
_BUILT = {'plan': {'name': 'Point Reyes 200', 'slug': 'point-reyes-200'},
          'stops': [{'stop_order': 1, 'location': 'Start', 'stop_type': 'start'}]}


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


# --------------------------------------------------------------------------- #
# Console — owner gate
# --------------------------------------------------------------------------- #
def test_console_403_for_non_owner(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club_owned_by_rider', return_value=None):
        resp = client.get('/admin/plan')
    assert resp.status_code == 403


def test_console_200_for_owner(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club_owned_by_rider', return_value=_OWNED_CLUB), \
         patch('brevethub.models.get_upcoming_events', return_value=[_EVENT]):
        resp = client.get('/admin/plan')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'San Francisco Randonneurs' in body
    assert 'Point Reyes 200' in body


def test_console_redirects_anonymous(client):
    # No session rider -> login_required bounces (not a 200/403).
    resp = client.get('/admin/plan')
    assert resp.status_code in (301, 302)


# --------------------------------------------------------------------------- #
# Generate — owner gate + fail-soft
# --------------------------------------------------------------------------- #
def test_generate_403_for_non_owner(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club_owned_by_rider', return_value=None), \
         patch('brevethub.models.upsert_brevet_route_plan') as mock_up:
        resp = client.post('/admin/plan/generate', data={'event_id': '11'})
    assert resp.status_code == 403
    mock_up.assert_not_called()


def test_generate_missing_creds_fails_soft(client):
    """No RWGPS keys -> fetch_route raises -> flash + redirect, never 500."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club_owned_by_rider', return_value=_OWNED_CLUB), \
         patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.routes.admin.fetch_route',
               side_effect=Exception('RWGPS API credentials not configured')), \
         patch('brevethub.models.upsert_brevet_route_plan') as mock_up:
        resp = client.post('/admin/plan/generate', data={'event_id': '11'})
    assert resp.status_code == 302          # redirect back, NOT a 500
    mock_up.assert_not_called()


def test_generate_success_persists_and_redirects(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club_owned_by_rider', return_value=_OWNED_CLUB), \
         patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.routes.admin.fetch_route', return_value={'name': 'r'}), \
         patch('brevethub.routes.admin.extract_controls', return_value=[{'x': 1}]), \
         patch('brevethub.routes.admin.build_ride_plan', return_value=_BUILT), \
         patch('brevethub.models.upsert_brevet_route_plan', return_value=99) as mock_up:
        resp = client.post('/admin/plan/generate',
                           data={'event_id': '11',
                                 'rwgps_url': 'https://ridewithgps.com/routes/123'})
    assert resp.status_code == 302
    assert '/plan/11' in resp.headers['Location']
    mock_up.assert_called_once()
    # Persisted scoped to the OWNER's club.
    _args, kwargs = mock_up.call_args
    assert kwargs.get('club_id') == 3


def test_generate_bad_event_id_fails_soft(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club_owned_by_rider', return_value=_OWNED_CLUB), \
         patch('brevethub.models.upsert_brevet_route_plan') as mock_up:
        resp = client.post('/admin/plan/generate', data={'event_id': 'not-a-number'})
    assert resp.status_code == 302
    mock_up.assert_not_called()
