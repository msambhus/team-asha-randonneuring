"""Strava OAuth connect/callback/disconnect + the CSRF `state` contract.

All Strava HTTP and every model write are mocked (no real network / DB). The
state cases are first-class: connect stores a `state` and sends it to Strava;
callback with no session state and with a mismatched state each HARD-reject
(no code exchange, no upsert); a matching state proceeds; and the session no
longer holds the flow keys after every terminal path.
"""
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': None,
          'rusa_id_duplicate': False}

_TOKENS = {'athlete': {'id': 555}, 'access_token': 'A', 'refresh_token': 'R',
           'expires_at': 1999999999}


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def _configure_strava(client):
    client.application.config['STRAVA_CLIENT_SECRET'] = 'test-secret'


# --------------------------------------------------------------------------- #
# connect
# --------------------------------------------------------------------------- #
def test_connect_redirects_to_strava_with_state(client):
    _login(client)
    _configure_strava(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=None):
        resp = client.get('/strava/connect')
    assert resp.status_code == 302
    loc = resp.headers['Location']
    assert loc.startswith('https://www.strava.com/oauth/authorize')
    qs = parse_qs(urlparse(loc).query)
    assert qs['client_id'] == ['113090']
    assert qs['scope'] == ['activity:read_all']
    assert 'strava/callback' in qs['redirect_uri'][0]
    state = qs['state'][0]
    assert state  # non-empty
    with client.session_transaction() as sess:
        assert sess['strava_oauth_state'] == state
        assert sess['strava_connecting_rider_id'] == 7


def test_connect_when_already_connected_redirects_dashboard(client):
    _login(client)
    _configure_strava(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection',
               return_value={'rider_id': 7, 'access_token': 'A'}):
        resp = client.get('/strava/connect')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/dashboard')


def test_connect_without_secret_flashes_and_returns(client):
    _login(client)
    client.application.config['STRAVA_CLIENT_SECRET'] = None
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=None):
        resp = client.get('/strava/connect')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/dashboard')


# --------------------------------------------------------------------------- #
# callback — CSRF state rejection (hard)
# --------------------------------------------------------------------------- #
def test_callback_no_session_state_hard_rejects(client):
    with patch('brevethub.routes.strava.exchange_code_for_token') as mock_exch, \
         patch('brevethub.models.upsert_strava_connection') as mock_upsert:
        resp = client.get('/strava/callback?code=abc&state=whatever')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/dashboard')
    mock_exch.assert_not_called()
    mock_upsert.assert_not_called()


def test_callback_mismatched_state_hard_rejects_and_clears(client):
    with client.session_transaction() as sess:
        sess['strava_oauth_state'] = 'real-state'
        sess['strava_connecting_rider_id'] = 7
    with patch('brevethub.routes.strava.exchange_code_for_token') as mock_exch, \
         patch('brevethub.models.upsert_strava_connection') as mock_upsert:
        resp = client.get('/strava/callback?code=abc&state=attacker-state')
    assert resp.status_code == 302
    mock_exch.assert_not_called()
    mock_upsert.assert_not_called()
    with client.session_transaction() as sess:
        assert 'strava_oauth_state' not in sess
        assert 'strava_connecting_rider_id' not in sess


# --------------------------------------------------------------------------- #
# callback — matching state
# --------------------------------------------------------------------------- #
def test_callback_matching_state_exchanges_and_upserts_epoch(client):
    _configure_strava(client)
    with client.session_transaction() as sess:
        sess['strava_oauth_state'] = 'good'
        sess['strava_connecting_rider_id'] = 7
    with patch('brevethub.routes.strava.exchange_code_for_token',
               return_value=_TOKENS) as mock_exch, \
         patch('brevethub.models.upsert_strava_connection') as mock_upsert:
        resp = client.get('/strava/callback?code=abc&state=good&scope=activity:read_all')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/dashboard')
    mock_exch.assert_called_once()
    mock_upsert.assert_called_once()
    # The raw epoch integer is passed straight through to the model upsert.
    assert mock_upsert.call_args.kwargs['expires_at'] == 1999999999
    assert mock_upsert.call_args.kwargs['strava_athlete_id'] == 555
    with client.session_transaction() as sess:
        assert 'strava_oauth_state' not in sess
        assert 'strava_connecting_rider_id' not in sess


def test_callback_denied_error_clears_session_no_exchange(client):
    with client.session_transaction() as sess:
        sess['strava_oauth_state'] = 'good'
        sess['strava_connecting_rider_id'] = 7
    with patch('brevethub.routes.strava.exchange_code_for_token') as mock_exch, \
         patch('brevethub.models.upsert_strava_connection') as mock_upsert:
        resp = client.get('/strava/callback?error=access_denied&state=good')
    assert resp.status_code == 302
    mock_exch.assert_not_called()
    mock_upsert.assert_not_called()
    with client.session_transaction() as sess:
        assert 'strava_oauth_state' not in sess


def test_callback_exchange_exception_does_not_500(client):
    _configure_strava(client)
    with client.session_transaction() as sess:
        sess['strava_oauth_state'] = 'good'
        sess['strava_connecting_rider_id'] = 7
    with patch('brevethub.routes.strava.exchange_code_for_token',
               side_effect=Exception('token boom')), \
         patch('brevethub.models.upsert_strava_connection') as mock_upsert:
        resp = client.get('/strava/callback?code=abc&state=good')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/dashboard')
    mock_upsert.assert_not_called()
    with client.session_transaction() as sess:
        assert 'strava_oauth_state' not in sess


# --------------------------------------------------------------------------- #
# disconnect
# --------------------------------------------------------------------------- #
def test_disconnect_deletes_and_deauthorizes(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection',
               return_value={'rider_id': 7, 'access_token': 'A'}), \
         patch('brevethub.routes.strava.deauthorize_strava') as mock_deauth, \
         patch('brevethub.models.delete_strava_connection') as mock_del:
        resp = client.post('/strava/disconnect')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/dashboard')
    mock_deauth.assert_called_once_with('A')
    mock_del.assert_called_once_with(7)


def test_disconnect_without_connection_is_noop(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_strava_connection', return_value=None), \
         patch('brevethub.models.delete_strava_connection') as mock_del:
        resp = client.post('/strava/disconnect')
    assert resp.status_code == 302
    mock_del.assert_not_called()


# --------------------------------------------------------------------------- #
# dashboard renders cached Strava stats
# --------------------------------------------------------------------------- #
def test_dashboard_renders_cached_strava_stats(client):
    """A connection with a fresh stats cache renders totals without a fetch."""
    import time
    _login(client)
    connection = {
        'rider_id': 7, 'strava_athlete_id': 555, 'access_token': 'A',
        'refresh_token': 'R', 'expires_at': time.time() + 3600,
        'stats_cache': {'rides': 5, 'distance_km': 320.5, 'elevation_m': 2400,
                        'moving_hours': 14.2, 'fitness': 72},
        'stats_fetched_at': time.time(),  # fresh → no re-fetch
    }
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club', return_value=None), \
         patch('brevethub.models.get_strava_connection', return_value=connection), \
         patch('brevethub.routes.strava.fetch_activities') as mock_fetch:
        resp = client.get('/dashboard')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '320.5' in body      # distance km
    assert '2400' in body       # elevation m
    assert '72/100' in body     # fitness score
    assert 'Disconnect Strava' in body
    mock_fetch.assert_not_called()
