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


def test_moving_stopped_ignores_large_gaps():
    # A 2-hour gap between two points must NOT count as moving/stopped time.
    pts = [
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': _t(0), 'speed': 5.0},
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': _t(7200), 'speed': 5.0},  # +2h gap
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
