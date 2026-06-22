"""Base view surfaces a way back to the rider's own custom plan (banner +
Strategies-tab link) when they have one.
"""
from unittest.mock import patch

_PLAN = {
    'id': 5, 'slug': 'mt-hamilton-200k', 'name': 'Mt Hamilton 200K',
    'total_distance_miles': 130.0, 'total_elevation_ft': 8000,
    'rwgps_url': None, 'rwgps_url_team': None, 'start_time': '06:00',
}
_STOPS = [
    {'id': 1, 'location': 'Start', 'distance_miles': 0.0, 'elevation_gain': 0,
     'segment_time_min': 0, 'stop_duration_min': 0, 'stop_type': 'start', 'notes': None, 'stop_order': 1},
    {'id': 2, 'location': 'Mid', 'distance_miles': 60.0, 'elevation_gain': 4000,
     'segment_time_min': 240, 'stop_duration_min': 15, 'stop_type': 'control', 'notes': None, 'stop_order': 2},
    {'id': 3, 'location': 'Finish', 'distance_miles': 130.0, 'elevation_gain': 4000,
     'segment_time_min': 280, 'stop_duration_min': 0, 'stop_type': 'finish', 'notes': None, 'stop_order': 3},
]
_MY_CP = {'id': 42, 'rider_id': 7, 'base_plan_id': 5, 'name': 'My pace'}


def _patches(custom_plan):
    return {
        'routes.riders.get_ride_plan_by_slug': lambda slug: _PLAN,
        'routes.riders.get_ride_plan_stops': lambda pid: [dict(s) for s in _STOPS],
        'routes.riders.get_public_custom_plans': lambda pid: [],
        'routes.riders.fetch_stop_wind': lambda **kw: None,
        'routes.riders.get_custom_plan': lambda rid, pid: custom_plan,
        'routes.riders.get_user_by_id': lambda uid: {'id': 1, 'rider_id': 7},
        'models.get_user_by_id': lambda uid: {'id': 1, 'rider_id': 7},
        'models.get_latest_ride_for_plan': lambda pid: None,
        'models.get_upcoming_rusa_events': lambda: [],
        'models.get_signups_for_ride': lambda eid: [],
    }


def _render(client, custom_plan):
    mgrs = [patch(p, side_effect=v) for p, v in _patches(custom_plan).items()]
    for m in mgrs:
        m.start()
    try:
        with client.session_transaction() as s:
            s['user_id'] = 1
        return client.get('/ride-plan/mt-hamilton-200k?tab=strategies')
    finally:
        for m in mgrs:
            m.stop()


def test_base_view_links_back_to_custom_plan(client):
    html = _render(client, _MY_CP).data.decode()
    # Banner back to the custom plan.
    assert 'You have a custom plan for this route' in html
    assert 'view=custom' in html
    # Strategies-tab notice.
    assert "You've saved a custom plan" in html
    assert 'My pace' in html


def test_base_view_no_link_without_custom_plan(client):
    html = _render(client, None).data.decode()
    assert 'You have a custom plan for this route' not in html
    assert "You've saved a custom plan" not in html
