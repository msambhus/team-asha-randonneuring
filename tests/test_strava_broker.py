"""Team Asha side of the shared Strava OAuth broker.

`/strava/connect` (broker on) signs a state and 302s to BrevetHub; `/strava/
broker-return` consumes a one-time handoff row (atomic delete-returning) and writes
`strava_connection` exactly as the old callback did. Every Strava HTTP call and DB
write is mocked — no real network or DB, per repo convention.

The broker-return security cases are first class: an unknown/expired/consumed code
yields no write (single-use), a rider mismatch is a hard reject, and the direct
flow stays intact when the broker is off (the rollback path).
"""
import re
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from shared.broker_state import verify_state

_SECRET = 'broker-secret-value-that-is-long-enough'


def _login(client, user_id=42, rider_id=42):
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
        sess['rider_id'] = rider_id


def _enable_broker(client, enabled=True):
    client.application.config['STRAVA_BROKER_ENABLED'] = enabled
    client.application.config['BROKER_HMAC_SECRET'] = _SECRET


def _handoff(rider_id=42, **overrides):
    row = {
        'ta_rider_id': rider_id,
        'strava_athlete_id': 987,
        'access_token': 'ACCESS',
        'refresh_token': 'REFRESH',
        'expires_at': 1999999999,
        'scope': 'activity:read_all',
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------- #
# connect — broker on
# --------------------------------------------------------------------------- #
def test_connect_broker_on_redirects_to_broker_with_signed_state(client):
    _login(client)
    _enable_broker(client)
    with patch('models.get_strava_connection', return_value=None):
        resp = client.get('/strava/connect')
    assert resp.status_code == 302
    loc = resp.headers['Location']
    assert loc.startswith('https://brevethub.vercel.app/strava/connect')
    qs = parse_qs(urlparse(loc).query)
    assert qs['origin'] == ['team-asha']
    state = qs['state'][0]
    payload = verify_state(state, secret=_SECRET, max_age=600)
    assert payload is not None
    assert payload['origin'] == 'team-asha'
    assert payload['ta_rider_id'] == 42
    assert payload['return_url'].endswith('/strava/broker-return')
    # No Strava token or client secret ever appears in the outbound URL.
    assert 'access_token' not in loc and _SECRET not in loc


def test_connect_broker_on_but_no_secret_falls_back_to_direct(client):
    _login(client)
    client.application.config['STRAVA_BROKER_ENABLED'] = True
    client.application.config['BROKER_HMAC_SECRET'] = None
    with patch('models.get_strava_connection', return_value=None):
        resp = client.get('/strava/connect')
    assert resp.status_code == 302
    assert resp.headers['Location'].startswith('https://www.strava.com/oauth/authorize')


def test_connect_already_connected_redirects_profile(client):
    _login(client)
    _enable_broker(client)
    with patch('models.get_strava_connection', return_value={'rider_id': 42}):
        resp = client.get('/strava/connect')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/my-profile')


# --------------------------------------------------------------------------- #
# connect — broker off (rollback path intact)
# --------------------------------------------------------------------------- #
def test_connect_broker_off_redirects_directly_to_strava(client):
    _login(client)
    client.application.config['STRAVA_BROKER_ENABLED'] = False
    with patch('models.get_strava_connection', return_value=None):
        resp = client.get('/strava/connect')
    assert resp.status_code == 302
    loc = resp.headers['Location']
    assert loc.startswith('https://www.strava.com/oauth/authorize')
    qs = parse_qs(urlparse(loc).query)
    assert 'strava/callback' in qs['redirect_uri'][0]
    with client.session_transaction() as sess:
        assert sess['strava_connecting_rider_id'] == 42


# --------------------------------------------------------------------------- #
# broker-return — happy path
# --------------------------------------------------------------------------- #
def test_broker_return_consumes_handoff_and_stores_connection(client):
    _login(client)
    with patch('models.consume_strava_broker_handoff', return_value=_handoff()) as mock_consume, \
         patch('models.create_strava_connection') as mock_create, \
         patch('routes.strava.sync_rider_activities', return_value={'new': 3, 'updated': 0}):
        resp = client.get('/strava/broker-return?code=one-time-code')
    assert resp.status_code == 302
    mock_consume.assert_called_once_with('one-time-code')
    mock_create.assert_called_once()
    kwargs = mock_create.call_args.kwargs
    assert kwargs['rider_id'] == 42
    assert kwargs['access_token'] == 'ACCESS'
    assert kwargs['refresh_token'] == 'REFRESH'
    assert kwargs['expires_at'] == 1999999999
    assert kwargs['strava_athlete_id'] == 987


# --------------------------------------------------------------------------- #
# broker-return — rejection paths (no write)
# --------------------------------------------------------------------------- #
def test_broker_return_unknown_or_expired_code_no_write(client):
    """consume returns None (unknown / expired / already-consumed) → error, no write."""
    _login(client)
    with patch('models.consume_strava_broker_handoff', return_value=None) as mock_consume, \
         patch('models.create_strava_connection') as mock_create:
        resp = client.get('/strava/broker-return?code=stale')
    assert resp.status_code == 302
    mock_consume.assert_called_once_with('stale')
    mock_create.assert_not_called()


def test_broker_return_second_consume_is_single_use(client):
    """A second consume of the same code returns None (delete-on-read) → no write."""
    _login(client)
    with patch('models.consume_strava_broker_handoff', side_effect=[_handoff(), None]), \
         patch('models.create_strava_connection') as mock_create, \
         patch('routes.strava.sync_rider_activities', return_value={'new': 0, 'updated': 0}):
        first = client.get('/strava/broker-return?code=dup')
        second = client.get('/strava/broker-return?code=dup')
    assert first.status_code == 302 and second.status_code == 302
    assert mock_create.call_count == 1  # only the first consume wrote a connection


def test_broker_return_rider_mismatch_hard_rejects(client):
    _login(client, rider_id=42)
    with patch('models.consume_strava_broker_handoff', return_value=_handoff(rider_id=999)), \
         patch('models.create_strava_connection') as mock_create:
        resp = client.get('/strava/broker-return?code=c')
    assert resp.status_code == 302
    mock_create.assert_not_called()


def test_broker_return_missing_code_no_write(client):
    _login(client)
    with patch('models.consume_strava_broker_handoff') as mock_consume, \
         patch('models.create_strava_connection') as mock_create:
        resp = client.get('/strava/broker-return')
    assert resp.status_code == 302
    mock_consume.assert_not_called()
    mock_create.assert_not_called()


def test_broker_return_user_denied_error_no_write(client):
    _login(client)
    with patch('models.consume_strava_broker_handoff') as mock_consume, \
         patch('models.create_strava_connection') as mock_create:
        resp = client.get('/strava/broker-return?error=access_denied')
    assert resp.status_code == 302
    mock_consume.assert_not_called()
    mock_create.assert_not_called()


# --------------------------------------------------------------------------- #
# TTL-column contract (no DB): the consume query gates on the short handoff TTL,
# never the ~6h Strava-token column. Asserted at the source level so it fails if
# the gate ever regresses.
# --------------------------------------------------------------------------- #
def test_consume_query_gates_on_handoff_expiry_column():
    import inspect
    import models
    src = inspect.getsource(models.consume_strava_broker_handoff)
    where = src[src.index('WHERE'):src.index('RETURNING')]
    assert 'handoff_expires_at' in where, "consume gate must read handoff_expires_at"
    assert 'strava_token_expires_at' not in where, (
        "consume gate must NOT read the Strava-token column (would extend the TTL to ~6h)"
    )
    # Single atomic delete-on-read — no consumed_at flag.
    assert re.search(r'DELETE\s+FROM\s+rp_strava_broker_handoff', src)
    assert 'RETURNING' in src
