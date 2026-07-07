"""Tests for the per-stop commentary save endpoint.

POST /rider/<rusa_id>/ride/<ride_id>/stop-commentary — owner-guarded, validates
the stop index against the persisted detected_stops array, truncates to the
length cap, and persists via the parameterized update. Models patched so no DB
is needed.
"""
from unittest.mock import patch

from routes.riders import MAX_STOP_COMMENTARY_LEN

_RIDER = {'id': 7, 'rusa_id': 123}
_MATCH = {'id': 55}
_ANALYSIS = {'detected_stops': [
    {'distance_miles': 2.0, 'start_time_s': 600, 'lat': 37.0, 'lng': -122.0},
    {'distance_miles': 5.0, 'start_time_s': 1800, 'lat': 37.1, 'lng': -122.1},
]}

_URL = '/rider/123/ride/9/stop-commentary'


def _own_session(client, rider_id=7):
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = rider_id


def test_happy_path_persists_commentary(client):
    _own_session(client)
    captured = {}

    def _update(match_id, stop_index, commentary):
        captured.update(match_id=match_id, stop_index=stop_index, commentary=commentary)
        return 1

    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.get_strava_ride_analysis', return_value=_ANALYSIS), \
         patch('models.update_stop_commentary', side_effect=_update):
        resp = client.post(_URL, json={'stop_index': 1, 'commentary': 'flat tire here'})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    assert body['stop_index'] == 1
    assert captured == {'match_id': 55, 'stop_index': 1, 'commentary': 'flat tire here'}


def test_resolves_target_by_start_time_identity(client):
    """When start_time_s is sent, the write targets the matching element even if
    the positional index would point elsewhere — robust to array drift."""
    _own_session(client)
    captured = {}

    def _update(match_id, stop_index, commentary):
        captured.update(stop_index=stop_index, commentary=commentary)
        return 1

    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.get_strava_ride_analysis', return_value=_ANALYSIS), \
         patch('models.update_stop_commentary', side_effect=_update):
        # stop_index deliberately wrong (0); identity 1800 resolves to element 1.
        resp = client.post(_URL, json={'stop_index': 0, 'start_time_s': 1800,
                                       'commentary': 'resolved by identity'})

    assert resp.status_code == 200
    assert captured['stop_index'] == 1


def test_unknown_start_time_identity_rejected(client):
    """A start_time_s that matches no persisted stop is a 400 (never a silent
    fallback to a possibly-wrong positional index)."""
    _own_session(client)
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.get_strava_ride_analysis', return_value=_ANALYSIS), \
         patch('models.update_stop_commentary') as mock_update:
        resp = client.post(_URL, json={'stop_index': 0, 'start_time_s': 99999,
                                       'commentary': 'x'})
    assert resp.status_code == 400
    mock_update.assert_not_called()


def test_non_owner_forbidden(client):
    # Logged in as a different rider than the profile owner.
    _own_session(client, rider_id=99)
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.update_stop_commentary') as mock_update:
        resp = client.post(_URL, json={'stop_index': 0, 'commentary': 'nope'})
    assert resp.status_code == 403
    mock_update.assert_not_called()


def test_no_session_forbidden(client):
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.update_stop_commentary') as mock_update:
        resp = client.post(_URL, json={'stop_index': 0, 'commentary': 'nope'})
    assert resp.status_code == 403
    mock_update.assert_not_called()


def test_index_out_of_range_rejected(client):
    _own_session(client)
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.get_strava_ride_analysis', return_value=_ANALYSIS), \
         patch('models.update_stop_commentary') as mock_update:
        resp = client.post(_URL, json={'stop_index': 5, 'commentary': 'x'})
    assert resp.status_code == 400
    mock_update.assert_not_called()


def test_negative_index_rejected(client):
    _own_session(client)
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.get_strava_ride_analysis', return_value=_ANALYSIS), \
         patch('models.update_stop_commentary') as mock_update:
        resp = client.post(_URL, json={'stop_index': -1, 'commentary': 'x'})
    assert resp.status_code == 400
    mock_update.assert_not_called()


def test_non_integer_index_rejected(client):
    _own_session(client)
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.get_strava_ride_analysis', return_value=_ANALYSIS), \
         patch('models.update_stop_commentary') as mock_update:
        resp = client.post(_URL, json={'stop_index': 'abc', 'commentary': 'x'})
    assert resp.status_code == 400
    mock_update.assert_not_called()


def test_no_match_returns_404(client):
    _own_session(client)
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=None), \
         patch('models.update_stop_commentary') as mock_update:
        resp = client.post(_URL, json={'stop_index': 0, 'commentary': 'x'})
    assert resp.status_code == 404
    mock_update.assert_not_called()


def test_commentary_truncated_to_cap(client):
    _own_session(client)
    captured = {}

    def _update(match_id, stop_index, commentary):
        captured['commentary'] = commentary
        return 1

    long_text = 'x' * (MAX_STOP_COMMENTARY_LEN + 500)
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.get_strava_ride_analysis', return_value=_ANALYSIS), \
         patch('models.update_stop_commentary', side_effect=_update):
        resp = client.post(_URL, json={'stop_index': 0, 'commentary': long_text})

    assert resp.status_code == 200
    assert len(captured['commentary']) == MAX_STOP_COMMENTARY_LEN


def test_unknown_rider_404(client):
    _own_session(client)
    with patch('routes.riders.get_rider_by_rusa', return_value=None), \
         patch('models.update_stop_commentary') as mock_update:
        resp = client.post(_URL, json={'stop_index': 0, 'commentary': 'x'})
    assert resp.status_code == 404
    mock_update.assert_not_called()
