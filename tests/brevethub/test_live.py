"""Public/guest live-ride browse + owner-only position ingestion.

Follows the established BrevetHub test pattern: monkeypatch `brevethub.models.*`,
use the `client` fixture, never touch a real DB. The security-critical contracts
are first-class:
  - the public list/map/poll surfaces expose NO rider PII (no email/rider_id),
  - a private or unknown ride 404s for a guest (map AND poll),
  - position POST is owner-scoped: 401 anon, 404 unknown, 403 non-owner, 400 bad
    coords, and only a valid owner insert reaches the DB.
"""
import os
import re
from datetime import datetime, timezone
from unittest.mock import patch

_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_PATH = os.path.join(REPO_ROOT, 'brevethub', 'models.py')


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


# --------------------------------------------------------------------------- #
# Guest browse — public list
# --------------------------------------------------------------------------- #
def test_public_list_renders_rides_without_pii(client):
    rides = [
        {'id': 1, 'name': 'SFR Point Reyes 200k', 'distance_km': 200,
         'start_at': datetime(2026, 7, 20, 6, 0), 'status': 'going',
         'club_name': 'San Francisco Randonneurs'},
    ]
    with patch('brevethub.models.get_public_rides', return_value=rides):
        resp = client.get('/live')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'SFR Point Reyes 200k' in body
    assert 'San Francisco Randonneurs' in body
    # No account needed and no rider identity is exposed.
    assert 'r@example.com' not in body
    assert 'email' not in body.lower()


def test_public_list_query_filters_to_is_public_only(client):
    """The model itself must filter is_public = TRUE — verified statically since
    the mocked route can't exercise the SQL (no DB in unit tests)."""
    with open(MODELS_PATH, 'r', encoding='utf-8') as fh:
        src = fh.read()
    # Both guest-facing selectors gate on is_public = TRUE.
    list_body = re.search(r'def get_public_rides\(.*?\n(?=def )', src, re.DOTALL).group(0)
    one_body = re.search(r'def get_public_ride\(.*?\n(?=def )', src, re.DOTALL).group(0)
    assert re.search(r'is_public\s*=\s*TRUE', list_body)
    assert re.search(r'is_public\s*=\s*TRUE', one_body)


# --------------------------------------------------------------------------- #
# Guest browse — per-ride map
# --------------------------------------------------------------------------- #
def test_public_map_renders_for_public_ride(client):
    ride = {'id': 1, 'name': 'Public Night 300', 'distance_km': 300,
            'start_at': datetime(2026, 7, 20, 6, 0), 'status': 'going',
            'club_name': 'Seattle International Randonneurs'}
    with patch('brevethub.models.get_public_ride', return_value=ride):
        resp = client.get('/live/1')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Public Night 300' in body
    assert '/live/1/positions.json' in body  # the poll URL is wired


def test_public_map_404_for_private_ride(client):
    """A private (is_public=FALSE) ride resolves to None → 404 for a guest."""
    with patch('brevethub.models.get_public_ride', return_value=None):
        resp = client.get('/live/1')
    assert resp.status_code == 404


def test_public_map_404_for_unknown_ride(client):
    with patch('brevethub.models.get_public_ride', return_value=None):
        resp = client.get('/live/999999')
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Guest browse — positions poll (no PII)
# --------------------------------------------------------------------------- #
def test_positions_json_exposes_only_latlng_recorded_at(client):
    ride = {'id': 1, 'name': 'X', 'club_name': 'C'}
    rows = [
        {'lat': 37.77, 'lng': -122.41, 'recorded_at': datetime(2026, 7, 20, 6, 5, tzinfo=timezone.utc)},
        {'lat': 37.80, 'lng': -122.45, 'recorded_at': datetime(2026, 7, 20, 6, 10, tzinfo=timezone.utc)},
    ]
    with patch('brevethub.models.get_public_ride', return_value=ride), \
         patch('brevethub.models.get_ride_positions', return_value=rows):
        resp = client.get('/live/1/positions.json')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data['positions']) == 2
    for p in data['positions']:
        assert set(p.keys()) == {'lat', 'lng', 'recorded_at'}  # NO rider_id / email
    body = resp.get_data(as_text=True)
    assert 'email' not in body
    assert 'rider_id' not in body


def test_positions_json_404_for_private_or_unknown_ride(client):
    with patch('brevethub.models.get_public_ride', return_value=None), \
         patch('brevethub.models.get_ride_positions') as mock_positions:
        resp = client.get('/live/1/positions.json')
    assert resp.status_code == 404
    mock_positions.assert_not_called()  # never even reads positions for a private ride


# --------------------------------------------------------------------------- #
# Position ingestion — owner-only POST
# --------------------------------------------------------------------------- #
def test_position_post_by_owner_inserts(client):
    _login(client, rider_id=7)
    with patch('brevethub.models.get_ride', return_value={'id': 1, 'rider_id': 7}), \
         patch('brevethub.models.insert_position') as mock_insert:
        resp = client.post('/api/rides/1/position', json={'lat': 37.77, 'lng': -122.41})
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True
    mock_insert.assert_called_once()
    args, kwargs = mock_insert.call_args
    assert args[0] == 1 and args[1] == 7 and args[2] == 37.77 and args[3] == -122.41


def test_position_post_by_non_owner_forbidden(client):
    _login(client, rider_id=7)
    with patch('brevethub.models.get_ride', return_value={'id': 1, 'rider_id': 99}), \
         patch('brevethub.models.insert_position') as mock_insert:
        resp = client.post('/api/rides/1/position', json={'lat': 37.77, 'lng': -122.41})
    assert resp.status_code == 403
    mock_insert.assert_not_called()


def test_position_post_anonymous_unauthorized(client):
    with patch('brevethub.models.get_ride') as mock_get, \
         patch('brevethub.models.insert_position') as mock_insert:
        resp = client.post('/api/rides/1/position', json={'lat': 37.77, 'lng': -122.41})
    assert resp.status_code == 401
    mock_get.assert_not_called()      # auth is checked before any ride lookup
    mock_insert.assert_not_called()


def test_position_post_unknown_ride_not_found(client):
    _login(client, rider_id=7)
    with patch('brevethub.models.get_ride', return_value=None), \
         patch('brevethub.models.insert_position') as mock_insert:
        resp = client.post('/api/rides/123/position', json={'lat': 1.0, 'lng': 2.0})
    assert resp.status_code == 404
    mock_insert.assert_not_called()


def test_position_post_out_of_range_coords_rejected(client):
    _login(client, rider_id=7)
    with patch('brevethub.models.get_ride', return_value={'id': 1, 'rider_id': 7}), \
         patch('brevethub.models.insert_position') as mock_insert:
        resp = client.post('/api/rides/1/position', json={'lat': 200.0, 'lng': -122.41})
    assert resp.status_code == 400
    mock_insert.assert_not_called()


def test_position_post_missing_coords_rejected(client):
    _login(client, rider_id=7)
    with patch('brevethub.models.get_ride', return_value={'id': 1, 'rider_id': 7}), \
         patch('brevethub.models.insert_position') as mock_insert:
        resp = client.post('/api/rides/1/position', json={'lat': 37.77})
    assert resp.status_code == 400
    mock_insert.assert_not_called()


def test_position_post_bad_recorded_at_rejected(client):
    _login(client, rider_id=7)
    with patch('brevethub.models.get_ride', return_value={'id': 1, 'rider_id': 7}), \
         patch('brevethub.models.insert_position') as mock_insert:
        resp = client.post('/api/rides/1/position',
                           json={'lat': 37.77, 'lng': -122.41, 'recorded_at': 'not-a-date'})
    assert resp.status_code == 400
    mock_insert.assert_not_called()


def test_position_post_valid_recorded_at_passes_through(client):
    _login(client, rider_id=7)
    with patch('brevethub.models.get_ride', return_value={'id': 1, 'rider_id': 7}), \
         patch('brevethub.models.insert_position') as mock_insert:
        resp = client.post('/api/rides/1/position',
                           json={'lat': 37.77, 'lng': -122.41,
                                 'recorded_at': '2026-07-20T06:05:00+00:00'})
    assert resp.status_code == 200
    assert mock_insert.call_args.kwargs['recorded_at'] == '2026-07-20T06:05:00+00:00'


# --------------------------------------------------------------------------- #
# Rider-facing — create / flag public
# --------------------------------------------------------------------------- #
def test_create_ride_public_redirects_to_shareable_map(client):
    _login(client, rider_id=7)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.create_ride', return_value=55) as mock_create:
        resp = client.post('/live/new', data={'name': 'My Live 200', 'distance_km': '200',
                                              'is_public': 'on'})
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/live/55')
    mock_create.assert_called_once()
    assert mock_create.call_args.args[0] == 7                    # owned by the session rider
    assert mock_create.call_args.kwargs['is_public'] is True
    assert mock_create.call_args.kwargs['name'] == 'My Live 200'


def test_create_ride_requires_name(client):
    _login(client, rider_id=7)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.create_ride') as mock_create:
        resp = client.post('/live/new', data={'name': '   '})
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/live/new')
    mock_create.assert_not_called()


def test_new_page_lists_rider_rides_with_share_links(client):
    _login(client, rider_id=7)
    rides = [{'id': 55, 'name': 'My Live 200', 'distance_km': 200,
              'start_at': datetime(2026, 7, 20, 6, 0), 'status': 'going', 'is_public': True}]
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_rider_rides', return_value=rides):
        resp = client.get('/live/new')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'My Live 200' in body
    assert '/live/55' in body  # shareable public URL shown


def test_set_public_owner_scoped(client):
    _login(client, rider_id=7)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.set_ride_public', return_value={'id': 55}) as mock_set:
        resp = client.post('/live/55/public', data={'is_public': 'on'})
    assert resp.status_code == 302
    mock_set.assert_called_once_with(55, 7, True)


def test_set_public_non_owner_is_noop(client):
    _login(client, rider_id=7)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.set_ride_public', return_value=None) as mock_set:
        resp = client.post('/live/55/public', data={'is_public': 'on'})
    assert resp.status_code == 302
    mock_set.assert_called_once_with(55, 7, True)  # owner-scoped UPDATE matched nothing


def test_live_routes_require_login_for_rider_surfaces(client):
    """The create/flag surfaces are profile-gated; an anon user is bounced to login."""
    with patch('brevethub.models.get_rider_by_id', return_value=None):
        resp = client.get('/live/new')
    assert resp.status_code == 302
    assert '/auth/login' in resp.headers['Location']


# --------------------------------------------------------------------------- #
# Dashboard — the "Coming soon" item is replaced with the live entry
# --------------------------------------------------------------------------- #
def test_dashboard_replaces_coming_soon_with_live_entry(client):
    _login(client, rider_id=7)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club', return_value={'id': 3, 'name': 'SFR'}), \
         patch('brevethub.models.get_strava_connection', return_value=None):
        resp = client.get('/dashboard')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Coming soon: Public live-ride tracking' not in body
    assert 'Public live-ride tracking' not in body
    assert '/live' in body  # links into the live browse
