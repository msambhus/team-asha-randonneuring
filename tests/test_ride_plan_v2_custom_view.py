"""Auth + render tests for ?view=custom on the v2 ride plan route."""
from unittest.mock import patch


_BASE_PLAN = {
    'id': 5,
    'slug': 'mt-hamilton-200k',
    'name': 'Mt Hamilton 200K',
    'total_distance_miles': 130.0,
    'total_elevation_ft': 8000,
    'rwgps_url': None,
    'rwgps_url_team': None,
    'start_time': '06:00',
}

_BASE_STOPS = [
    {'id': 1, 'location': 'Start', 'distance_miles': 0.0, 'elevation_gain': 0,
     'segment_time_min': 0, 'stop_duration_min': 0, 'stop_type': 'start', 'notes': None,
     'stop_order': 1},
    {'id': 2, 'location': 'Mid', 'distance_miles': 60.0, 'elevation_gain': 4000,
     'segment_time_min': 240, 'stop_duration_min': 15, 'stop_type': 'control', 'notes': None,
     'stop_order': 2},
    {'id': 3, 'location': 'Finish', 'distance_miles': 130.0, 'elevation_gain': 4000,
     'segment_time_min': 280, 'stop_duration_min': 0, 'stop_type': 'finish', 'notes': None,
     'stop_order': 3},
]


def _merged_stops_for(_custom_plan_id):
    """Return same shape as get_merged_plan_stops — (stops, custom_plan_dict)."""
    return list(_BASE_STOPS), {'name': 'My pace', 'id': _custom_plan_id}


def _patches(extras=None):
    """Compose the common patch stack used by every test below.

    Where the symbol is imported affects which target we patch. Module-level
    imports in routes/riders.py are patched on `routes.riders.*`; symbols
    imported inside the function body are patched on `models.*`.
    """
    base = {
        'routes.riders.get_ride_plan_by_slug': lambda slug: _BASE_PLAN,
        'routes.riders.get_ride_plan_stops': lambda pid: list(_BASE_STOPS),
        'routes.riders.get_public_custom_plans': lambda pid: [],
        'routes.riders.fetch_route': lambda rid: {'track_points': []},
        'routes.riders.fetch_stop_wind': lambda **kw: None,
        'routes.riders.get_merged_plan_stops': _merged_stops_for,
        # In-function imports → patch on models module:
        'models.get_latest_ride_for_plan': lambda pid: None,
        'models.get_upcoming_rusa_events': lambda: [],
        'models.get_signups_for_ride': lambda eid: [],
    }
    if extras:
        base.update(extras)
    return base


def _apply(patches):
    """Turn a dict of dotted-path → return-value-or-callable into context managers."""
    mgrs = []
    for path, val in patches.items():
        if callable(val):
            mgrs.append(patch(path, side_effect=val))
        else:
            mgrs.append(patch(path, return_value=val))
    return mgrs


def _run(patches, client_action):
    mgrs = _apply(patches)
    for m in mgrs:
        m.start()
    try:
        return client_action()
    finally:
        for m in mgrs:
            m.stop()


# ---------------------------------------------------------------------------
# Auth matrix
# ---------------------------------------------------------------------------

def _user_patches(user):
    """Patch get_user_by_id on both the route module and models (is_admin_user
    re-imports it inside its body, so we need both to keep DB access mocked).
    """
    return {
        'routes.riders.get_user_by_id': user,
        'models.get_user_by_id': user,
    }


def test_owner_can_view_own_private_custom_plan(client):
    cp = {'id': 42, 'rider_id': 7, 'base_plan_id': 5, 'is_public': False,
          'name': 'My pace', 'first_name': 'Alice'}
    extras = {
        'routes.riders.get_custom_plan': cp,
        'routes.riders.get_custom_plan_with_rider_info': cp,
    }
    extras.update(_user_patches({'id': 1, 'rider_id': 7}))
    with client.session_transaction() as s:
        s['user_id'] = 1
    resp = _run(_patches(extras), lambda: client.get('/ride-plan/mt-hamilton-200k?view=custom'))
    assert resp.status_code == 200
    assert b'Viewing your custom plan' in resp.data


def test_anon_blocked_on_private_custom_plan(client):
    cp = {'id': 42, 'rider_id': 7, 'base_plan_id': 5, 'is_public': False,
          'name': 'Secret', 'first_name': 'Alice'}
    extras = {'routes.riders.get_custom_plan_with_rider_info': cp}
    extras.update(_user_patches(None))
    resp = _run(_patches(extras),
                lambda: client.get('/ride-plan/mt-hamilton-200k?view=custom&plan=42'))
    assert resp.status_code == 404


def test_anon_allowed_on_public_custom_plan(client):
    cp = {'id': 42, 'rider_id': 7, 'base_plan_id': 5, 'is_public': True,
          'name': 'Comfort pace', 'first_name': 'Alice'}
    extras = {'routes.riders.get_custom_plan_with_rider_info': cp}
    extras.update(_user_patches(None))
    resp = _run(_patches(extras),
                lambda: client.get('/ride-plan/mt-hamilton-200k?view=custom&plan=42'))
    assert resp.status_code == 200
    assert b"Alice" in resp.data  # owner attribution
    assert b'Comfort pace' in resp.data


def test_base_fallback_when_no_custom_plan_exists(client):
    """User requests view=custom but has no custom plan — render base, not 404."""
    extras = {'routes.riders.get_custom_plan': None}
    extras.update(_user_patches({'id': 1, 'rider_id': 7}))
    with client.session_transaction() as s:
        s['user_id'] = 1
    resp = _run(_patches(extras), lambda: client.get('/ride-plan/mt-hamilton-200k?view=custom'))
    assert resp.status_code == 200
    # Banner absent — we silently fell back.
    assert b'Viewing your custom plan' not in resp.data


def test_plan_id_for_wrong_base_returns_404(client):
    cp = {'id': 42, 'rider_id': 7, 'base_plan_id': 999, 'is_public': True,
          'name': 'X', 'first_name': 'Alice'}
    extras = {'routes.riders.get_custom_plan_with_rider_info': cp}
    extras.update(_user_patches(None))
    resp = _run(_patches(extras),
                lambda: client.get('/ride-plan/mt-hamilton-200k?view=custom&plan=42'))
    assert resp.status_code == 404
