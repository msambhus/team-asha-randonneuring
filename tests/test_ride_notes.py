"""Tests for the ride-notes save/delete endpoint.

POST /rider/<rusa_id>/ride/<ride_id>/notes — owner-guarded, three scopes:
  - scope=overall              → one note for the whole ride
  - scope=segment, ident=<loc> → a note on a planned segment
  - scope=stop,    ident=<key> → a note on an unplanned stop (distance key)
A blank note deletes (the model removes the JSONB key). Truncated to the cap.
Models patched so no DB is needed.
"""
from unittest.mock import patch

from routes.riders import MAX_RIDE_NOTE_LEN, _stop_note_key

_RIDER = {'id': 7, 'rusa_id': 123}
_MATCH = {'id': 55}
_URL = '/rider/123/ride/9/notes'


def _own_session(client, rider_id=7):
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = rider_id


def test_stop_note_key_is_one_decimal_distance():
    assert _stop_note_key(42) == '42.0'
    assert _stop_note_key(148.04) == '148.0'
    assert _stop_note_key(148.06) == '148.1'


def test_overall_note_persists(client):
    _own_session(client)
    captured = {}

    def _update(match_id, note):
        captured.update(match_id=match_id, note=note)
        return 1

    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.update_overall_note', side_effect=_update), \
         patch('models.update_segment_note') as mock_seg, \
         patch('models.update_stop_note') as mock_stop:
        resp = client.post(_URL, json={'scope': 'overall', 'note': 'felt strong all day'})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True and body['scope'] == 'overall'
    assert captured == {'match_id': 55, 'note': 'felt strong all day'}
    mock_seg.assert_not_called()
    mock_stop.assert_not_called()


def test_segment_note_persists(client):
    _own_session(client)
    captured = {}

    def _update(match_id, location, note):
        captured.update(match_id=match_id, location=location, note=note)
        return 1

    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.update_segment_note', side_effect=_update), \
         patch('models.update_overall_note') as mock_overall, \
         patch('models.update_stop_note') as mock_stop:
        resp = client.post(_URL, json={'scope': 'segment', 'ident': 'Pescadero',
                                       'note': 'headwind, eased off'})

    assert resp.status_code == 200
    assert captured == {'match_id': 55, 'location': 'Pescadero', 'note': 'headwind, eased off'}
    mock_overall.assert_not_called()
    mock_stop.assert_not_called()


def test_stop_note_persists(client):
    _own_session(client)
    captured = {}

    def _update(match_id, key, note):
        captured.update(match_id=match_id, key=key, note=note)
        return 1

    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.update_stop_note', side_effect=_update), \
         patch('models.update_segment_note') as mock_seg, \
         patch('models.update_overall_note') as mock_overall:
        resp = client.post(_URL, json={'scope': 'stop', 'ident': '148.0',
                                       'note': 'long taco bell stop, cramped'})

    assert resp.status_code == 200
    assert captured == {'match_id': 55, 'key': '148.0', 'note': 'long taco bell stop, cramped'}
    mock_seg.assert_not_called()
    mock_overall.assert_not_called()


def test_blank_note_deletes_segment(client):
    """A blank note is a valid save that clears the note (model removes the key)."""
    _own_session(client)
    captured = {}

    def _update(match_id, location, note):
        captured['note'] = note
        return 1

    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.update_segment_note', side_effect=_update):
        resp = client.post(_URL, json={'scope': 'segment', 'ident': 'CP1', 'note': ''})

    assert resp.status_code == 200
    assert captured['note'] == ''


def test_segment_note_missing_ident_rejected(client):
    _own_session(client)
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.update_segment_note') as mock_seg:
        resp = client.post(_URL, json={'scope': 'segment', 'ident': '  ', 'note': 'x'})
    assert resp.status_code == 400
    mock_seg.assert_not_called()


def test_stop_note_missing_ident_rejected(client):
    _own_session(client)
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.update_stop_note') as mock_stop:
        resp = client.post(_URL, json={'scope': 'stop', 'note': 'x'})
    assert resp.status_code == 400
    mock_stop.assert_not_called()


def test_unknown_scope_rejected(client):
    _own_session(client)
    with patch('routes.riders.get_rider_by_rusa', return_value=_RIDER), \
         patch('models.get_strava_ride_match', return_value=_MATCH), \
         patch('models.update_overall_note') as mock_overall, \
         patch('models.update_segment_note') as mock_seg, \
         patch('models.update_stop_note') as mock_stop:
        resp = client.post(_URL, json={'scope': 'bogus', 'note': 'x'})
    assert resp.status_code == 400
    mock_overall.assert_not_called()
    mock_seg.assert_not_called()
    mock_stop.assert_not_called()


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
