"""Unit tests for services/live_telemetry.py (pure functions, no I/O)."""
from datetime import datetime, timedelta, timezone

import pytest

from services import live_telemetry as tlm


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


def test_project_history_empty_or_no_track():
    assert tlm.project_history_to_route([], _TRACK) == (None, None, None)
    assert tlm.project_history_to_route(
        [{'lat': 37.0, 'lng': -122.0, 'recorded_at': _t(0)}], []) == (None, None, None)


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
