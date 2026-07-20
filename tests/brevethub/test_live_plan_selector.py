"""Live plan selector (Mission 3, Feature 3) — IDOR-safe allow-set + Surface-B UI.

The member positions poll emits a plan allow-set + the applied plan id; the
resolver refuses any id outside the allow-set (today: the single base plan) and
falls back to base. Models are monkeypatched — no real DB, no RWGPS.
"""
from unittest.mock import patch

_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}
_PUBLIC_RIDE = {'id': 1, 'rider_id': 99, 'is_public': True, 'name': 'Public 200',
                'distance_km': 200, 'start_at': None, 'rwgps_url': None}
_PLAN = {'id': 42, 'name': 'SF 200', 'cutoff_hours': None,
         'total_distance_miles': None}


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def _get(client, url):
    return client.get(url)


def _poll(client, url, plan=_PLAN):
    """Poll the member positions endpoint with a ride that resolves to `plan`."""
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_PUBLIC_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=[]), \
         patch('brevethub.routes.live._resolve_ride_plan', return_value=plan), \
         patch('brevethub.models.get_brevet_route_plan_stops', return_value=[]):
        return client.get(url)


# --------------------------------------------------------------------------- #
# Allow-set + applied plan
# --------------------------------------------------------------------------- #
def test_poll_offers_base_plan_option(client):
    _login(client)
    resp = _poll(client, '/live/1/live-positions.json')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['plans'] == [{'id': 'base', 'name': 'SF 200', 'is_custom': False}]
    assert data['selected_plan_id'] == 'base'


def test_poll_base_plan_option_without_named_plan(client):
    """A ride with no real plan still offers the base option (generic label)."""
    _login(client)
    resp = _poll(client, '/live/1/live-positions.json', plan=None)
    data = resp.get_json()
    assert data['plans'] == [{'id': 'base', 'name': 'Base plan', 'is_custom': False}]
    assert data['selected_plan_id'] == 'base'


def test_explicit_base_plan_id_stays_base(client):
    _login(client)
    resp = _poll(client, '/live/1/live-positions.json?plan_id=base')
    assert resp.get_json()['selected_plan_id'] == 'base'


# --------------------------------------------------------------------------- #
# IDOR guard — out-of-allowset + malformed ids refuse to base
# --------------------------------------------------------------------------- #
def test_out_of_allowset_plan_id_refused_to_base(client):
    """A numeric id outside the allow-set (even the base plan's real numeric id, or
    a stranger's plan) never resolves — it falls back to base."""
    _login(client)
    resp = _poll(client, '/live/1/live-positions.json?plan_id=999999')
    assert resp.status_code == 200
    assert resp.get_json()['selected_plan_id'] == 'base'


def test_base_plans_own_numeric_id_is_not_resolvable(client):
    """Even the base plan's own numeric id is out-of-allowset today (base is the
    'base' sentinel), so it refuses to base — proving strict membership."""
    _login(client)
    resp = _poll(client, '/live/1/live-positions.json?plan_id=42')
    assert resp.get_json()['selected_plan_id'] == 'base'


def test_malformed_plan_id_refused_to_base(client):
    _login(client)
    resp = _poll(client, '/live/1/live-positions.json?plan_id=not-a-number')
    assert resp.status_code == 200
    assert resp.get_json()['selected_plan_id'] == 'base'


# --------------------------------------------------------------------------- #
# Selector present on Surface B
# --------------------------------------------------------------------------- #
def test_selector_control_present_on_member_map(app, client):
    _login(client)
    app.config['MAPBOX_ACCESS_TOKEN'] = 'pk.test-token'
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_PUBLIC_RIDE), \
         patch('brevethub.models.get_live_tracking_rp', return_value=None):
        resp = client.get('/live/1/map')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'plan-select' in body                 # selector control rendered
    assert 'plan_id=' in body                     # re-poll wiring present
