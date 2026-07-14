"""GET /api/clubs — the seeded club directory the signup picker reads from.

Public (no auth) and read-only. Model access is patched so no real DB is opened
(per repo convention: no real external calls in tests).
"""
from unittest.mock import patch

_CLUBS = [
    {'id': 1, 'name': 'Oregon Randonneurs', 'city': 'Portland', 'state': 'OR',
     'rusa_club_id': 'ORR'},
    {'id': 2, 'name': 'Seattle International Randonneurs', 'city': 'Seattle',
     'state': 'WA', 'rusa_club_id': 'SIR'},
]


def test_api_clubs_returns_seeded_clubs(client):
    with patch('brevethub.models.get_all_clubs', return_value=_CLUBS):
        resp = client.get('/api/clubs')
    assert resp.status_code == 200
    data = resp.get_json()
    assert [c['name'] for c in data] == [
        'Oregon Randonneurs', 'Seattle International Randonneurs']
    assert data[0] == {'id': 1, 'name': 'Oregon Randonneurs', 'city': 'Portland',
                       'state': 'OR', 'rusa_club_id': 'ORR'}


def test_api_clubs_is_public(client):
    """No auth required — a signed-out visitor can list clubs for the picker."""
    with patch('brevethub.models.get_all_clubs', return_value=[]):
        resp = client.get('/api/clubs')
    assert resp.status_code == 200
    assert resp.get_json() == []
