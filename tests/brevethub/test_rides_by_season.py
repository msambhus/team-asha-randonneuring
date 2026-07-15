"""/rides-by-season route — mocked models, no real DB / network.

Proves season grouping under the correct headings (current highlighted, past
collapsible), per-season totals, the merge of the rider's own rp_ride records into
the RUSA history, and the graceful empty states. Season names are derived from the
real "today" via shared.seasons so the test never hard-codes a clock-dependent
season.
"""
from datetime import date, datetime, timezone
from unittest.mock import patch

from brevethub.routes import main as main_routes
from shared import seasons

_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': '12345', 'club_id': None,
          'rusa_id_duplicate': False, 'created_at': datetime(2024, 3, 1, tzinfo=timezone.utc)}
_RIDER_NO_RUSA = {**_RIDER, 'rusa_id': None}

# The season "today" falls in, and a past season two years earlier — derived so the
# assertions hold whatever the wall clock is.
_TODAY = date.today()
_CURRENT = seasons.current_season_name(_TODAY)
_SY = int(_CURRENT.split('-')[0])
_PAST = f'{_SY - 3}-{_SY - 2}'


def _brevet(iso, dist, route='Route'):
    return {'date': iso, 'distance_km': dist, 'finish_time': '10:00', 'route_name': route}


# An SR set in the current season + one older brevet in a past season.
_CACHE = [
    _brevet(f'{_SY}-11-15', 200, 'Fall 200'),
    _brevet(f'{_SY + 1}-01-01', 300, 'Winter 300'),
    _brevet(f'{_SY + 1}-02-01', 400, 'Winter 400'),
    _brevet(f'{_SY + 1}-03-01', 600, 'Spring 600'),
    _brevet(f'{_SY - 2}-06-01', 200, 'Old 200'),
]


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def test_rides_by_season_groups_and_highlights_current(client):
    _login(client)
    fresh = datetime.now(timezone.utc)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': _CACHE, 'rusa_fetched_at': fresh}), \
         patch('brevethub.models.get_rider_rides', return_value=[]), \
         patch('brevethub.routes.main.fetch_rider_results') as mock_scrape:
        resp = client.get('/rides-by-season')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert _CURRENT in body                 # current season heading
    assert 'current season' in body         # highlight badge
    assert _PAST in body                     # past season present
    assert '<details' in body               # past seasons are collapsible
    assert '1500 km' in body                # current-season total (200+300+400+600)
    assert 'Fall 200' in body               # a listed ride
    mock_scrape.assert_not_called()


def test_rides_by_season_merges_own_finished_rides(client):
    """A rider's own FINISHED rp_ride merges into the season view with a 'logged'
    tag; a self-logged ride that duplicates a RUSA (date, distance) is deduped."""
    _login(client)
    fresh = datetime.now(timezone.utc)
    own = [
        {'id': 1, 'name': 'My Populaire', 'distance_km': 200,
         'start_at': datetime(_SY - 2, 7, 1, tzinfo=timezone.utc),
         'status': 'finished', 'is_public': False},
        # duplicate of the past-season RUSA 200 on the same day+distance → deduped
        {'id': 2, 'name': 'Dup 200', 'distance_km': 200,
         'start_at': datetime(_SY - 2, 6, 1, tzinfo=timezone.utc),
         'status': 'finished', 'is_public': False},
        # a non-finished ride is ignored
        {'id': 3, 'name': 'Planned 300', 'distance_km': 300,
         'start_at': datetime(_SY - 2, 8, 1, tzinfo=timezone.utc),
         'status': 'going', 'is_public': False},
    ]
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': _CACHE, 'rusa_fetched_at': fresh}), \
         patch('brevethub.models.get_rider_rides', return_value=own), \
         patch('brevethub.routes.main.fetch_rider_results'):
        resp = client.get('/rides-by-season')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'My Populaire' in body     # own finished ride merged in
    assert 'logged' in body           # tagged as a self-logged ride
    assert 'Dup 200' not in body      # deduped against the RUSA entry
    assert 'Planned 300' not in body  # non-finished ride excluded


def test_rides_by_season_empty_when_no_rusa_id(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER_NO_RUSA), \
         patch('brevethub.models.get_rider_rides', return_value=[]):
        resp = client.get('/rides-by-season')
    assert resp.status_code == 200
    assert 'Add a RUSA ID' in resp.get_data(as_text=True)


def test_rides_by_season_empty_cache_is_graceful(client):
    """A rider with a RUSA ID but an empty history (scrape returns nothing) →
    200 with a friendly empty state, never a 500."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': None, 'rusa_fetched_at': None}), \
         patch('brevethub.models.get_rider_rides', return_value=[]), \
         patch('brevethub.routes.main.fetch_rider_results', return_value=[]):
        resp = client.get('/rides-by-season')
    assert resp.status_code == 200
    assert 'No brevets to show yet' in resp.get_data(as_text=True)


def test_rides_by_season_requires_login(client):
    resp = client.get('/rides-by-season')
    assert resp.status_code in (301, 302)
    assert '/auth/login' in resp.headers['Location'] or '/login' in resp.headers['Location']


# --------------------------------------------------------------------------- #
# Merge helpers (unit-level, no HTTP)
# --------------------------------------------------------------------------- #
def test_finished_rides_as_brevets_filters_and_shapes():
    rides = [
        {'name': 'Done', 'distance_km': 200, 'status': 'finished',
         'start_at': datetime(2025, 6, 1, tzinfo=timezone.utc)},
        {'name': 'Going', 'distance_km': 200, 'status': 'going',
         'start_at': datetime(2025, 6, 1, tzinfo=timezone.utc)},
        {'name': 'NoDate', 'distance_km': 200, 'status': 'finished', 'start_at': None},
    ]
    out = main_routes._finished_rides_as_brevets(rides)
    assert len(out) == 1
    assert out[0]['date'] == '2025-06-01'
    assert out[0]['route_name'] == 'Done'
    assert out[0]['source'] == 'ride'


def test_merge_brevets_prefers_rusa_on_collision():
    rusa = [{'date': '2025-06-01', 'distance_km': 200, 'route_name': 'RUSA 200'}]
    own = [
        {'date': '2025-06-01', 'distance_km': 200, 'route_name': 'Dup', 'source': 'ride'},
        {'date': '2025-07-01', 'distance_km': 300, 'route_name': 'New', 'source': 'ride'},
    ]
    merged = main_routes._merge_brevets(rusa, own)
    assert len(merged) == 2
    names = [b['route_name'] for b in merged]
    assert 'RUSA 200' in names and 'New' in names and 'Dup' not in names
