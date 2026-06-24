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
    dist_m, idx = tlm.project_to_route(37.0, -121.975, _TRACK)
    assert idx == 3
    assert dist_m == 2667.0


def test_project_to_route_empty():
    assert tlm.project_to_route(1, 2, []) == (None, None)


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
