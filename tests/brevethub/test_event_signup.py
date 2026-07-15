"""BrevetHub rider sign-up (rp_event_signup) — the participation API + dashboard.

Monkeypatch `brevethub.models.*`, use the `client` fixture, never touch a real DB.
Contracts:
  - anon POST → 401 (with a login_url) and NO row written,
  - a signed-in rider can mark interested → going → withdraw; each persists via
    set_rider_signup with the right (rider, event, status),
  - invalid status → 400, unknown event → 404, neither writes,
  - the rider's upcoming sign-ups surface on the dashboard,
  - set_rider_signup is one-row-per-(rider,event): UPDATE existing else INSERT.
"""
import os
import re
from datetime import date
from unittest.mock import patch

_RIDER = {'id': 7, 'email': 'rider@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}
_EVENT = {'id': 11, 'name': 'Point Reyes Lighthouse 200', 'date': '2026-08-15',
          'distance_km': 200, 'region': 'CA: San Francisco'}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_PATH = os.path.join(REPO_ROOT, 'brevethub', 'models.py')


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


# --------------------------------------------------------------------------- #
# Auth gate
# --------------------------------------------------------------------------- #
def test_anonymous_signup_unauthorized_no_write(client):
    with patch('brevethub.models.set_rider_signup') as mock_set, \
         patch('brevethub.models.get_brevet_event') as mock_event:
        resp = client.post('/calendar/11/signup', json={'status': 'going'})
    assert resp.status_code == 401
    data = resp.get_json()
    assert 'login_url' in data          # client can send the guest to sign in
    mock_set.assert_not_called()         # no row written
    mock_event.assert_not_called()       # auth checked before any lookup


# --------------------------------------------------------------------------- #
# Interested → Going → Withdraw
# --------------------------------------------------------------------------- #
def test_rider_marks_interested(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event', return_value=_EVENT), \
         patch('brevethub.models.set_rider_signup') as mock_set:
        resp = client.post('/calendar/11/signup', json={'status': 'interested'})
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'event_id': 11, 'status': 'interested'}
    mock_set.assert_called_once_with(7, 11, 'interested')


def test_rider_changes_to_going(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event', return_value=_EVENT), \
         patch('brevethub.models.set_rider_signup') as mock_set:
        resp = client.post('/calendar/11/signup', json={'status': 'going'})
    assert resp.status_code == 200
    mock_set.assert_called_once_with(7, 11, 'going')


def test_rider_withdraws(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event', return_value=_EVENT), \
         patch('brevethub.models.set_rider_signup') as mock_set:
        resp = client.post('/calendar/11/signup', json={'status': 'withdraw'})
    assert resp.status_code == 200
    mock_set.assert_called_once_with(7, 11, 'withdraw')


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def test_invalid_status_rejected(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event', return_value=_EVENT), \
         patch('brevethub.models.set_rider_signup') as mock_set:
        resp = client.post('/calendar/11/signup', json={'status': 'finished'})
    assert resp.status_code == 400
    mock_set.assert_not_called()


def test_unknown_event_not_found(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event', return_value=None), \
         patch('brevethub.models.set_rider_signup') as mock_set:
        resp = client.post('/calendar/999/signup', json={'status': 'going'})
    assert resp.status_code == 404
    mock_set.assert_not_called()


def test_form_encoded_signup_also_works(client):
    """The API accepts a form POST too (no-JS fallback), not just JSON."""
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event', return_value=_EVENT), \
         patch('brevethub.models.set_rider_signup') as mock_set:
        resp = client.post('/calendar/11/signup', data={'status': 'going'})
    assert resp.status_code == 200
    mock_set.assert_called_once_with(7, 11, 'going')


# --------------------------------------------------------------------------- #
# Dashboard surfacing
# --------------------------------------------------------------------------- #
def test_dashboard_shows_upcoming_signups(client):
    _login(client)
    signups = [{'event_id': 11, 'status': 'going', 'name': 'Point Reyes Lighthouse 200',
                'date': date(2026, 8, 15), 'distance_km': 200, 'region': 'CA: San Francisco',
                'start_location': None, 'start_time': None}]
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club', return_value={'id': 3, 'name': 'SFR'}), \
         patch('brevethub.models.get_strava_connection', return_value=None), \
         patch('brevethub.models.get_rider_signups', return_value=signups):
        resp = client.get('/dashboard')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'My upcoming sign-ups' in body
    assert 'Point Reyes Lighthouse 200' in body
    assert 'Going' in body


def test_dashboard_empty_signups_prompts_calendar(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_club', return_value={'id': 3, 'name': 'SFR'}), \
         patch('brevethub.models.get_strava_connection', return_value=None), \
         patch('brevethub.models.get_rider_signups', return_value=[]):
        resp = client.get('/dashboard')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "haven't signed up" in body
    assert '/calendar' in body


# --------------------------------------------------------------------------- #
# Model shape — one row per (rider, event)
# --------------------------------------------------------------------------- #
def test_set_rider_signup_is_upsert_by_pair():
    """set_rider_signup transitions an existing row in place (UPDATE) or INSERTs a
    new one — so a rider never accumulates duplicate rows for the same event."""
    with open(MODELS_PATH, 'r', encoding='utf-8') as fh:
        src = fh.read()
    body = re.search(r'def set_rider_signup\(.*?\n(?=def )', src, re.DOTALL).group(0)
    assert 'UPDATE rp_event_signup SET status' in body
    assert 'INSERT INTO rp_event_signup' in body
    # Existence keyed on (rider_id, event_id).
    assert 'WHERE rider_id = %s AND event_id = %s' in body
