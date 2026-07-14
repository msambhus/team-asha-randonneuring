"""Unit tests for shared/strava.py — the framework-free, epoch-native Strava layer.

All HTTP is mocked (no real network, per repo convention). These assert the pure
protocol behavior both Team Asha (via services/strava.py) and BrevetHub rely on:
config passed by keyword, `expires_at` returned as the epoch integer Strava sends,
pagination, the 429 signal, and the identical activity transform + summary.
"""
from unittest.mock import MagicMock, patch

import pytest

from shared import strava


def _resp(json_data=None, status_code=200, ok=True, text=''):
    r = MagicMock()
    r.json.return_value = json_data if json_data is not None else {}
    r.status_code = status_code
    r.ok = ok
    r.text = text
    r.raise_for_status.side_effect = None if ok else Exception('http error')
    return r


def test_exchange_code_posts_creds_and_returns_epoch():
    payload = {'athlete': {'id': 42}, 'access_token': 'A', 'refresh_token': 'R',
               'expires_at': 1999999999}
    with patch('shared.strava.requests.post', return_value=_resp(payload)) as mock_post:
        out = strava.exchange_code_for_token(
            'the-code', client_id='cid', client_secret='sec',
            token_url='https://strava/oauth/token')
    assert out['expires_at'] == 1999999999  # unchanged epoch integer
    args, kwargs = mock_post.call_args
    assert args[0] == 'https://strava/oauth/token'
    assert kwargs['data']['grant_type'] == 'authorization_code'
    assert kwargs['data']['client_id'] == 'cid'
    assert kwargs['data']['code'] == 'the-code'


def test_exchange_code_requires_secret():
    with pytest.raises(Exception) as exc:
        strava.exchange_code_for_token('c', client_id='cid', client_secret='',
                                       token_url='u')
    assert 'STRAVA_CLIENT_SECRET' in str(exc.value)


def test_exchange_code_raises_on_http_error():
    with patch('shared.strava.requests.post',
               return_value=_resp({'message': 'bad'}, status_code=400, ok=False)):
        with pytest.raises(Exception) as exc:
            strava.exchange_code_for_token('c', client_id='cid', client_secret='s',
                                           token_url='u')
    assert 'Strava token error (400)' in str(exc.value)


def test_refresh_access_token_returns_epoch():
    payload = {'access_token': 'A2', 'refresh_token': 'R2', 'expires_at': 2100000000}
    with patch('shared.strava.requests.post', return_value=_resp(payload)) as mock_post:
        out = strava.refresh_access_token('R', client_id='cid', client_secret='s',
                                          token_url='u')
    assert out['expires_at'] == 2100000000
    assert mock_post.call_args.kwargs['data']['grant_type'] == 'refresh_token'
    assert mock_post.call_args.kwargs['data']['refresh_token'] == 'R'


def test_refresh_access_token_raises_on_http_error():
    with patch('shared.strava.requests.post',
               return_value=_resp({}, status_code=401, ok=False)):
        with pytest.raises(Exception) as exc:
            strava.refresh_access_token('R', client_id='c', client_secret='s',
                                        token_url='u')
    assert 'Strava token refresh error (401)' in str(exc.value)


def test_fetch_activities_paginates_and_sends_bearer():
    page1 = [{'id': i} for i in range(100)]  # full page → fetch next
    page2 = [{'id': 100}]                    # short page → stop
    with patch('shared.strava.requests.get',
               side_effect=[_resp(page1), _resp(page2)]) as mock_get:
        acts = strava.fetch_activities('tok', api_base='https://api', after_epoch=123)
    assert len(acts) == 101
    first_call = mock_get.call_args_list[0]
    assert first_call.kwargs['headers']['Authorization'] == 'Bearer tok'
    assert first_call.kwargs['params']['after'] == 123


def test_fetch_activities_raises_on_rate_limit():
    with patch('shared.strava.requests.get', return_value=_resp([], status_code=429)):
        with pytest.raises(Exception) as exc:
            strava.fetch_activities('tok', api_base='https://api', after_epoch=1)
    assert 'rate limit' in str(exc.value).lower()


def test_transform_activity_maps_fields():
    row = strava.transform_activity(
        {'id': 9, 'name': 'AM ride', 'type': 'Ride', 'distance': 42000.0,
         'total_elevation_gain': 500, 'moving_time': 3600}, rider_id=7)
    assert row['rider_id'] == 7
    assert row['strava_activity_id'] == 9
    assert row['activity_type'] == 'Ride'
    assert row['distance'] == 42000.0
    assert row['strava_url'] == 'https://www.strava.com/activities/9'


def test_summarize_activities_totals_cycling_only():
    activities = [
        {'activity_type': 'Ride', 'distance': 40000, 'total_elevation_gain': 400, 'moving_time': 3600},
        {'activity_type': 'Ride', 'distance': 60000, 'total_elevation_gain': 600, 'moving_time': 7200},
        {'activity_type': 'Run', 'distance': 10000, 'total_elevation_gain': 100, 'moving_time': 3600},
    ]
    s = strava.summarize_activities(activities)
    assert s['rides'] == 2                    # Run excluded
    assert s['distance_km'] == 100.0
    assert s['elevation_m'] == 1000
    assert s['moving_hours'] == 3.0


def test_deauthorize_is_best_effort():
    with patch('shared.strava.requests.post', side_effect=Exception('boom')):
        # must not raise
        strava.deauthorize_strava('tok')
