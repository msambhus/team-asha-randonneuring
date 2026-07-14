"""Signup — profile completion after Google sign-in.

Collects an OPTIONAL RUSA ID + a club affiliation and writes both to the signed-in
rider's `rp_rider` row. RUSA ID is shape-checked only; a duplicate claim is
soft-flagged, never rejected. Model access is patched (no real DB).
"""
from unittest.mock import patch

import pytest

_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': False, 'rusa_id': None, 'club_id': None,
          'rusa_id_duplicate': False}

_CLUBS = [
    {'id': 1, 'name': 'Oregon Randonneurs', 'city': 'Portland', 'state': 'OR',
     'rusa_club_id': 'ORR'},
    {'id': 2, 'name': 'Seattle International Randonneurs', 'city': 'Seattle',
     'state': 'WA', 'rusa_club_id': 'SIR'},
]


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def test_signup_get_requires_login(client):
    resp = client.get('/signup/')
    assert resp.status_code in (301, 302)
    assert '/auth/login' in resp.headers['Location']


def test_signup_get_renders_club_picker(client):
    _login(client)
    with patch('brevethub.routes.signup.current_rider', return_value=_RIDER), \
         patch('brevethub.models.get_all_clubs', return_value=_CLUBS):
        resp = client.get('/signup/')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Oregon Randonneurs' in body
    assert 'Seattle International Randonneurs' in body


def test_signup_post_stores_club_only(client):
    """Club chosen, RUSA left blank → stored with rusa_id None, not flagged."""
    _login(client)
    with patch('brevethub.routes.signup.current_rider', return_value=_RIDER), \
         patch('brevethub.models.get_all_clubs', return_value=_CLUBS), \
         patch('brevethub.models.club_exists', return_value=True), \
         patch('brevethub.models.complete_rider_profile') as mock_complete:
        resp = client.post('/signup/', data={'club_id': '2', 'rusa_id': ''})
    assert resp.status_code in (301, 302)
    assert resp.headers['Location'].endswith('/dashboard')
    mock_complete.assert_called_once_with(7, None, 2, rusa_id_duplicate=False)


def test_signup_post_stores_optional_rusa_id(client):
    _login(client)
    with patch('brevethub.routes.signup.current_rider', return_value=_RIDER), \
         patch('brevethub.models.get_all_clubs', return_value=_CLUBS), \
         patch('brevethub.models.club_exists', return_value=True), \
         patch('brevethub.models.rusa_id_already_claimed', return_value=False), \
         patch('brevethub.models.complete_rider_profile') as mock_complete:
        resp = client.post('/signup/', data={'club_id': '1', 'rusa_id': '12345'})
    assert resp.status_code in (301, 302)
    mock_complete.assert_called_once_with(7, '12345', 1, rusa_id_duplicate=False)


def test_signup_post_soft_flags_duplicate_rusa_id(client):
    """A RUSA ID already claimed by another rider is stored but soft-flagged."""
    _login(client)
    with patch('brevethub.routes.signup.current_rider', return_value=_RIDER), \
         patch('brevethub.models.get_all_clubs', return_value=_CLUBS), \
         patch('brevethub.models.club_exists', return_value=True), \
         patch('brevethub.models.rusa_id_already_claimed', return_value=True), \
         patch('brevethub.models.complete_rider_profile') as mock_complete:
        resp = client.post('/signup/', data={'club_id': '1', 'rusa_id': '12345'})
    assert resp.status_code in (301, 302)
    mock_complete.assert_called_once_with(7, '12345', 1, rusa_id_duplicate=True)


def test_signup_post_requires_a_valid_club(client):
    """No club (or an unknown club) → re-render the form, nothing stored."""
    _login(client)
    with patch('brevethub.routes.signup.current_rider', return_value=_RIDER), \
         patch('brevethub.models.get_all_clubs', return_value=_CLUBS), \
         patch('brevethub.models.complete_rider_profile') as mock_complete:
        resp = client.post('/signup/', data={'club_id': '', 'rusa_id': ''})
    assert resp.status_code == 200
    mock_complete.assert_not_called()


def test_signup_post_rejects_malformed_rusa_id(client):
    """A non-numeric RUSA ID is rejected (shape check), nothing stored."""
    _login(client)
    with patch('brevethub.routes.signup.current_rider', return_value=_RIDER), \
         patch('brevethub.models.get_all_clubs', return_value=_CLUBS), \
         patch('brevethub.models.club_exists', return_value=True), \
         patch('brevethub.models.complete_rider_profile') as mock_complete:
        resp = client.post('/signup/', data={'club_id': '1', 'rusa_id': 'abc'})
    assert resp.status_code == 200
    mock_complete.assert_not_called()
