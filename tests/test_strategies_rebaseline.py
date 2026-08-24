"""Tests for the Strategies-tab rebaseline, per-segment signals, and the
custom-plan publish toggle (TA-138).

`compute_pace_strategies` is a pure function — tested directly. The publish
endpoint is tested through the Flask client with the DB layer mocked.
"""
from unittest.mock import patch

from routes.riders import compute_pace_strategies


_PLAN = {'total_distance_miles': 130.0}


def _stops(seg_times):
    """3 stops (start, control@60mi/4000ft, finish@130mi/2000ft) with the given
    per-segment moving times (minutes)."""
    return [
        {'location': 'Start', 'stop_type': 'start', 'distance_miles': 0.0,
         'elevation_gain': 0, 'segment_time_min': seg_times[0], 'stop_duration_min': 0},
        {'location': 'Big Control', 'stop_type': 'control', 'distance_miles': 60.0,
         'elevation_gain': 4000, 'segment_time_min': seg_times[1], 'stop_duration_min': 15},
        {'location': 'Finish', 'stop_type': 'finish', 'distance_miles': 130.0,
         'elevation_gain': 2000, 'segment_time_min': seg_times[2], 'stop_duration_min': 0},
    ]


# Keyed by rounded cumulative distance (route-constant), matching the route.
_SEG_META = {
    0.0: {'tough_class': '', 'tough_known': False, 'wind_known': False,
          'wind_label': '', 'headwind_mph': 0, 'wind_arrow_deg': 0},
    60.0: {'tough_class': 't4', 'tough_known': True, 'wind_known': True,
           'wind_label': 'Head', 'headwind_mph': 10, 'wind_arrow_deg': 170},
    130.0: {'tough_class': 't1', 'tough_known': True, 'wind_known': True,
            'wind_label': 'Tail', 'headwind_mph': -8, 'wind_arrow_deg': 10},
}


# ── Base view (no custom plan) is unchanged ──────────────────────────

def test_base_view_returns_comfort_standard_push():
    paces = compute_pace_strategies(_stops([0, 240, 280]), _PLAN, '06:00', 40)
    assert [p['id'] for p in paces] == ['comfort', 'standard', 'push']
    # Standard is the team plan and the only recommended one.
    std = next(p for p in paces if p['id'] == 'standard')
    assert std['recommended'] is True
    assert sum(1 for p in paces if p['recommended']) == 1


def test_multiday_standard_summary_aggregates_sleep_and_uses_final_bank():
    stops = [
        {'location': 'Start', 'stop_type': 'start', 'distance_miles': 0.0,
         'segment_time_min': 0, 'stop_duration_min': 0},
        {'location': 'Day 1 Finish', 'stop_type': 'control', 'distance_miles': 250.0,
         'segment_time_min': 600, 'stop_duration_min': 180},
        {'location': 'Day 2 Finish', 'stop_type': 'control', 'distance_miles': 500.0,
         'segment_time_min': 600, 'stop_duration_min': 240},
        {'location': 'Finish', 'stop_type': 'finish', 'distance_miles': 745.2,
         'segment_time_min': 600, 'stop_duration_min': 0},
    ]
    plan = {'total_distance_miles': 745.2, 'distance_km': 1200}

    standard = next(
        p for p in compute_pace_strategies(stops, plan, '04:00', 90)
        if p['id'] == 'standard'
    )

    assert standard['sleep'] == '7:00'
    assert standard['total'] == '37:00'
    assert standard['bank'] == '+52:57'
    assert standard['stops'][-1]['bank'] == '+52:57'


# ── Rebaseline when viewing a custom plan ────────────────────────────

def test_rebaseline_custom_faster_puts_team_on_comfort_side():
    custom = _stops([0, 200, 240])   # faster (440 moving)
    base = _stops([0, 240, 280])     # slower team (520 moving)
    paces = compute_pace_strategies(custom, _PLAN, '06:00', 40,
                                    base_stops=base, your_plan_name='My pace',
                                    seg_meta=_SEG_META)
    # Your plan is quicker -> team is the slower (left) card, push on the right.
    assert [p['id'] for p in paces] == ['team', 'yours', 'push']
    assert paces[1]['name'] == 'My pace'


def test_rebaseline_custom_slower_puts_team_on_push_side():
    custom = _stops([0, 280, 320])   # slower (600 moving)
    base = _stops([0, 240, 280])     # faster team (520 moving)
    paces = compute_pace_strategies(custom, _PLAN, '06:00', 40,
                                    base_stops=base, your_plan_name='My pace',
                                    seg_meta=_SEG_META)
    # Your plan is slower -> comfort on the left, team is the faster (right) card.
    assert [p['id'] for p in paces] == ['comfort', 'yours', 'team']
    assert paces[1]['name'] == 'My pace'


def test_rebaseline_team_card_gets_the_badge_not_your_plan():
    paces = compute_pace_strategies(_stops([0, 200, 240]), _PLAN, '06:00', 40,
                                    base_stops=_stops([0, 240, 280]),
                                    your_plan_name='My pace', seg_meta=_SEG_META)
    team = next(p for p in paces if p['id'] == 'team')
    yours = next(p for p in paces if p['id'] == 'yours')
    assert team['recommended'] is True       # ★ TEAM PLAN badge on the team card
    assert yours['recommended'] is False      # NOT on the custom plan


# ── Per-segment signals on every card's stops ────────────────────────

def test_pace_stops_carry_segment_signals():
    paces = compute_pace_strategies(_stops([0, 200, 240]), _PLAN, '06:00', 40,
                                    base_stops=_stops([0, 240, 280]),
                                    your_plan_name='My pace', seg_meta=_SEG_META)
    your_control = next(s for s in paces[1]['stops'] if s['i'] == 1)
    # 60 mi / 4000 ft -> 67 ft/mi; 60 mi over 200 min -> 18.0 mph.
    assert your_control['fpm'] == 67
    assert your_control['seg_speed'] == 18.0
    assert your_control['seg_speed_known'] is True
    # Wind + toughness come from seg_meta (route-constant) by index.
    assert your_control['tough_class'] == 't4'
    assert your_control['tough_known'] is True
    assert your_control['wind_known'] is True
    assert your_control['headwind_mph'] == 10


def test_base_view_stops_also_carry_signals():
    paces = compute_pace_strategies(_stops([0, 240, 280]), _PLAN, '06:00', 40,
                                    seg_meta=_SEG_META)
    control = next(s for s in paces[1]['stops'] if s['i'] == 1)
    assert control['fpm'] == 67
    assert control['seg_speed_known'] is True
    assert control['tough_class'] == 't4'


def test_seg_meta_aligns_by_distance_when_stop_counts_differ():
    # Custom plan HID a stop the base plan has (base has an extra @90mi). Because
    # seg_meta is keyed by distance, the team card's control@60 still gets the
    # right toughness — index-based keying would have mis-assigned it.
    custom = _stops([0, 200, 240])                       # 3 stops: 0, 60, 130
    base = [
        {'location': 'Start', 'stop_type': 'start', 'distance_miles': 0.0,
         'elevation_gain': 0, 'segment_time_min': 0, 'stop_duration_min': 0},
        {'location': 'Big Control', 'stop_type': 'control', 'distance_miles': 60.0,
         'elevation_gain': 4000, 'segment_time_min': 240, 'stop_duration_min': 15},
        {'location': 'Extra waypoint', 'stop_type': 'waypoint', 'distance_miles': 90.0,
         'elevation_gain': 1000, 'segment_time_min': 120, 'stop_duration_min': 0},
        {'location': 'Finish', 'stop_type': 'finish', 'distance_miles': 130.0,
         'elevation_gain': 1000, 'segment_time_min': 160, 'stop_duration_min': 0},
    ]
    paces = compute_pace_strategies(custom, _PLAN, '06:00', 40,
                                    base_stops=base, your_plan_name='My pace',
                                    seg_meta=_SEG_META)
    team = next(p for p in paces if p['id'] == 'team')
    team_control = next(s for s in team['stops'] if round(s['cumul_mi'], 1) == 60.0)
    assert team_control['tough_class'] == 't4'        # right location, not shifted
    # The base-only @90mi stop simply has no signal (graceful), not a wrong one.
    extra = next(s for s in team['stops'] if round(s['cumul_mi'], 1) == 90.0)
    assert extra['tough_known'] is False


# ── Publish toggle endpoint ──────────────────────────────────────────

_PUB_PLAN = {'id': 5, 'slug': 'mt-hamilton-200k'}


def _pub_patches(extras=None):
    base = {
        'routes.riders.get_ride_plan_by_slug': lambda slug: _PUB_PLAN,
        'routes.riders.get_user_by_id': lambda uid: {'id': 1, 'rider_id': 7},
        'models.get_user_by_id': lambda uid: {'id': 1, 'rider_id': 7},
        'routes.riders.get_custom_plan': lambda rid, pid: {'id': 42, 'rider_id': 7},
        'routes.riders.update_custom_plan_settings': lambda *a, **k: True,
    }
    if extras:
        base.update(extras)
    return base


def _run(patches, action):
    mgrs = [patch(p, side_effect=v) if callable(v) else patch(p, return_value=v)
            for p, v in patches.items()]
    for m in mgrs:
        m.start()
    try:
        return action()
    finally:
        for m in mgrs:
            m.stop()


def test_publish_toggle_requires_login(client):
    resp = _run(_pub_patches({'routes.riders.get_user_by_id': lambda uid: None}),
                lambda: client.post('/ride-plan/mt-hamilton-200k/v2/custom/public',
                                    json={'is_public': True}))
    assert resp.status_code == 401


def test_publish_toggle_owner_can_publish(client):
    with client.session_transaction() as s:
        s['user_id'] = 1
    captured = {}

    def _upd(cp_id, rider_id, **kw):
        captured['cp_id'] = cp_id
        captured['rider_id'] = rider_id
        captured['is_public'] = kw.get('is_public')
        return True

    resp = _run(_pub_patches({'routes.riders.update_custom_plan_settings': _upd}),
                lambda: client.post('/ride-plan/mt-hamilton-200k/v2/custom/public',
                                    json={'is_public': True}))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    assert body['is_public'] is True
    # The update was scoped to the owner's rider_id (security).
    assert captured == {'cp_id': 42, 'rider_id': 7, 'is_public': True}


def test_publish_toggle_no_custom_plan_404(client):
    with client.session_transaction() as s:
        s['user_id'] = 1
    resp = _run(_pub_patches({'routes.riders.get_custom_plan': lambda rid, pid: None}),
                lambda: client.post('/ride-plan/mt-hamilton-200k/v2/custom/public',
                                    json={'is_public': True}))
    assert resp.status_code == 404


def test_publish_toggle_no_rider_403(client):
    with client.session_transaction() as s:
        s['user_id'] = 1
    extras = {
        'routes.riders.get_user_by_id': lambda uid: {'id': 1, 'rider_id': None},
        'models.get_user_by_id': lambda uid: {'id': 1, 'rider_id': None},
    }
    resp = _run(_pub_patches(extras),
                lambda: client.post('/ride-plan/mt-hamilton-200k/v2/custom/public',
                                    json={'is_public': True}))
    assert resp.status_code == 403


def test_publish_toggle_unpublish_round_trips(client):
    with client.session_transaction() as s:
        s['user_id'] = 1
    captured = {}

    def _upd(cp_id, rider_id, **kw):
        captured['is_public'] = kw.get('is_public')
        return True

    resp = _run(_pub_patches({'routes.riders.update_custom_plan_settings': _upd}),
                lambda: client.post('/ride-plan/mt-hamilton-200k/v2/custom/public',
                                    json={'is_public': False}))
    assert resp.status_code == 200
    assert resp.get_json()['is_public'] is False
    assert captured['is_public'] is False
