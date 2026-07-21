"""Owner-context Eddington compute: the shared helper + the on-connect hook.

All Strava HTTP is mocked and every model write is monkeypatched (no real network
or DB, per the BrevetHub test convention). These pin:
  - compute_and_cache_eddington transforms raw activities (raw ``type`` -> the
    engine ``activity_type`` key) and persists the expected E in BOTH miles and km,
  - a raw-``type`` activity list does NOT silently yield E=0 (the transform ran),
  - a successful Strava connect invokes the compute once,
  - a compute error on connect still flashes connect-SUCCESS (connect never blocked).
"""
from unittest.mock import patch

from brevethub.routes import strava

# 40 distinct days of 45 km rides (45000 m). Through the shared engine that is
# E(km) = 40 and E(miles) = 27 (45 km ~= 27.96 mi, so day 28 needs >= 28 mi and
# falls short). The two differing values prove both units are computed.
_RAW_ACTIVITIES = (
    [{'id': 1000 + d, 'distance': 45000, 'type': 'Ride',
      'start_date': f'2025-06-{d:02d}T08:00:00Z'} for d in range(1, 15)]
    + [{'id': 2000 + d, 'distance': 45000, 'type': 'Ride',
        'start_date': f'2025-07-{d:02d}T08:00:00Z'} for d in range(1, 27)]
)
_EXPECTED_KM = 40
_EXPECTED_MILES = 27

_CONNECTION = {'rider_id': 7, 'access_token': 'live-token',
               'refresh_token': 'R', 'expires_at': 9_999_999_999}

_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': None,
          'rusa_id_duplicate': False}

_TOKENS = {'athlete': {'id': 555}, 'access_token': 'A', 'refresh_token': 'R',
           'expires_at': 1999999999}


def test_compute_transforms_and_persists_both_units(app):
    """The helper fetches the rider history, transforms raw -> engine shape, and
    persists the expected E in miles + km."""
    with app.app_context(), \
         patch('brevethub.routes.strava._valid_access_token', return_value='live-token'), \
         patch('brevethub.routes.strava.fetch_activities', return_value=_RAW_ACTIVITIES), \
         patch('brevethub.models.set_rider_eddington') as mock_set:
        result = strava.compute_and_cache_eddington(7, _CONNECTION)

    assert result == {'eddington_km': _EXPECTED_KM, 'eddington_miles': _EXPECTED_MILES}
    mock_set.assert_called_once()
    assert mock_set.call_args.kwargs['eddington_km'] == _EXPECTED_KM
    assert mock_set.call_args.kwargs['eddington_miles'] == _EXPECTED_MILES


def test_raw_type_list_does_not_yield_zero(app):
    """Guard the transform contract: raw Strava activities carry ``type``, not
    ``activity_type``; if the helper skipped the transform, E would be 0. A non-zero
    persisted E proves the transform ran."""
    with app.app_context(), \
         patch('brevethub.routes.strava._valid_access_token', return_value='live-token'), \
         patch('brevethub.routes.strava.fetch_activities', return_value=_RAW_ACTIVITIES), \
         patch('brevethub.models.set_rider_eddington') as mock_set:
        result = strava.compute_and_cache_eddington(7, _CONNECTION)

    assert result['eddington_km'] > 0
    assert mock_set.call_args.kwargs['eddington_km'] > 0


def _flashes(client):
    with client.session_transaction() as sess:
        return [msg for _cat, msg in sess.get('_flashes', [])]


def test_connect_callback_computes_once(client):
    client.application.config['STRAVA_CLIENT_SECRET'] = 'test-secret'
    with client.session_transaction() as sess:
        sess['strava_oauth_state'] = 'good'
        sess['strava_connecting_rider_id'] = 7
    with patch('brevethub.routes.strava.exchange_code_for_token', return_value=_TOKENS), \
         patch('brevethub.models.upsert_strava_connection'), \
         patch('brevethub.models.get_strava_connection', return_value=_CONNECTION), \
         patch('brevethub.routes.strava.compute_and_cache_eddington') as mock_compute:
        resp = client.get('/strava/callback?code=abc&state=good&scope=activity:read_all')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/dashboard')
    mock_compute.assert_called_once()
    assert mock_compute.call_args[0][0] == 7  # rider_id


def test_connect_callback_compute_error_still_flashes_success(client):
    """A compute failure must NOT block a successful connect: success is flashed,
    the failure flash is not, and no 500 escapes."""
    client.application.config['STRAVA_CLIENT_SECRET'] = 'test-secret'
    with client.session_transaction() as sess:
        sess['strava_oauth_state'] = 'good'
        sess['strava_connecting_rider_id'] = 7
    with patch('brevethub.routes.strava.exchange_code_for_token', return_value=_TOKENS), \
         patch('brevethub.models.upsert_strava_connection') as mock_upsert, \
         patch('brevethub.models.get_strava_connection', return_value=_CONNECTION), \
         patch('brevethub.routes.strava.compute_and_cache_eddington',
               side_effect=Exception('strava boom')):
        resp = client.get('/strava/callback?code=abc&state=good')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/dashboard')
    mock_upsert.assert_called_once()  # the connect itself succeeded
    flashes = _flashes(client)
    assert 'Strava connected!' in flashes
    assert 'Failed to connect Strava. Please try again.' not in flashes
