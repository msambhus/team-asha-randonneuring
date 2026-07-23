"""Owner-context Eddington compute: the shared helper + the on-connect hook.

All Strava HTTP is mocked and every model write is monkeypatched (no real network
or DB, per the BrevetHub test convention). These pin:
  - compute_and_cache_eddington transforms raw activities (raw ``type`` -> the
    engine ``activity_type`` key) and persists the expected E in BOTH miles and km,
  - a raw-``type`` activity list does NOT silently yield E=0 (the transform ran),
  - a successful Strava connect does NOT compute synchronously (the all-time history
    fetch is deferred to the daily cron, off the request path), yet still succeeds.
"""
import time
from datetime import datetime, timezone
from unittest.mock import patch

from brevethub import models
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


# --------------------------------------------------------------------------- #
# expires_at type contract: the cron loads connections via
# get_strava_connections_for_eddington, which returns expires_at as an epoch FLOAT
# (identical to get_strava_connection). _valid_access_token does a NUMERIC epoch
# compare (expires_at > time.time() + 60), so an epoch float is exactly what it
# expects — a bare datetime would TypeError. These exercise the REAL token helper
# (NOT patched) to prove the float connection flows through end-to-end.
# --------------------------------------------------------------------------- #
def test_connections_loader_returns_epoch_float_expires_at():
    """get_strava_connections_for_eddington converts the TIMESTAMPTZ expires_at to an
    epoch float (parity with get_strava_connection) so the token helper can compare
    it numerically. db.query is patched, so no real DB is touched."""
    dt = datetime(2030, 1, 1, tzinfo=timezone.utc)
    fake_rows = [{'id': 1, 'rider_id': 7, 'strava_athlete_id': 555,
                  'access_token': 'A', 'refresh_token': 'R', 'expires_at': dt,
                  'scope': 'read', 'stats_cache': None, 'stats_fetched_at': None,
                  'created_at': dt}]
    with patch('brevethub.models.db.query', return_value=fake_rows):
        conns = models.get_strava_connections_for_eddington()
    assert len(conns) == 1
    assert isinstance(conns[0]['expires_at'], float)
    assert conns[0]['expires_at'] == dt.timestamp()
    assert conns[0]['stats_fetched_at'] is None


def test_compute_flows_through_real_valid_access_token(app):
    """Regression: compute_and_cache_eddington must accept a loader-shaped connection
    (epoch-float expires_at) through the REAL _valid_access_token — no TypeError. A
    future (valid) epoch means the stored token is used directly, with no refresh."""
    conn = {'rider_id': 7, 'access_token': 'live-token', 'refresh_token': 'R',
            'expires_at': time.time() + 3600}  # epoch float, comfortably in the future
    with app.app_context(), \
         patch('brevethub.routes.strava.fetch_activities', return_value=_RAW_ACTIVITIES), \
         patch('brevethub.routes.strava.refresh_access_token') as mock_refresh, \
         patch('brevethub.models.set_rider_eddington') as mock_set:
        result = strava.compute_and_cache_eddington(7, conn)
    mock_refresh.assert_not_called()  # valid future epoch → stored token used, no datetime compare
    assert result == {'eddington_km': _EXPECTED_KM, 'eddington_miles': _EXPECTED_MILES}
    mock_set.assert_called_once()


def _flashes(client):
    with client.session_transaction() as sess:
        return [msg for _cat, msg in sess.get('_flashes', [])]


def test_connect_callback_defers_eddington_to_cron(client):
    """A successful connect persists the token and flashes success, but does NOT
    compute Eddington on the request path: the all-time history fetch is deferred to
    the daily /cron/refresh-eddington (an unbounded fetch on the connect redirect can
    exceed the serverless timeout for an active rider). The profile shows the
    "will appear after the next sync" note until the cron fills it in."""
    client.application.config['STRAVA_CLIENT_SECRET'] = 'test-secret'
    with client.session_transaction() as sess:
        sess['strava_oauth_state'] = 'good'
        sess['strava_connecting_rider_id'] = 7
    with patch('brevethub.routes.strava.exchange_code_for_token', return_value=_TOKENS), \
         patch('brevethub.models.upsert_strava_connection') as mock_upsert, \
         patch('brevethub.routes.strava.compute_and_cache_eddington') as mock_compute:
        resp = client.get('/strava/callback?code=abc&state=good&scope=activity:read_all')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/dashboard')
    mock_upsert.assert_called_once()          # the connect itself succeeded
    mock_compute.assert_not_called()          # compute is deferred to the cron
    flashes = _flashes(client)
    assert 'Strava connected!' in flashes
    assert 'Failed to connect Strava. Please try again.' not in flashes
