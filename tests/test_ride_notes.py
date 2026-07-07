"""Tests for the ride-notes save endpoint.

POST /rider/<rusa_id>/ride/<ride_id>/notes — owner-guarded, two scopes:
  - scope=overall  → one note for the whole ride
  - scope=segment  → a note on the segment identified by `location`
Truncates to the length cap and persists via the parameterized model writers.
Models patched so no DB is needed.
"""
from unittest.mock import patch

from routes.riders import MAX_RIDE_NOTE_LEN

_RIDER = {'id': 7, 'rusa_id': 123}
_MATCH = {'id': 55}
_URL = '/rider/123/ride/9/notes'


def _own_session(client, rider_id=7):
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = rider_id


def test_overall_note_persists(client):
    _own_session(client)
    captured = {}

    def _update(match_id, note):
        captured.update(match_id=match_id, note=note)
        return 1

    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.update_overall_note', side_effect=_update), \
         patch('models.update_segment_note') as mock_seg:
        resp = client.post(_URL, json={'scope': 'overall', 'note': 'felt strong all day'})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True and body['scope'] == 'overall'
    assert captured == {'match_id': 55, 'note': 'felt strong all day'}
    mock_seg.assert_not_called()


def test_segment_note_persists(client):
    _own_session(client)
    captured = {}

    def _update(match_id, location, note):
        captured.update(match_id=match_id, location=location, note=note)
        return 1

    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.update_segment_note', side_effect=_update), \
         patch('models.update_overall_note') as mock_overall:
        resp = client.post(_URL, json={'scope': 'segment', 'location': 'Pescadero',
                                       'note': 'headwind, eased off'})

    assert resp.status_code == 200
    assert captured == {'match_id': 55, 'location': 'Pescadero', 'note': 'headwind, eased off'}
    mock_overall.assert_not_called()


def test_segment_note_missing_location_rejected(client):
    _own_session(client)
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.update_segment_note') as mock_seg:
        resp = client.post(_URL, json={'scope': 'segment', 'location': '  ', 'note': 'x'})
    assert resp.status_code == 400
    mock_seg.assert_not_called()


def test_unknown_scope_rejected(client):
    _own_session(client)
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.update_overall_note') as mock_overall, \
         patch('models.update_segment_note') as mock_seg:
        resp = client.post(_URL, json={'scope': 'bogus', 'note': 'x'})
    assert resp.status_code == 400
    mock_overall.assert_not_called()
    mock_seg.assert_not_called()


def test_non_owner_forbidden(client):
    _own_session(client, rider_id=99)  # different rider than the profile owner
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.update_overall_note') as mock_overall:
        resp = client.post(_URL, json={'scope': 'overall', 'note': 'nope'})
    assert resp.status_code == 403
    mock_overall.assert_not_called()


def test_no_session_forbidden(client):
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.update_overall_note') as mock_overall:
        resp = client.post(_URL, json={'scope': 'overall', 'note': 'nope'})
    assert resp.status_code == 403
    mock_overall.assert_not_called()


def test_no_match_returns_404(client):
    _own_session(client)
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=None), \
         patch('models.update_overall_note') as mock_overall:
        resp = client.post(_URL, json={'scope': 'overall', 'note': 'x'})
    assert resp.status_code == 404
    mock_overall.assert_not_called()


def test_unknown_rider_404(client):
    _own_session(client)
    with patch('routes.riders.get_rider_by_rusa', return_value=None), \
         patch('models.update_overall_note') as mock_overall:
        resp = client.post(_URL, json={'scope': 'overall', 'note': 'x'})
    assert resp.status_code == 404
    mock_overall.assert_not_called()


def test_note_truncated_to_cap(client):
    _own_session(client)
    captured = {}

    def _update(match_id, note):
        captured['note'] = note
        return 1

    long_text = 'x' * (MAX_RIDE_NOTE_LEN + 500)
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.update_overall_note', side_effect=_update):
        resp = client.post(_URL, json={'scope': 'overall', 'note': long_text})

    assert resp.status_code == 200
    assert len(captured['note']) == MAX_RIDE_NOTE_LEN


def test_empty_note_allowed_for_clearing(client):
    """An empty note is a valid save (lets the rider clear a note)."""
    _own_session(client)
    captured = {}

    def _update(match_id, location, note):
        captured['note'] = note
        return 1

    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.update_segment_note', side_effect=_update):
        resp = client.post(_URL, json={'scope': 'segment', 'location': 'CP1', 'note': ''})

    assert resp.status_code == 200
    assert captured['note'] == ''
