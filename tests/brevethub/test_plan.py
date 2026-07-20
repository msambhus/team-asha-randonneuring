"""BrevetHub pacing-plan route — compute/render, save auth, and honesty contracts.

Follows the established BrevetHub test pattern: monkeypatch `brevethub.models.*`,
use the `client` fixture, never touch a real DB or network. First-class contracts:
  - the guest view COMPUTES + renders a schedule (arrival, time bank, km/h) and the
    ACP time limit — a real render-path assertion (proves NO missing-filter 500),
  - the guest view exposes NO rider PII,
  - a guest SAVE is refused with 401 (+ a login_url), a signed-in rider's save is
    200 and persists a SERVER-computed plan_data,
  - an unknown event -> 404,
  - a target speed OR finish time recomputes the schedule.
"""
from unittest.mock import patch

import pytest


_RIDER = {'id': 7, 'email': 'rider@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}

# A cached 200 km brevet with an ACP time limit (as get_brevet_event_full returns).
_EVENT = {
    'id': 11, 'rusa_route_id': '1234', 'name': 'Point Reyes Lighthouse 200',
    'date': '2026-08-15', 'distance_km': 200, 'region': 'CA: San Francisco',
    'ride_type': 'ACP brevet', 'elevation_ft': 4200, 'rwgps_url': None,
    'start_location': None, 'start_time': None, 'time_limit_hours': 13.5,
}
# A brevet with NO cached time limit -> the route falls back to the ACP mapping.
_EVENT_NO_LIMIT = dict(_EVENT, id=13, name='Fog City 300', distance_km=300,
                       time_limit_hours=None)


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


# --------------------------------------------------------------------------- #
# Guest compute + render
# --------------------------------------------------------------------------- #
def test_guest_plan_renders_schedule(client):
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT):
        resp = client.get('/plan/11?speed=20')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Event + ACP time limit render.
    assert 'Point Reyes Lighthouse 200' in body
    assert '13.5' in body                 # ACP time limit (h)
    # Schedule cells: per-stop distance, avg speed (km/h), arrival, time bank.
    assert '100 km' in body               # first evenly-spaced control
    assert '200 km' in body               # final stop at the exact total
    assert '20.0 km/h' in body            # avg speed emerges as km/h (unit-agnostic)
    assert 'Time bank vs cutoff' in body  # the time-bank column renders
    # A signed-out guest can compute but not save, and sees no rider PII.
    assert 'rider@example.com' not in body
    assert 'Save this plan' not in body
    assert 'Sign in' in body              # guest gets the sign-in-to-save prompt


def test_guest_plan_no_missing_filter_500(client):
    """Render-path proof (not a Jinja parse check): the page renders fully with no
    500 from a missing commafy/clean_name filter, even with a large elevation."""
    event = dict(_EVENT, elevation_ft=12500)
    with patch('brevethub.models.get_brevet_event_full', return_value=event):
        resp = client.get('/plan/11')
    assert resp.status_code == 200
    assert 'Pacing plan' in resp.get_data(as_text=True)


def test_default_pace_used_when_no_target(client):
    """No speed/finish -> the default 20 km/h target computes a schedule."""
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT):
        resp = client.get('/plan/11')
    assert resp.status_code == 200
    assert '20.0 km/h' in resp.get_data(as_text=True)


def test_finish_time_param_recomputes_pace(client):
    """A target FINISH time is converted to a pace and recomputes the schedule."""
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT):
        resp = client.get('/plan/11?finish=13.5')
    assert resp.status_code == 200
    # 200 km / 13.5 h ~= 14.8 km/h.
    assert '14.8 km/h' in resp.get_data(as_text=True)


def test_missing_time_limit_falls_back_to_acp_mapping(client):
    """An event with no cached time_limit_hours uses the ACP mapping (300 -> 20 h)."""
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT_NO_LIMIT):
        resp = client.get('/plan/13?speed=20')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Cutoff now renders in the TA-style ACP-limit stat card (value + label).
    assert '<div class="number">20</div><div class="label">ACP limit (h)</div>' in body  # 300 km -> 20 h band
    assert '300 km' in body


def test_unknown_event_404(client):
    with patch('brevethub.models.get_brevet_event_full', return_value=None):
        resp = client.get('/plan/999')
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Save — guest 401, rider 200 with server-computed plan_data
# --------------------------------------------------------------------------- #
def test_guest_save_returns_401_with_login_url(client):
    """A guest save is refused BEFORE any model call, with a login_url to bounce to."""
    resp = client.post('/plan/11/save', json={'speed': 20})
    assert resp.status_code == 401
    data = resp.get_json()
    assert 'login_url' in data


def test_rider_save_persists_server_computed_plan(client):
    _login(client)
    captured = {}

    def _fake_upsert(rider_id, event_id, **kwargs):
        captured['rider_id'] = rider_id
        captured['event_id'] = event_id
        captured.update(kwargs)

    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.upsert_rider_brevet_plan',
               side_effect=_fake_upsert) as mock_up:
        resp = client.post('/plan/11/save', json={'speed': 20})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True and body['target_speed_kmh'] == 20.0
    mock_up.assert_called_once()
    # The persisted plan is SERVER-computed (not trusted from the client).
    assert captured['rider_id'] == 7 and captured['event_id'] == 11
    assert captured['target_speed_kmh'] == 20.0
    plan_data = captured['plan_data']
    assert plan_data['target_speed_kmh'] == 20.0
    assert plan_data['total_km'] == 200
    assert plan_data['stops'], "server must compute a non-empty schedule"
    assert plan_data['stops'][-1]['distance_km'] == 200


def test_rider_save_unknown_event_404(client):
    _login(client)
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event_full', return_value=None), \
         patch('brevethub.models.upsert_rider_brevet_plan') as mock_up:
        resp = client.post('/plan/999/save', json={'speed': 20})
    assert resp.status_code == 404
    mock_up.assert_not_called()


def test_rider_sees_save_control_and_saved_state(client):
    _login(client)
    saved = {'rider_id': 7, 'event_id': 11, 'target_speed_kmh': 22.0,
             'target_finish_min': 545, 'plan_data': {'stops': []}}
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.get_rider_brevet_plan', return_value=saved):
        resp = client.get('/plan/11?speed=20')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Save this plan' in body
    assert 'You have a saved plan' in body
