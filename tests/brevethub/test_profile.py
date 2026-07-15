"""/profile route — mocked models, no real DB / network.

Covers the three data profiles the mission requires (RUSA history, Strava, and
neither) and the login guard. Career/SR/R-12 assertions use date-independent
values (totals, SR-season list, streak length) so the test does not depend on the
wall clock; the exact Nov 1 boundary + streak-activity behaviour is proven
deterministically in test_seasons.py.
"""
import time
from datetime import datetime, timezone
from unittest.mock import patch

_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': '12345', 'club_id': 1,
          'rusa_id_duplicate': False, 'created_at': datetime(2024, 3, 1, tzinfo=timezone.utc)}
_RIDER_NO_RUSA = {**_RIDER, 'rusa_id': None}

# An SR season (2024-2025) so career['sr_seasons'] is a fixed, clock-independent value.
_CACHE_SR = [
    {'date': '2024-11-15', 'distance_km': 200, 'finish_time': '13:30', 'route_name': 'Fall 200'},
    {'date': '2025-03-01', 'distance_km': 300, 'finish_time': '19:45', 'route_name': 'Spring 300'},
    {'date': '2025-04-01', 'distance_km': 400, 'finish_time': '26:00', 'route_name': 'Spring 400'},
    {'date': '2025-06-01', 'distance_km': 600, 'finish_time': '38:00', 'route_name': 'Summer 600'},
]


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def test_profile_renders_with_rusa_history(client):
    """RUSA cache present → identity + career summary render, no 500, no scrape."""
    _login(client)
    fresh = datetime.now(timezone.utc)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club', return_value={'id': 1, 'name': 'SF Rando'}), \
         patch('brevethub.models.get_strava_connection', return_value=None), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': _CACHE_SR, 'rusa_fetched_at': fresh}), \
         patch('brevethub.routes.main.fetch_rider_results') as mock_scrape:
        resp = client.get('/profile')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'r@example.com' in body
    assert 'SF Rando' in body
    assert '1500 km' in body            # career total of the four brevets
    assert '(4 brevets)' in body
    assert '2024-2025' in body          # the SR season
    assert 'R-12 streak' in body
    assert 'March 2024' in body         # member-since from created_at
    mock_scrape.assert_not_called()


def test_profile_renders_with_strava_only(client):
    """A Strava-connected rider with no RUSA ID → Strava summary shows, career
    section prompts for a RUSA ID, and the page does not 500."""
    _login(client)
    connection = {
        'strava_athlete_id': 999, 'access_token': 'tok', 'refresh_token': 'ref',
        'expires_at': time.time() + 3600, 'stats_fetched_at': time.time(),
        'stats_cache': {'rides': 12, 'distance_km': 480, 'elevation_m': 3200,
                        'moving_hours': 24, 'fitness': 72},
    }
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER_NO_RUSA), \
         patch('brevethub.models.get_club', return_value=None), \
         patch('brevethub.models.get_strava_connection', return_value=connection):
        resp = client.get('/profile')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '72/100' in body             # fitness score from the cached summary
    assert '480 km' in body
    assert 'Add a RUSA ID' in body      # career section graceful w/o RUSA


def test_profile_renders_with_neither(client):
    """No RUSA ID and no Strava → graceful prompts, never a 500."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER_NO_RUSA), \
         patch('brevethub.models.get_club', return_value=None), \
         patch('brevethub.models.get_strava_connection', return_value=None):
        resp = client.get('/profile')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Add a RUSA ID' in body
    assert 'Connect Strava' in body


def test_profile_requires_login(client):
    """Unauthenticated GET redirects to login — the structural PII guard (there is
    no rider-id parameter, so a rider can only ever load their own row)."""
    resp = client.get('/profile')
    assert resp.status_code in (301, 302)
    assert '/auth/login' in resp.headers['Location'] or '/login' in resp.headers['Location']
