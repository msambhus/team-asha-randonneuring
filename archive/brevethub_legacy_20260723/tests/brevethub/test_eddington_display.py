"""Eddington display on the own profile and the same-club public rider profile.

The crux privacy invariant: a PUBLIC rider profile viewer holds no token for the
viewed rider, so that render must read ONLY the cached scalar and issue ZERO Strava
calls. These tests assert the public render never touches the Strava fetch/compute
path, shows the cached miles value (or an honest dash), and that the own profile shows
the value card when computed and a graceful prompt (never a fabricated 0) when not.

Everything is mocked (no real DB / network), per the BrevetHub test convention.
"""
from datetime import datetime, timezone
from unittest.mock import patch

_UTC = timezone.utc
_MADE = datetime(2024, 3, 1, tzinfo=_UTC)

# A club-A viewer and a same-club target with a cached Eddington of 42 km / 26 mi.
_VIEWER = {'id': 1, 'email': 'alice@ex.com', 'google_id': 'g-alice', 'rusa_id': '100',
           'club_id': 1, 'profile_completed': True, 'created_at': _MADE,
           'rusa_id_duplicate': False, 'eddington_km': 55, 'eddington_miles': 34,
           'eddington_calculated_at': _MADE}
_TARGET = {'id': 2, 'email': 'bob@ex.com', 'rusa_id': '200', 'club_id': 1,
           'profile_completed': True, 'created_at': _MADE, 'rusa_cache': [],
           'eddington_km': 42, 'eddington_miles': 26}
_TARGET_NO_EDD = {**_TARGET, 'id': 3, 'email': 'carol@ex.com',
                  'eddington_km': None, 'eddington_miles': None}


def _login(client, rider_id):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def _no_strava():
    """Patch every owner-context Strava entry point and assert-none-called later —
    proving the public render issues zero Strava work."""
    return (
        patch('brevethub.routes.strava.fetch_activities'),
        patch('brevethub.routes.strava.compute_and_cache_eddington'),
    )


# --------------------------------------------------------------------------- #
# Public rider profile — cached read, ZERO Strava calls
# --------------------------------------------------------------------------- #
def test_public_profile_shows_cached_value_with_zero_strava_fetch(client):
    _login(client, _VIEWER['id'])
    p_fetch, p_compute = _no_strava()
    with patch('brevethub.models.get_rider_by_id', return_value=_VIEWER), \
         patch('brevethub.models.get_club_rider', return_value=_TARGET), \
         p_fetch as mock_fetch, p_compute as mock_compute:
        resp = client.get('/riders/2')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '26' in body               # miles value (eddington_miles), not km
    assert 'Eddington' in body
    # The whole point: a public viewer never fetches the viewed rider's Strava data.
    mock_fetch.assert_not_called()
    mock_compute.assert_not_called()


def test_public_profile_absent_value_shows_dash_not_zero(client):
    _login(client, _VIEWER['id'])
    p_fetch, p_compute = _no_strava()
    with patch('brevethub.models.get_rider_by_id', return_value=_VIEWER), \
         patch('brevethub.models.get_club_rider', return_value=_TARGET_NO_EDD), \
         p_fetch as mock_fetch, p_compute as mock_compute:
        resp = client.get('/riders/3')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The Eddington card specifically renders an em dash, never a fabricated 0.
    assert '<div class="number">—</div><div class="label">Eddington (mi)</div>' in body
    mock_fetch.assert_not_called()
    mock_compute.assert_not_called()


# --------------------------------------------------------------------------- #
# Own profile — value card vs graceful prompt
# --------------------------------------------------------------------------- #
def _own_profile(client, rider):
    _login(client, rider['id'])
    with patch('brevethub.models.get_rider_by_id', return_value=rider), \
         patch('brevethub.models.get_club', return_value={'id': 1, 'name': 'Club Alpha'}), \
         patch('brevethub.models.get_strava_connection', return_value=None), \
         patch('brevethub.models.get_rider_rusa_cache',
               return_value={'rusa_cache': None, 'rusa_fetched_at': None}):
        return client.get('/profile')


def test_own_profile_shows_eddington_card_when_computed(client):
    resp = _own_profile(client, _VIEWER)  # eddington_miles = 34
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '34' in body               # eddington_miles (34), not km (55)
    assert 'Eddington (mi)' in body


def test_own_profile_unconnected_shows_prompt_not_zero(client):
    rider = {**_VIEWER, 'eddington_km': None, 'eddington_miles': None,
             'eddington_calculated_at': None, 'rusa_id': None}
    resp = _own_profile(client, rider)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Connect Strava to see your Eddington number.' in body
    # No fabricated Eddington stat card when there is no value.
    assert 'Eddington (mi)' not in body
