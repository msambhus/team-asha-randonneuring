"""Broker changes that let a separate-database consumer (Runnernet) broker a
Strava connect through BrevetHub: the `runnernet` origin and the server-to-server
handoff redeem endpoint. Team Asha's shared-DB flow is unchanged."""
from unittest.mock import patch

import pytest

from brevethub.app import create_app


@pytest.fixture
def app():
    application = create_app()
    application.config['TESTING'] = True
    application.config['BROKER_HMAC_SECRET'] = 'hmac-secret'
    application.config['BROKER_REDEEM_SECRET'] = 'redeem-secret'
    application.config['STRAVA_CLIENT_SECRET'] = 'strava-secret'
    application.config['BROKER_RETURN_URL_ALLOWLIST'] = ['https://runnernet.example']
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def test_redeem_requires_configured_secret(app, client):
    app.config['BROKER_REDEEM_SECRET'] = None
    assert client.post('/strava/broker/redeem', json={'code': 'x'}).status_code == 503


def test_redeem_rejects_bad_bearer_and_missing_code(client):
    assert client.post('/strava/broker/redeem', headers={'Authorization': 'Bearer nope'},
                       json={'code': 'x'}).status_code == 401
    assert client.post('/strava/broker/redeem', headers={'Authorization': 'Bearer redeem-secret'},
                       json={}).status_code == 400


def test_redeem_returns_tokens_once_then_gone(client):
    row = {
        'ta_rider_id': '11111111-1111-1111-1111-111111111111',
        'strava_athlete_id': 42, 'access_token': 'acc', 'refresh_token': 'ref',
        'strava_token_expires_at': 1790000000, 'scope': 'activity:read_all',
    }
    with patch('brevethub.routes.strava.models.consume_broker_handoff', side_effect=[row, None]):
        first = client.post('/strava/broker/redeem', headers={'Authorization': 'Bearer redeem-secret'},
                            json={'code': 'good'})
        assert first.status_code == 200
        assert first.get_json() == row
        second = client.post('/strava/broker/redeem', headers={'Authorization': 'Bearer redeem-secret'},
                             json={'code': 'good'})
        assert second.status_code == 410


def test_broker_connect_accepts_runnernet_origin(client):
    payload = {
        'origin': 'runnernet', 'ta_rider_id': 'uuid-1',
        'return_url': 'https://runnernet.example/api/v1/strava/broker-return', 'nonce': 'n1',
    }
    with patch('brevethub.routes.strava.verify_state', return_value=payload), \
         patch('brevethub.routes.strava.models.claim_broker_state', return_value={'nonce': 'n1'}):
        response = client.get('/strava/connect?origin=runnernet&state=signed')
        assert response.status_code == 302
        assert response.headers['Location'].startswith('https://www.strava.com/oauth/authorize')


def test_broker_connect_rejects_unknown_origin(client):
    assert client.get('/strava/connect?origin=evil&state=signed').status_code == 400
