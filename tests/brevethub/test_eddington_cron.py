"""BrevetHub Eddington refresh cron — /cron/refresh-eddington.

Compute is OFF the request path so a PUBLIC profile is a pure cache read. These
tests pin the contract:
  - auth: Bearer CRON_SECRET required; missing/wrong → 401; secret unset → 500,
  - fail-soft per rider: one rider Strava error does NOT abort the batch — the run
    still processes the rest and reports {refreshed, failed, considered},
  - the GET verb works (Vercel cron issues a GET),
  - the PINNED route: the production URL is exactly `/cron/refresh-eddington`.

All compute is mocked (no real Strava / DB). `time.sleep` is patched so the inter
-rider backoff does not slow the suite. Follows the monkeypatch-models pattern.
"""
from unittest.mock import patch

_SECRET = 'test-cron-secret-value'
_PATH = '/cron/refresh-eddington'

_CONNECTIONS = [
    {'rider_id': 1, 'access_token': 'A1', 'refresh_token': 'R1', 'expires_at': 9e9},
    {'rider_id': 2, 'access_token': 'A2', 'refresh_token': 'R2', 'expires_at': 9e9},
]


def _auth(secret=_SECRET):
    return {'Authorization': f'Bearer {secret}'}


def _with_secret(app):
    app.config['CRON_SECRET'] = _SECRET


# --------------------------------------------------------------------------- #
# Auth ladder
# --------------------------------------------------------------------------- #
def test_refresh_requires_auth(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_strava_connections_for_eddington') as mock_load:
        resp = client.post(_PATH)  # no Authorization header
    assert resp.status_code == 401
    mock_load.assert_not_called()


def test_refresh_rejects_wrong_secret(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_strava_connections_for_eddington') as mock_load:
        resp = client.post(_PATH, headers=_auth('wrong-secret'))
    assert resp.status_code == 401
    mock_load.assert_not_called()


def test_refresh_500_when_secret_unset(app, client):
    app.config['CRON_SECRET'] = None
    with patch('brevethub.models.get_strava_connections_for_eddington') as mock_load:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 500
    mock_load.assert_not_called()


# --------------------------------------------------------------------------- #
# Refresh behavior
# --------------------------------------------------------------------------- #
def test_refresh_computes_every_connected_rider(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_strava_connections_for_eddington',
               return_value=_CONNECTIONS), \
         patch('brevethub.routes.cron.compute_and_cache_eddington') as mock_compute, \
         patch('brevethub.routes.cron.time.sleep'):
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {'ok': True, 'refreshed': 2, 'failed': 0, 'considered': 2}
    assert mock_compute.call_count == 2


def test_refresh_fail_soft_one_rider_error_does_not_abort_batch(app, client):
    """The first rider raises a Strava error; the batch still processes the second
    and reports refreshed=1, failed=1, considered=2 (never a 500)."""
    _with_secret(app)

    def _compute(rider_id, connection, **kwargs):
        if rider_id == 1:
            raise Exception('strava rate limit')
        return {'eddington_km': 10, 'eddington_miles': 6}

    with patch('brevethub.models.get_strava_connections_for_eddington',
               return_value=_CONNECTIONS), \
         patch('brevethub.routes.cron.compute_and_cache_eddington',
               side_effect=_compute) as mock_compute, \
         patch('brevethub.routes.cron.time.sleep'):
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'refreshed': 1, 'failed': 1, 'considered': 2}
    assert mock_compute.call_count == 2  # the second rider was still attempted


def test_refresh_get_verb_works(app, client):
    """Vercel cron issues a GET — the endpoint must accept it."""
    _with_secret(app)
    with patch('brevethub.models.get_strava_connections_for_eddington',
               return_value=_CONNECTIONS), \
         patch('brevethub.routes.cron.compute_and_cache_eddington'), \
         patch('brevethub.routes.cron.time.sleep'):
        resp = client.get(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()['considered'] == 2


def test_refresh_no_connections_is_a_noop(app, client):
    _with_secret(app)
    with patch('brevethub.models.get_strava_connections_for_eddington', return_value=[]), \
         patch('brevethub.routes.cron.compute_and_cache_eddington') as mock_compute:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'refreshed': 0, 'failed': 0, 'considered': 0}
    mock_compute.assert_not_called()


def test_refresh_connection_load_failure_no_500(app, client):
    """A connection-load failure is caught → non-500 JSON body."""
    _with_secret(app)
    with patch('brevethub.models.get_strava_connections_for_eddington',
               side_effect=OSError('db down')), \
         patch('brevethub.routes.cron.compute_and_cache_eddington') as mock_compute:
        resp = client.post(_PATH, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is False
    mock_compute.assert_not_called()


# --------------------------------------------------------------------------- #
# Route path regression guard (pinned — the double-prefix / missing-prefix bug)
# --------------------------------------------------------------------------- #
def test_composed_route_is_exactly_cron_refresh_eddington(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert '/cron/refresh-eddington' in rules
    assert '/cron/cron/refresh-eddington' not in rules
    assert '/refresh-eddington' not in rules
