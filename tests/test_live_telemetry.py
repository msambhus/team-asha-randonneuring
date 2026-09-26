"""Unit tests for services/live_telemetry.py (pure functions, no I/O)."""
from datetime import datetime, timedelta, timezone

import pytest

from services import live_telemetry as tlm
from services.live_telemetry import _LOOP_CLOSURE_M as _LOOP_GAP_MIN_M


def _t(secs):
    return datetime(2026, 6, 23, 14, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=secs)


# straight west→east track at the equator-ish; dist_m increasing
_TRACK = [
    {'lat': 37.0, 'lng': -122.00, 'dist_m': 0.0},
    {'lat': 37.0, 'lng': -121.99, 'dist_m': 889.0},
    {'lat': 37.0, 'lng': -121.98, 'dist_m': 1778.0},
    {'lat': 37.0, 'lng': -121.97, 'dist_m': 2667.0},
]


def test_haversine_known_distance():
    # ~0.01 deg lng at lat 37 ≈ 888 m
    d = tlm.haversine_m(37.0, -122.0, 37.0, -121.99)
    assert 850 < d < 920


def test_project_to_route_picks_nearest():
    dist_m, idx, off_by_m = tlm.project_to_route(37.0, -121.975, _TRACK)
    assert idx == 3
    assert dist_m == 2667.0
    assert off_by_m is not None and off_by_m < 600   # close to the line


def test_project_to_route_reports_off_route_distance():
    # ~0.2 deg north of the track (~22 km away) → large off_by_m
    dist_m, idx, off_by_m = tlm.project_to_route(37.2, -121.99, _TRACK)
    assert off_by_m > tlm.ON_ROUTE_MAX_M


def test_project_to_route_empty():
    assert tlm.project_to_route(1, 2, []) == (None, None, None)


# Out-and-back over the SAME road: lng -121.99 appears twice — once outbound
# (dist 889) and once on the return leg (dist 4445).
_OUT_AND_BACK = [
    {'lat': 37.0, 'lng': -122.00, 'dist_m': 0.0},
    {'lat': 37.0, 'lng': -121.99, 'dist_m': 889.0},
    {'lat': 37.0, 'lng': -121.98, 'dist_m': 1778.0},
    {'lat': 37.0, 'lng': -121.97, 'dist_m': 2667.0},   # turnaround
    {'lat': 37.0, 'lng': -121.98, 'dist_m': 3556.0},
    {'lat': 37.0, 'lng': -121.99, 'dist_m': 4445.0},
    {'lat': 37.0, 'lng': -122.00, 'dist_m': 5334.0},
]


def test_project_to_route_eastbound_picks_outbound_leg():
    # Rider sits on the overlapping line, heading east (~90°) → outbound leg.
    dist_m, idx, off_by_m = tlm.project_to_route(37.0, -121.99, _OUT_AND_BACK,
                                                 heading_deg=90)
    assert idx == 1 and dist_m == 889.0


def test_project_to_route_westbound_picks_return_leg():
    # Same spot, but heading west (~270°) → the return leg, ~4.4 km in.
    dist_m, idx, off_by_m = tlm.project_to_route(37.0, -121.99, _OUT_AND_BACK,
                                                 heading_deg=270)
    assert idx == 5 and dist_m == 4445.0


def test_project_to_route_no_heading_is_legacy_nearest():
    # Without a heading we keep the old behavior: the first global nearest point.
    dist_m, idx, off_by_m = tlm.project_to_route(37.0, -121.99, _OUT_AND_BACK)
    assert idx == 1 and dist_m == 889.0


def test_project_history_follows_out_and_back():
    # Rider rides out to the turnaround then back; at lng -121.99 on the RETURN
    # the temporal walk must pick the return leg (4445 m), not snap to the
    # outbound 889 m a stateless nearest match would give.
    hist = [
        {'lat': 37.0, 'lng': -122.00, 'recorded_at': _t(0)},
        {'lat': 37.0, 'lng': -121.99, 'recorded_at': _t(60)},
        {'lat': 37.0, 'lng': -121.98, 'recorded_at': _t(120)},
        {'lat': 37.0, 'lng': -121.97, 'recorded_at': _t(180)},   # turnaround
        {'lat': 37.0, 'lng': -121.98, 'recorded_at': _t(240)},
        {'lat': 37.0, 'lng': -121.99, 'recorded_at': _t(300)},   # back, on the return leg
    ]
    dist_m, idx, off = tlm.project_history_to_route(hist, _OUT_AND_BACK)
    assert idx == 5 and dist_m == 4445.0
    assert tlm.project_to_route(37.0, -121.99, _OUT_AND_BACK)[0] == 889.0   # stateless is wrong


def test_project_history_is_monotonic_through_gps_backstep():
    # A small backward GPS blip must not reduce the distance already reached.
    hist = [
        {'lat': 37.0, 'lng': -122.00, 'recorded_at': _t(0)},      # 0 m
        {'lat': 37.0, 'lng': -121.98, 'recorded_at': _t(60)},     # 1778 m reached
        {'lat': 37.0, 'lng': -121.99, 'recorded_at': _t(120)},    # blip back toward 889 m
        {'lat': 37.0, 'lng': -121.97, 'recorded_at': _t(180)},    # 2667 m
    ]
    dist_m, idx, off = tlm.project_history_to_route(hist, _TRACK)
    assert dist_m == 2667.0          # never dropped to 889 on the blip


def test_project_history_freezes_at_last_valid_point_while_off_course():
    hist = [
        {'lat': 37.0, 'lng': -122.00, 'recorded_at': _t(0)},
        {'lat': 37.0, 'lng': -121.98, 'recorded_at': _t(60)},
        {'lat': 37.2, 'lng': -121.97, 'recorded_at': _t(120)},
        {'lat': 37.2, 'lng': -121.96, 'recorded_at': _t(180)},
    ]
    dist_m, idx, off = tlm.project_history_to_route(hist, _TRACK)
    assert dist_m == 1778.0
    assert idx == 2
    assert off > tlm.ON_ROUTE_MAX_M


def test_project_history_rejoins_from_frozen_valid_cursor():
    hist = [
        {'lat': 37.0, 'lng': -122.00, 'recorded_at': _t(0)},
        {'lat': 37.0, 'lng': -121.99, 'recorded_at': _t(60)},
        {'lat': 37.2, 'lng': -121.98, 'recorded_at': _t(120)},
        {'lat': 37.0, 'lng': -121.97, 'recorded_at': _t(180)},
    ]
    dist_m, idx, off = tlm.project_history_to_route(hist, _TRACK)
    assert dist_m == 2667.0
    assert idx == 3
    assert off <= tlm.ON_ROUTE_MAX_M


def test_project_history_rejoin_window_grows_from_last_valid_fix():
    track = [
        {'lat': 37.0, 'lng': -122.0 + i * 0.01, 'dist_m': i * 889.0}
        for i in range(12)
    ]
    hist = [
        {'lat': 37.0, 'lng': -122.00, 'recorded_at': _t(0)},
        {'lat': 37.0, 'lng': -121.99, 'recorded_at': _t(60)},
        {'lat': 37.2, 'lng': -121.98, 'recorded_at': _t(120)},
        {'lat': 37.2, 'lng': -121.94, 'recorded_at': _t(300)},
        # Rejoins ~5.3 km after the last valid point. That is outside the fixed
        # 3 km minimum but plausible over the five minutes since the valid fix.
        {'lat': 37.0, 'lng': -121.93, 'recorded_at': _t(360)},
    ]
    dist_m, idx, off = tlm.project_history_to_route(hist, track)
    assert dist_m == 7 * 889.0
    assert idx == 7
    assert off <= tlm.ON_ROUTE_MAX_M


def test_project_history_empty_or_no_track():
    assert tlm.project_history_to_route([], _TRACK) == (None, None, None)
    assert tlm.project_history_to_route(
        [{'lat': 37.0, 'lng': -122.0, 'recorded_at': _t(0)}], []) == (None, None, None)


def test_project_history_reuses_walk_for_per_point_route_positions():
    hist = [
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': _t(0)},
        {'lat': 37.0, 'lng': -121.99, 'recorded_at': _t(60)},
        {'lat': 37.0, 'lng': -121.98, 'recorded_at': _t(120)},
    ]

    result = tlm.project_history_to_route(
        hist, _TRACK, with_start=True, with_point_projections=True)

    dist_m, idx, off, start_dist, start_idx, projections = result
    assert (dist_m, idx, start_dist, start_idx) == (1778.0, 2, 0.0, 0)
    assert off == pytest.approx(0)
    assert [projection[0] for projection in projections] == [0.0, 889.0, 1778.0]


# --- mid-route loop start (permanent begun partway round) ---

def test_route_start_offset_detects_mid_route_start():
    # First fix sits at the -121.98 vertex (1778 m along) → a mid-route start.
    hist = [{'lat': 37.0, 'lng': -121.98, 'recorded_at': _t(0)}]
    offset_m, idx = tlm.route_start_offset_m(hist, _TRACK)
    assert offset_m == 1778.0 and idx == 2


def test_route_start_offset_zero_for_mile0_start():
    # Started at the route's mile 0 → no offset (below START_OFFSET_MIN_M).
    hist = [{'lat': 37.0, 'lng': -122.00, 'recorded_at': _t(0)}]
    assert tlm.route_start_offset_m(hist, _TRACK) == (0.0, 0)


def test_route_start_offset_skips_offroute_warmup_fix():
    # A garbage warm-up fix ~22 km off-route is skipped; the first ON-route fix
    # (at 1778 m) sets the offset.
    hist = [
        {'lat': 37.2, 'lng': -121.99, 'recorded_at': _t(0)},    # far off-route
        {'lat': 37.0, 'lng': -121.98, 'recorded_at': _t(60)},   # on route, 1778 m
    ]
    offset_m, idx = tlm.route_start_offset_m(hist, _TRACK)
    assert offset_m == 1778.0 and idx == 2


def test_route_start_offset_empty():
    assert tlm.route_start_offset_m([], _TRACK) == (0.0, 0)
    assert tlm.route_start_offset_m(
        [{'lat': 37.0, 'lng': -122.0, 'recorded_at': _t(0)}], []) == (0.0, 0)


# A loop whose FINISH vertex sits ~15 m from the START vertex — the case that made
# a stateless nearest-point seed mis-snap a normal mile-0 start onto the finish.
_LOOP = [
    {'lat': 37.00,    'lng': -122.00,    'dist_m': 0.0},      # start
    {'lat': 37.00,    'lng': -121.99,    'dist_m': 889.0},    # east
    {'lat': 37.01,    'lng': -121.99,    'dist_m': 1900.0},   # north
    {'lat': 37.01,    'lng': -122.00,    'dist_m': 2789.0},   # west
    {'lat': 37.0001,  'lng': -122.0001,  'dist_m': 3700.0},   # finish ≈ start
]


def test_route_start_offset_zero_on_loop_started_at_mile0():
    # First fix sits BETWEEN the start and finish vertices, marginally closer to the
    # finish — a stateless nearest match would snap to the finish (3700 m) and
    # wrongly flag a mid-route start. Heading-aware seeding sees the rider heading
    # OUT (east) and keeps them at mile 0.
    hist = [
        {'lat': 37.00007, 'lng': -122.00007, 'recorded_at': _t(0)},
        {'lat': 37.00, 'lng': -121.995, 'recorded_at': _t(60)},   # moved east
    ]
    assert tlm.route_start_offset_m(hist, _LOOP) == (0.0, 0)


def test_project_history_with_start_returns_seed_tuple():
    hist = [{'lat': 37.0, 'lng': -121.98, 'recorded_at': _t(0)}]
    dist_m, idx, off, start_dist, start_idx = tlm.project_history_to_route(
        hist, _TRACK, with_start=True)
    assert start_dist == 1778.0 and start_idx == 2
    assert dist_m == 1778.0 and idx == 2           # single fix: current == start
    assert tlm.project_history_to_route([], _TRACK, with_start=True) == (
        None, None, None, None, None)


def test_distance_progressed_no_offset_is_absolute():
    assert tlm.distance_progressed_m(2667.0, 0, 5334.0) == 2667.0
    assert tlm.distance_progressed_m(None, 0, 5334.0) is None


def test_distance_progressed_subtracts_offset():
    # Started 1778 m in, now at 2667 m → 889 m done.
    assert tlm.distance_progressed_m(2667.0, 1778.0, 5334.0) == 889.0


def test_distance_progressed_wraps_the_loop():
    # Started at 4445 m, now wrapped past the finish to 500 m → 500 − 4445 + 5334.
    assert tlm.distance_progressed_m(500.0, 4445.0, 5334.0) == pytest.approx(1389.0)


_CUM_ASCENT = [0, 100, 250, 400]


def test_ascent_progressed_split_start0_matches_ascent_split():
    assert (tlm.ascent_progressed_split(_CUM_ASCENT, 0, 3, 400)
            == tlm.ascent_split(_CUM_ASCENT, 3, 400))


def test_ascent_progressed_split_mid_route_arc():
    # Climbed from index 1 (100 ft) to index 3 (400 ft) → 300 done, 100 left.
    assert tlm.ascent_progressed_split(_CUM_ASCENT, 1, 3, 400) == (300, 100)


def test_ascent_progressed_split_wrapped_arc():
    # Started at the finish index (3) and wrapped to index 1 → (400−400) + 100 done.
    assert tlm.ascent_progressed_split(_CUM_ASCENT, 3, 1, 400) == (100, 300)


# A simple loop plan: start/finish share the node; controls every 10 mi, 6 min/mi.
_PLAN = [
    {'distance_miles': 0.0,  'cum_time_min': 0,   'arrival_time_min': 0,   'stop_type': 'start',   'location': 'S/F'},
    {'distance_miles': 10.0, 'cum_time_min': 60,  'arrival_time_min': 60,  'stop_type': 'control', 'location': 'C1'},
    {'distance_miles': 20.0, 'cum_time_min': 120, 'arrival_time_min': 120, 'stop_type': 'control', 'location': 'C2'},
    {'distance_miles': 30.0, 'cum_time_min': 180, 'arrival_time_min': 180, 'stop_type': 'finish',  'location': 'S/F'},
]


def test_plan_time_at_interpolates_and_clamps():
    assert tlm.plan_time_at(15.0, _PLAN) == 90.0     # halfway between 60 and 120
    assert tlm.plan_time_at(0.0, _PLAN) == 0.0
    assert tlm.plan_time_at(-5.0, _PLAN) == 0.0       # clamp low
    assert tlm.plan_time_at(99.0, _PLAN) == 180.0     # clamp high
    assert tlm.plan_time_at(5.0, None) is None


def test_rebase_plan_stops_identity_without_offset():
    assert tlm.rebase_plan_stops(_PLAN, 0, 30.0) is _PLAN


def test_rebase_plan_stops_rotates_to_rider_start():
    # Rider began at plan-mile 10. Their frame: that becomes 0; the loop node (mile 0
    # / 30) sits at rider-distance 20; a synthetic finish caps their ride at 30.
    reb = tlm.rebase_plan_stops(_PLAN, 10.0, 30.0)
    by_dist = {s['distance_miles']: s['cum_time_min'] for s in reb}
    assert by_dist[0.0] == 0        # rider's own start, elapsed 0
    assert by_dist[10.0] == 60      # next control, +60 min
    assert by_dist[20.0] == 120     # the loop node
    # Synthetic finish at a full loop on: rider-distance = total, time = full plan.
    fin = tlm.finish_stop(reb)
    assert fin['distance_miles'] == 30.0 and fin['arrival_time_min'] == 180
    # No 'start' stop_type survives (the plan's start is a mid-ride waypoint now).
    assert all((s.get('stop_type') or '') != 'start' for s in reb)


def test_rebase_then_plan_delta_matches_rider_frame():
    # Rider started at mile 10, has ridden 5 mi (rider frame) in 25 min. Plan expects
    # 30 min there (interp 0→60 over 0→10) → +5 min ahead.
    reb = tlm.rebase_plan_stops(_PLAN, 10.0, 30.0)
    assert tlm.plan_delta(5.0, 25, reb) == 5


def test_rebase_fractional_offset_interpolates_start_time():
    # Offset 15 mi lands BETWEEN stops (C1@10=60min, C2@20=120min) → t_start=90 min.
    # C2 (mile 20) → rider-distance 5, elapsed 120−90=30 min; its arrival wraps the
    # same way. Exercises plan_time_at interpolation + arrival rebasing off a stop.
    reb = tlm.rebase_plan_stops(_PLAN, 15.0, 30.0)
    c2 = next(s for s in reb if abs(s['distance_miles'] - 5.0) < 0.01)
    assert c2['cum_time_min'] == 30 and c2['arrival_time_min'] == 30
    # Loop node (mile 0/30, plan-time 0/180) → rider-distance 15, time 90 (=180−90).
    node = next(s for s in reb if abs(s['distance_miles'] - 15.0) < 0.01)
    assert node['cum_time_min'] == 90
    # Duplicate wrap-point stops are collapsed: distances are unique.
    dists = [s['distance_miles'] for s in reb]
    assert len(dists) == len(set(dists))


def test_course_over_ground_eastbound():
    pts = [{'lat': 37.0, 'lng': -122.00}, {'lat': 37.0, 'lng': -121.99}]
    hd = tlm.course_over_ground(pts)
    assert hd is not None and 85 < hd < 95          # due east


def test_course_over_ground_none_when_stopped():
    # All fixes within a few meters → no reliable heading.
    pts = [{'lat': 37.0, 'lng': -122.0000}, {'lat': 37.0, 'lng': -121.99999}]
    assert tlm.course_over_ground(pts) is None


def test_course_over_ground_none_with_one_point():
    assert tlm.course_over_ground([{'lat': 37.0, 'lng': -122.0}]) is None


def test_activity_from_speed():
    assert tlm.activity_from_speed(None) is None
    assert tlm.activity_from_speed(0.0) == 'paused'
    assert tlm.activity_from_speed(1.5) == 'walking'
    assert tlm.activity_from_speed(6.0) == 'cycling'
    assert tlm.activity_from_speed(20.0) == 'driving'


def test_current_stop_duration_uses_trailing_stationary_cluster():
    points = [
        {'lat': 37.0, 'lng': -122.01, 'speed': 6.0, 'recorded_at': _t(0)},
        {'lat': 37.0, 'lng': -122.0, 'speed': 0.0, 'recorded_at': _t(60)},
        # Small GPS drift remains the same stopped point.
        {'lat': 37.0001, 'lng': -122.0001, 'speed': 0.0, 'recorded_at': _t(360)},
        {'lat': 37.0, 'lng': -122.0, 'speed': 0.0, 'recorded_at': _t(660)},
    ]

    assert tlm.current_stop_duration_min(points) == 10.0


def test_current_stop_duration_hidden_while_moving():
    points = [
        {'lat': 37.0, 'lng': -122.0, 'speed': 0.0, 'recorded_at': _t(0)},
        {'lat': 37.0, 'lng': -121.99, 'speed': 5.0, 'recorded_at': _t(60)},
    ]
    assert tlm.current_stop_duration_min(points) is None


def test_stationary_periods_separate_intermediate_stops():
    points = [
        {'lat': 37.0, 'lng': -122.0, 'speed': 0.0, 'recorded_at': _t(0)},
        {'lat': 37.0, 'lng': -122.0, 'speed': 0.0, 'recorded_at': _t(300)},
        {'lat': 37.0, 'lng': -121.99, 'speed': 5.0, 'recorded_at': _t(360)},
        {'lat': 37.0, 'lng': -121.98, 'speed': 0.0, 'recorded_at': _t(420)},
        {'lat': 37.0, 'lng': -121.98, 'speed': 0.0, 'recorded_at': _t(600)},
    ]

    periods = tlm.stationary_periods(points)

    assert [period['duration_min'] for period in periods] == [5.0, 3.0]


def test_stationary_periods_merge_brief_speed_glitch_at_same_control():
    points = [
        {'lat': 37.0, 'lng': -122.0, 'speed': 0.0, 'recorded_at': _t(0)},
        {'lat': 37.0, 'lng': -122.0, 'speed': 0.0, 'recorded_at': _t(360)},
        {'lat': 37.0, 'lng': -122.0, 'speed': 2.0, 'recorded_at': _t(420)},
        {'lat': 37.0, 'lng': -122.0, 'speed': 0.0, 'recorded_at': _t(480)},
        {'lat': 37.0, 'lng': -122.0, 'speed': 0.0, 'recorded_at': _t(720)},
    ]

    periods = tlm.stationary_periods(points)

    assert len(periods) == 1
    assert periods[0]['duration_min'] == 12.0


def test_stationary_periods_merge_overnight_telemetry_gaps_at_same_place():
    points = [
        {'lat': 37.0, 'lng': -122.0, 'speed': 0.0, 'recorded_at': _t(0)},
        {'lat': 37.0, 'lng': -122.0, 'speed': 0.0, 'recorded_at': _t(3600)},
        {'lat': 37.0, 'lng': -122.0, 'speed': 2.0, 'recorded_at': _t(4200)},
        {'lat': 37.0, 'lng': -122.0, 'speed': 0.0, 'recorded_at': _t(5400)},
        {'lat': 37.0, 'lng': -122.0, 'speed': 0.0, 'recorded_at': _t(9000)},
    ]

    periods = tlm.stationary_periods(points)

    assert len(periods) == 1
    assert periods[0]['duration_min'] == 150.0


def test_moving_stopped_long_gap_same_place_is_stopped():
    # A 2-hour gap where the rider didn't move (same spot) = stopped, not moving.
    # (The reported speed is irrelevant across a telemetry gap.)
    pts = [
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': _t(0), 'speed': 5.0},
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': _t(7200), 'speed': 5.0},  # +2h, no move
    ]
    moving, stopped = tlm.moving_stopped(pts)
    assert moving == 0.0 and stopped == 120.0


def test_moving_stopped_long_gap_while_riding_counts_as_moving():
    # Signal dropout on a remote brevet: a 40-min gap where the rider moved
    # ~15 km (~22 km/h) is real riding and must count as moving, not be dropped.
    pts = [
        {'lat': 37.0, 'lng': -122.00, 'recorded_at': _t(0)},
        {'lat': 37.0, 'lng': -121.83, 'recorded_at': _t(2400)},   # ~15 km in 40 min
    ]
    moving, stopped = tlm.moving_stopped(pts)
    assert moving == 40.0 and stopped == 0.0


def test_moving_stopped_long_gap_slow_drift_is_stopped():
    # 45-min gap where the rider drifted only ~1.5 km (~2 km/h) = a rest, not
    # riding — must be stopped, not bridged into moving on the bare floor.
    pts = [
        {'lat': 37.0, 'lng': -122.000, 'recorded_at': _t(0)},
        {'lat': 37.0, 'lng': -121.983, 'recorded_at': _t(2700)},   # ~1.5 km in 45 min
    ]
    moving, stopped = tlm.moving_stopped(pts)
    assert moving == 0.0 and stopped == 45.0


def test_moving_stopped_boundary_gap_trusts_reported_speed():
    # Exactly MAX_GAP_SECONDS is still a "normal" interval: trust reported speed
    # even though the rider didn't change position (e.g. a stationary GPS fix).
    pts = [
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': _t(0), 'speed': 5.0},
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': _t(int(tlm.MAX_GAP_SECONDS)), 'speed': 5.0},
    ]
    moving, stopped = tlm.moving_stopped(pts)
    assert moving == round(tlm.MAX_GAP_SECONDS / 60.0, 1) and stopped == 0.0


def test_moving_stopped_long_gap_implausible_speed_dropped():
    # A 30-min gap implying ~200 km/h (a drive / resumed session / GPS jump) is
    # not counted at all, so it can't inflate moving time.
    pts = [
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': _t(0)},
        {'lat': 37.0, 'lng': -120.8, 'recorded_at': _t(1800)},    # ~107 km in 30 min
    ]
    moving, stopped = tlm.moving_stopped(pts)
    assert moving == 0.0 and stopped == 0.0


def test_remaining_distance():
    assert tlm.remaining_distance_m(2667.0, 889.0) == 1778.0
    assert tlm.remaining_distance_m(1000, 1200) == 0.0   # never negative


def test_ascent_split():
    cum = [0, 100, 250, 400]
    done, left = tlm.ascent_split(cum, 2, 400)
    assert done == 250 and left == 150


def test_headwinds_split_done_and_ahead():
    wind = [
        {'dist_m': 0, 'headwind_kmh': 10},
        {'dist_m': 1000, 'headwind_kmh': 20},
        {'dist_m': 2000, 'headwind_kmh': -6},
    ]
    done, ahead = tlm.headwinds_split(wind, 1000)
    assert done == 15.0          # mean(10, 20)
    assert ahead == -6.0         # only the 2000 point is ahead


def test_headwinds_split_none_when_missing():
    assert tlm.headwinds_split(None, 100) == (None, None)


def test_crosswinds_split_done_and_ahead():
    wind = [
        {'dist_m': 0, 'headwind_kmh': 10, 'crosswind_kmh': 4},
        {'dist_m': 1000, 'headwind_kmh': 20, 'crosswind_kmh': 8},
        {'dist_m': 2000, 'headwind_kmh': -6, 'crosswind_kmh': -10},
    ]
    done, ahead = tlm.crosswinds_split(wind, 1000)
    assert done == 6.0      # mean of 4, 8
    assert ahead == -10.0


def test_crosswinds_split_tolerates_missing_key():
    # Legacy cached context without crosswind_kmh → (None, None), no KeyError.
    wind = [{'dist_m': 0, 'headwind_kmh': 10}]
    assert tlm.crosswinds_split(wind, 1000) == (None, None)


def test_crosswinds_split_none_when_missing():
    assert tlm.crosswinds_split(None, 100) == (None, None)


def test_toughness_remaining_scales_with_climb():
    flat = tlm.toughness_remaining(100, 16093)     # ~10 ft/mi over 10 mi
    steep = tlm.toughness_remaining(2000, 16093)    # ~200 ft/mi over 10 mi
    assert steep > flat
    assert tlm.toughness_remaining(500, 0) == 0.0   # no distance left


def test_plan_delta_ahead_and_behind():
    stops = [
        {'distance_miles': 0, 'cum_time_min': 0},
        {'distance_miles': 60, 'cum_time_min': 300},   # plan: 60 mi in 300 min
    ]
    # At 30 mi the plan expects 150 min. Rider took 120 → 30 min ahead.
    assert tlm.plan_delta(30, 120, stops) == 30
    # Took 180 → 30 min behind.
    assert tlm.plan_delta(30, 180, stops) == -30


def test_plan_delta_does_not_credit_upcoming_overnight_stop():
    stops = [
        {'distance_miles': 235, 'cum_time_min': 1440, 'arrival_time_min': 1200},
        # Four-hour sleep begins only after arrival at mile 417.
        {'distance_miles': 417, 'cum_time_min': 2700, 'arrival_time_min': 2460},
    ]

    # Halfway through the riding segment, expected time is halfway from the
    # previous departure (1440) to overnight arrival (2460), not to departure
    # (2700). The old calculation incorrectly gifted two hours of sleep here.
    assert tlm.plan_time_at(326, stops) == 1950
    assert tlm.plan_delta(326, 2000, stops) == -50


def test_plan_delta_none_without_plan():
    assert tlm.plan_delta(10, 60, []) is None
    assert tlm.plan_delta(10, 60, [{'distance_miles': 0, 'cum_time_min': 0}]) is None


_PLAN_STOPS = [
    {'distance_miles': 0, 'cum_time_min': 0, 'location': 'Start', 'stop_type': 'start'},
    {'distance_miles': 25, 'cum_time_min': 120, 'location': 'Control 1, CA', 'stop_type': 'control'},
    {'distance_miles': 60, 'cum_time_min': 300, 'location': 'Control 2', 'stop_type': 'control'},
    {'distance_miles': 90, 'cum_time_min': 480, 'location': 'Finish', 'stop_type': 'finish'},
]


def test_next_control_returns_first_stop_ahead():
    nc = tlm.next_control(30, _PLAN_STOPS)      # past Control 1 (25 mi)
    assert nc['location'] == 'Control 2'
    assert nc['stop_type'] == 'control'
    assert nc['distance_miles'] == 60
    assert nc['cum_time_min'] == 300
    assert nc['dist_to_go_mi'] == 30.0


def test_next_control_skips_start_and_current_stop():
    # At the very beginning, the next stop is Control 1, never the 'start'.
    assert tlm.next_control(0, _PLAN_STOPS)['location'] == 'Control 1, CA'
    # Standing essentially on Control 1 → next is Control 2 (epsilon skip).
    assert tlm.next_control(25.05, _PLAN_STOPS)['location'] == 'Control 2'


def test_next_control_none_when_past_last_or_no_plan():
    assert tlm.next_control(95, _PLAN_STOPS) is None      # past the finish
    assert tlm.next_control(10, []) is None
    assert tlm.next_control(None, _PLAN_STOPS) is None


# arrival_time_min (= cum − stop_duration) is the REACHING time — distinct from
# cum_time_min for a control with a break, and the basis for the live ETA.
_PLAN_STOPS_WITH_BREAK = [
    {'distance_miles': 0, 'cum_time_min': 0, 'arrival_time_min': 0,
     'location': 'Start', 'stop_type': 'start'},
    {'distance_miles': 25, 'cum_time_min': 135, 'arrival_time_min': 120,
     'location': 'Control 1', 'stop_type': 'control'},   # 15-min break here
    {'distance_miles': 60, 'cum_time_min': 315, 'arrival_time_min': 300,
     'location': 'Control 2', 'stop_type': 'control'},
]


def test_next_control_returns_arrival_time_distinct_from_cum():
    nc = tlm.next_control(10, _PLAN_STOPS_WITH_BREAK)     # heading to Control 1
    assert nc['location'] == 'Control 1'
    # ETA basis is arrival (120), NOT departure (cum 135) — earlier by the break.
    assert nc['arrival_time_min'] == 120
    assert nc['cum_time_min'] == 135
    assert nc['arrival_time_min'] < nc['cum_time_min']


def test_next_control_arrival_falls_back_to_cum_when_absent():
    # Legacy cached stop without arrival_time_min → arrival falls back to cum.
    nc = tlm.next_control(30, _PLAN_STOPS)                # _PLAN_STOPS has no arrival
    assert nc['arrival_time_min'] == nc['cum_time_min'] == 300


# ── required_speed_mph ─────────────────────────────────────────────────────

def test_required_speed_normal():
    # 30 mi to go, plan arrival at 240 min, elapsed 120 → 2 h window → 15 mph.
    mph, behind = tlm.required_speed_mph(30, 240, 120)
    assert mph == 15.0 and behind is False


def test_required_speed_behind_when_window_nonpositive():
    # Arrival already passed → behind, no negative / no divide-by-zero.
    mph, behind = tlm.required_speed_mph(10, 100, 130)
    assert mph is None and behind is True


def test_required_speed_zero_window_is_behind_not_zerodiv():
    # Exactly at the arrival time (window == 0) must not raise ZeroDivisionError.
    mph, behind = tlm.required_speed_mph(5, 120, 120)
    assert mph is None and behind is True


def test_required_speed_none_inputs():
    assert tlm.required_speed_mph(None, 100, 50) == (None, False)
    assert tlm.required_speed_mph(10, None, 50) == (None, False)
    assert tlm.required_speed_mph(10, 100, None) == (None, False)


# ── time_banked_cutoff_min ─────────────────────────────────────────────────

def test_time_banked_cutoff_positive_and_negative():
    # 100 mi into a 200 mi / 20 h ride → cutoff clock 600 min at that distance.
    assert tlm.time_banked_cutoff_min(100, 500, 200, 20) == 100   # 100 min in hand
    assert tlm.time_banked_cutoff_min(100, 700, 200, 20) == -100  # 100 min over


def test_time_banked_cutoff_none_without_cutoff_or_distance():
    assert tlm.time_banked_cutoff_min(100, 500, 200, None) is None   # no cutoff
    assert tlm.time_banked_cutoff_min(100, 500, 0, 20) is None       # no plan distance
    assert tlm.time_banked_cutoff_min(None, 500, 200, 20) is None
    assert tlm.time_banked_cutoff_min(100, None, 200, 20) is None
    assert tlm.time_banked_cutoff_min(0, 0, 200, 20) is None


def test_time_banked_cutoff_uses_relaxed_long_brevet_band_after_600k():
    total_mi = 1200 / 1.609344
    at_800_mi = 800 / 1.609344
    assert tlm.time_banked_cutoff_min(
        at_800_mi, 3000, total_mi, 90,
        event_distance_km=1200) == 450


# A short 3-point profile: flat, then a 10 m climb over 100 m (10% grade).
_GRADE_TRACK = [
    {'dist_m': 0, 'e_m': 100.0},
    {'dist_m': 100, 'e_m': 100.0},
    {'dist_m': 200, 'e_m': 110.0},
    {'dist_m': 300, 'e_m': 120.0},
]


def test_grade_at_positive_on_climb():
    # Around index 2 (200 m), the window spans a rising profile → positive grade.
    g = tlm.grade_at(_GRADE_TRACK, 2, min_window_m=100)
    assert g is not None and g > 0


def test_grade_at_negative_on_descent():
    descent = [
        {'dist_m': 0, 'e_m': 120.0},
        {'dist_m': 100, 'e_m': 110.0},
        {'dist_m': 200, 'e_m': 100.0},
    ]
    assert tlm.grade_at(descent, 1, min_window_m=100) < 0


def test_grade_at_none_without_elevation():
    no_elev = [{'dist_m': 0, 'e_m': None}, {'dist_m': 100, 'e_m': None}]
    assert tlm.grade_at(no_elev, 0) is None
    assert tlm.grade_at([], 0) is None
    assert tlm.grade_at(_GRADE_TRACK, None) is None


def test_moving_stopped_with_reported_speed():
    pts = [
        {'lat': 37, 'lng': -122, 'recorded_at': _t(0), 'speed': 5.0},
        {'lat': 37, 'lng': -122, 'recorded_at': _t(60), 'speed': 5.0},   # moving 60s
        {'lat': 37, 'lng': -122, 'recorded_at': _t(120), 'speed': 0.0},  # stopped 60s
    ]
    moving, stopped = tlm.moving_stopped(pts)
    assert moving == 1.0 and stopped == 1.0


def test_moving_stopped_derives_speed_from_positions():
    # No 'speed' key → derive from displacement. Big move then no move.
    pts = [
        {'lat': 37.0, 'lng': -122.00, 'recorded_at': _t(0)},
        {'lat': 37.0, 'lng': -121.99, 'recorded_at': _t(60)},   # ~888m/60s = moving
        {'lat': 37.0, 'lng': -121.99, 'recorded_at': _t(120)},  # no move = stopped
    ]
    moving, stopped = tlm.moving_stopped(pts)
    assert moving == 1.0 and stopped == 1.0


def test_moving_stopped_too_few_points():
    assert tlm.moving_stopped([]) == (0.0, 0.0)
    assert tlm.moving_stopped([{'lat': 1, 'lng': 2, 'recorded_at': _t(0)}]) == (0.0, 0.0)


def test_latest_speed_prefers_reported():
    pts = [{'lat': 37, 'lng': -122, 'recorded_at': _t(0), 'speed': 6.5}]
    assert tlm.latest_speed_ms(pts) == 6.5


def test_latest_speed_derived_when_absent():
    pts = [
        {'lat': 37.0, 'lng': -122.00, 'recorded_at': _t(0)},
        {'lat': 37.0, 'lng': -121.99, 'recorded_at': _t(60)},
    ]
    s = tlm.latest_speed_ms(pts)
    assert 13 < s < 16   # ~888m / 60s ≈ 14.8 m/s


def test_latest_speed_none_when_empty():
    assert tlm.latest_speed_ms([]) is None


def test_build_trail_drops_off_route_points():
    history = [
        {'lat': 37.0, 'lng': -122.00, 'recorded_at': _t(0)},    # on route
        {'lat': 37.2, 'lng': -121.99, 'recorded_at': _t(30)},   # ~22 km off → dropped
        {'lat': 37.0, 'lng': -121.98, 'recorded_at': _t(60)},   # on route
    ]
    trail = tlm.build_trail(history, _TRACK)
    assert trail == [[-122.0, 37.0], [-121.98, 37.0]]   # [lng,lat], off-route removed


def test_build_trail_without_route_keeps_all():
    history = [
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': _t(0)},
        {'lat': 37.5, 'lng': -122.5, 'recorded_at': _t(30)},
    ]
    assert tlm.build_trail(history, None) == [[-122.0, 37.0], [-122.5, 37.5]]


def test_build_trail_downsamples_keeps_order_and_newest():
    history = [{'lat': 37.0, 'lng': -122.0 + i * 0.0001, 'recorded_at': _t(i)} for i in range(400)]
    trail = tlm.build_trail(history, None, max_points=40)
    assert 0 < len(trail) <= 50                 # downsampled, not 400
    assert trail[0] == [-122.0, 37.0]           # oldest first
    assert trail[-1] == [-122.0 + 399 * 0.0001, 37.0]   # newest always included
    lngs = [c[0] for c in trail]
    assert lngs == sorted(lngs)                 # order preserved


def test_build_trail_empty():
    assert tlm.build_trail([], _TRACK) == []


def test_build_actual_trail_preserves_off_route_detour():
    history = [
        {'lat': 37.000, 'lng': -122.000, 'recorded_at': _t(0)},
        {'lat': 37.020, 'lng': -121.995, 'recorded_at': _t(30)},
        {'lat': 37.000, 'lng': -121.990, 'recorded_at': _t(60)},
    ]

    trail = tlm.build_actual_trail(history)

    assert trail == [
        [-122.0, 37.0], [-121.995, 37.02], [-121.99, 37.0]]


def test_build_actual_trail_simplifies_redundancy_but_keeps_real_turns():
    history = [
        {'lat': 37.0, 'lng': -122.0 + i * 0.0001, 'recorded_at': _t(i)}
        for i in range(100)
    ]
    history.insert(50, {
        'lat': 37.01, 'lng': -121.995, 'recorded_at': _t(49.5)})

    trail = tlm.build_actual_trail(history, max_points=50, tolerance_m=5)

    assert len(trail) <= 50
    assert trail[0] == [-122.0, 37.0]
    assert trail[-1] == pytest.approx([-121.9901, 37.0])
    assert any(lat == pytest.approx(37.01) for _lng, lat in trail)


# ── Loop start/finish seam — issue #919 ──────────────────────────────────────
# A loop permanent begun partway round must cross the route's start/finish seam.
# The trajectory walk used `range(cur, n)` and so could never advance past the
# final track point: progress froze at the seam (observed live on Iron Horse 200k
# — stuck at 4.4 mi while the rider was 18 mi in, reported 17.8 km off route).

def _square_loop(step_m=100.0):
    """A closed square loop, ~100 m between points, dist_m ascending then closing.

    Built from a real lat/lng square so haversine distances are consistent with
    the dist_m column the walk compares against.
    """
    lat0, lng0 = 37.0, -122.0
    dlat = step_m / 111320.0
    dlng = step_m / (111320.0 * 0.7986355)  # cos(37°)
    corners = []
    for i in range(20):                      # east
        corners.append((lat0, lng0 + i * dlng))
    for i in range(20):                      # north
        corners.append((lat0 + i * dlat, lng0 + 19 * dlng))
    for i in range(20):                      # west
        corners.append((lat0 + 19 * dlat, lng0 + (19 - i) * dlng))
    for i in range(20):                      # south, back to the start
        corners.append((lat0 + (19 - i) * dlat, lng0))
    corners.append((lat0, lng0))             # close the loop exactly
    track, run = [], 0.0
    prev = None
    for lat, lng in corners:
        if prev is not None:
            run += tlm.haversine_m(prev[0], prev[1], lat, lng)
        track.append({'lat': lat, 'lng': lng, 'dist_m': run})
        prev = (lat, lng)
    return track


def test_route_is_loop_detects_a_closed_course():
    assert tlm.route_is_loop(_square_loop()) is True
    assert tlm.route_is_loop(_TRACK) is False, 'a point-to-point route is not a loop'


def _ride_from(track, start_i, count, secs=30):
    """History following the track forward from `start_i`, wrapping the loop."""
    n = len(track) - 1                       # last point duplicates the first
    return [{'lat': track[(start_i + k) % n]['lat'],
             'lng': track[(start_i + k) % n]['lng'],
             'recorded_at': _t(k * secs)} for k in range(count)]


def test_progress_continues_past_the_start_finish_seam():
    """The defect. Starting at 3/4 round and riding on must keep accumulating."""
    track = _square_loop()
    total = track[-1]['dist_m']
    start_i = 60                             # three quarters round
    history = _ride_from(track, start_i, 30)  # crosses the seam
    dist_m, idx, off_by = tlm.project_history_to_route(history, track)
    offset, _ = tlm.route_start_offset_m(history, track)
    progressed = tlm.distance_progressed_m(dist_m, offset, total)
    expected = 29 * 100.0                    # 29 hops of ~100 m
    assert off_by is not None and off_by < 50, 'rider should read as on-route'
    assert progressed > expected * 0.8, (
        f'progress froze at the seam: {progressed:.0f} m, expected ~{expected:.0f} m')


def test_the_seam_crossing_is_monotonic_for_every_fix():
    """Per-fix projections must not jump backwards as the seam is crossed."""
    track = _square_loop()
    total = track[-1]['dist_m']
    history = _ride_from(track, 60, 30)
    *_, projections = tlm.project_history_to_route(
        history, track, with_start=True, with_point_projections=True)
    offset, _ = tlm.route_start_offset_m(history, track)
    seq = [(d - offset) % total for d, _i, _o in projections if d is not None]
    assert len(seq) >= 25, 'most fixes should project cleanly'
    assert all(b >= a - 1.0 for a, b in zip(seq, seq[1:])), f'non-monotonic: {seq}'


def _collinear_loop(step_m=100.0):
    """A loop whose LAST segment approaches the start on the same bearing as its
    first segment leaves it — the real Iron Horse geometry at Alamo Plaza.

    Both candidate seed legs then point the same way, so heading cannot separate
    the start vertex from the finish vertex. `_square_loop` does not reproduce the
    defect because its start leg runs east and its finish leg runs north.
    """
    lat0, lng0 = 37.0, -122.0
    dlat = step_m / 111320.0
    dlng = step_m / (111320.0 * 0.7986355)
    # The first vertex sits 0.3 steps (~30 m) east of the physical start corner and
    # the closing vertex lands ON it. Downsampling routinely produces this, and it
    # is what makes the FINISH vertex the nearest one to a rider waiting at the
    # corner — exactly the real Alamo Plaza case, where the seed picked mile 124.5.
    pts = [(lat0, lng0 + (i + 0.3) * dlng) for i in range(15)]  # east from the start
    pts += [(lat0 + i * dlat, lng0 + 14 * dlng) for i in range(1, 15)]   # north
    pts += [(lat0 + 14 * dlat, lng0 + (14 - i) * dlng) for i in range(1, 30)]  # west, past
    pts += [(lat0 + (14 - i) * dlat, lng0 - 15 * dlng) for i in range(1, 15)]  # south
    pts += [(lat0, lng0 + (i - 15) * dlng) for i in range(1, 16)]  # EAST back to the corner
    track, run, prev = [], 0.0, None
    for lat, lng in pts:
        if prev is not None:
            run += tlm.haversine_m(prev[0], prev[1], lat, lng)
        track.append({'lat': lat, 'lng': lng, 'dist_m': run})
        prev = (lat, lng)
    return track


def test_a_stationary_loop_start_seeds_at_the_start_not_the_finish():
    """A rider idling at the start/finish point of a loop whose two legs run the
    same way through that point, so heading cannot disambiguate them.

    CHARACTERISATION, not a regression guard for the seam fix: the existing
    `project_to_route` tie-break already resolves this fixture to the start vertex.
    The live failure seeded on the FINISH vertex instead, and no synthetic fixture
    reproduced that, so an explicit remap was written and then removed — see the
    note in project_history_to_route on why it earns nothing once progress is
    measured in the rider's frame.
    """
    track = _collinear_loop()
    total = track[-1]['dist_m']
    assert tlm.route_is_loop(track), 'fixture must be a closed loop'
    # sanity: the two legs really are ambiguous by bearing
    out = tlm.bearing_deg(track[0]['lat'], track[0]['lng'], track[1]['lat'], track[1]['lng'])
    back = tlm.bearing_deg(track[-2]['lat'], track[-2]['lng'], track[-1]['lat'], track[-1]['lng'])
    assert tlm.angle_diff_deg(out, back) < 20, 'fixture should be collinear at the start'

    # Waiting at the physical corner, which IS the closing vertex — so plain
    # nearest-point matching resolves to the finish, not the start.
    corner = track[-1]
    nearest_end = tlm.haversine_m(corner['lat'], corner['lng'], track[-1]['lat'], track[-1]['lng'])
    nearest_start = tlm.haversine_m(corner['lat'], corner['lng'], track[0]['lat'], track[0]['lng'])
    assert nearest_end < nearest_start, 'fixture must make the FINISH vertex nearest'

    idle = [{'lat': corner['lat'], 'lng': corner['lng'], 'recorded_at': _t(k * 10)}
            for k in range(6)]
    moving = [{'lat': track[k]['lat'], 'lng': track[k]['lng'],
               'recorded_at': _t(60 + k * 30)} for k in range(1, 12)]
    offset, _offset_i = tlm.route_start_offset_m(idle + moving, track)
    assert offset < total * 0.05, (
        f'seeded at {offset:.0f} m of {total:.0f} — that is the finish vertex')
    dist_m, _idx, _off = tlm.project_history_to_route(idle + moving, track)
    progressed = tlm.distance_progressed_m(dist_m, offset, total)
    assert progressed > 900, f'progress should follow the moving fixes, got {progressed:.0f} m'


def test_progress_tracks_expected_distance_right_through_the_seam():
    """Not just monotonic — ACCURATE across the seam.

    Without the wrapping forward window the cursor sticks on the final track point
    (which coincides with the start) until the rider is beyond the on-route
    tolerance, so progress lags by up to that tolerance before the global-rejoin
    path recovers it. This pins the local walk itself.
    """
    track = _square_loop()
    total = track[-1]['dist_m']
    history = _ride_from(track, 60, 30)
    offset, _ = tlm.route_start_offset_m(history, track)
    *_, projections = tlm.project_history_to_route(
        history, track, with_start=True, with_point_projections=True)
    worst = 0.0
    for k, (d, _i, _o) in enumerate(projections):
        if d is None:
            continue
        worst = max(worst, abs(((d - offset) % total) - k * 100.0))
    assert worst < 250, f'progress drifted up to {worst:.0f} m from the true distance'


def test_a_point_to_point_route_never_walks_backwards_to_its_finish():
    """The index walk must not wrap on a point-to-point course. A rider near the
    start with the finish only a few hundred metres 'behind' in modulo arithmetic
    must not be snapped forward to the end of the route."""
    track = _TRACK
    total = track[-1]['dist_m']
    history = [{'lat': 37.0, 'lng': -122.00, 'recorded_at': _t(0)},
               {'lat': 37.0, 'lng': -121.999, 'recorded_at': _t(60)}]
    dist_m, idx, _off = tlm.project_history_to_route(history, track)
    assert dist_m is not None and dist_m < total * 0.5, (
        f'walked to {dist_m} of {total} — the walk wrapped on a point-to-point route')


def _near_loop():
    """A long course whose finish comes back NEAR the start but not close enough to
    count as closed. Wrapping here would be wrong, and the two ends are close
    enough geographically that an ungated index wrap would happily jump between
    them."""
    track = [dict(p) for p in _square_loop()]
    dlng = 400.0 / (111320.0 * 0.7986355)
    # WEST of the start — nothing else on the square lies there, so this point is
    # 400 m from the start and far from every other part of the route. Displacing it
    # east would land it exactly on the outbound leg, where the forward walk finds
    # it legitimately and the fixture proves nothing.
    track[-1] = {**track[-1], 'lng': track[-1]['lng'] - dlng}   # open the loop by 400 m
    return track


def test_a_near_loop_is_not_treated_as_closed():
    track = _near_loop()
    assert tlm.route_is_loop(track) is False
    assert tlm.haversine_m(track[0]['lat'], track[0]['lng'],
                           track[-1]['lat'], track[-1]['lng']) > _LOOP_GAP_MIN_M


def test_an_open_course_never_jumps_from_its_start_to_its_finish():
    """A rider at the start of an OPEN course, with a fix landing on the finish
    (400 m away, but 8 km along the route). The forward window cannot legitimately
    reach it; an ungated backward index wrap reaches it in one step and reports the
    whole route as complete."""
    track = _near_loop()
    total = track[-1]['dist_m']
    history = [{'lat': track[0]['lat'], 'lng': track[0]['lng'], 'recorded_at': _t(0)},
               {'lat': track[1]['lat'], 'lng': track[1]['lng'], 'recorded_at': _t(30)},
               {'lat': track[-1]['lat'], 'lng': track[-1]['lng'], 'recorded_at': _t(60)}]
    dist_m, _idx, _off, _sd, _si, projections = tlm.project_history_to_route(
        history, track, with_start=True, with_point_projections=True)
    assert dist_m is not None and dist_m < total * 0.5, (
        f'jumped to {dist_m:.0f} m of {total:.0f} — the walk wrapped on an open course')
    # The returned distance is additionally protected by the monotonic guard, so
    # assert the PER-FIX projection too: those indices feed the ascent and wind
    # splits and the stop-day classification, and a wrapped match corrupts them
    # even when the headline distance survives.
    last_dist, last_idx, _ = projections[-1]
    assert last_idx is None or last_idx < len(track) - 5, (
        f'final fix projected to idx {last_idx} of {len(track) - 1} — wrapped to the finish')
    assert last_dist is None or last_dist < total * 0.5, (
        f'final fix projected to {last_dist:.0f} m of {total:.0f}')
