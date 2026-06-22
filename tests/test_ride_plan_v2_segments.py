"""Unit tests for the v2 itinerary per-segment metrics + toughness.

Covers the pure helpers (`_compute_segment_toughness`, `_toughness_class`) and
the new fields wired through `_to_v2_stops` (segment time/speed, signed
headwind, and the per-segment toughness score). These are pure functions, so no
Flask app or DB is needed.
"""
from routes.riders import (
    _compute_segment_toughness,
    _toughness_class,
    _to_v2_stops,
)


# ── _compute_segment_toughness ───────────────────────────────────────

def test_toughness_zero_when_flat_and_calm():
    assert _compute_segment_toughness(0, 0) == 0.0


def test_toughness_climbing_only_caps_at_5():
    # 80 ft/mi -> 5.0 (the climbing ceiling); steeper does not exceed it.
    assert _compute_segment_toughness(80, 0) == 5.0
    assert _compute_segment_toughness(128, 0) == 5.0


def test_toughness_headwind_adds_up_to_6():
    # 48 ft/mi -> climb 3.0. +9 mph headwind -> +3.0. +18 mph -> +6.0 cap.
    assert _compute_segment_toughness(48, 9) == 6.0
    assert _compute_segment_toughness(48, 18) == 9.0
    assert _compute_segment_toughness(48, 40) == 9.0  # wind term capped at +6


def test_toughness_headwind_weighted_higher_than_gradient():
    # A stiff headwind alone outscores even the steepest possible climb
    # (headwind cap 6.0 > climbing cap 5.0).
    assert _compute_segment_toughness(0, 18) > _compute_segment_toughness(300, 0)
    # And a stiff 15 mph headwind outranks a moderate 60 ft/mi climb.
    assert _compute_segment_toughness(0, 15) > _compute_segment_toughness(60, 0)


def test_toughness_tailwind_eases_up_to_1_5():
    # Negative headwind = tailwind, eases the score, floored at -1.5.
    assert _compute_segment_toughness(48, -12) == 1.5   # 3.0 - 1.5
    assert _compute_segment_toughness(48, -100) == 1.5  # tailwind term floored


def test_toughness_headwind_raises_vs_calm():
    calm = _compute_segment_toughness(40, 0)
    windy = _compute_segment_toughness(40, 15)
    assert windy > calm


def test_toughness_never_exceeds_10_or_below_0():
    assert _compute_segment_toughness(200, 200) == 10.0
    assert _compute_segment_toughness(0, -200) == 0.0


def test_toughness_handles_none_inputs():
    # No ft/mi and no wind forecast must not raise.
    assert _compute_segment_toughness(None, None) == 0.0


# ── _toughness_class ─────────────────────────────────────────────────

def test_toughness_class_tiers():
    assert _toughness_class(2.9) == 't1'
    assert _toughness_class(3.0) == 't2'
    assert _toughness_class(4.9) == 't2'
    assert _toughness_class(5.0) == 't3'
    assert _toughness_class(6.9) == 't3'
    assert _toughness_class(7.0) == 't4'
    assert _toughness_class(9.5) == 't4'


# ── _to_v2_stops new fields ──────────────────────────────────────────

_PLAN = {'start_time': '06:00', 'total_distance_miles': 130.0, 'cutoff_hours': 13.5}

_STOPS = [
    {'location': 'Start', 'stop_type': 'start', 'distance_miles': 0.0,
     'seg_dist': 0.0, 'elevation_gain': 0, 'ft_per_mi': 0, 'segment_time_min': 0,
     'stop_duration_min': 0, 'arrival_time_min': 0, 'time_bank_min': 0, 'notes': None},
    {'location': 'Big Climb Control', 'stop_type': 'control', 'distance_miles': 60.0,
     'seg_dist': 60.0, 'elevation_gain': 4000, 'ft_per_mi': 67, 'segment_time_min': 240,
     'stop_duration_min': 15, 'arrival_time_min': 240, 'time_bank_min': 60, 'notes': None},
    {'location': 'Finish', 'stop_type': 'finish', 'distance_miles': 130.0,
     'seg_dist': 70.0, 'elevation_gain': 2000, 'ft_per_mi': 29, 'segment_time_min': 280,
     'stop_duration_min': 0, 'arrival_time_min': 520, 'time_bank_min': 30, 'notes': None},
]

# Index-aligned with _STOPS. headwind_kmh is the signed component (positive=head).
_STOP_WIND = [
    None,
    {'wind_speed_mph': 12.0, 'wind_arrow_deg': 170, 'wind_type': 'headwind',
     'headwind_kmh': 16.1, 'label': 'headwind'},   # ~10 mph headwind
    {'wind_speed_mph': 8.0, 'wind_arrow_deg': 10, 'wind_type': 'tailwind',
     'headwind_kmh': -12.9, 'label': 'tailwind'},   # ~8 mph tailwind
]


def test_to_v2_stops_surfaces_segment_time_and_speed():
    out = _to_v2_stops(_STOPS, _PLAN, _STOP_WIND)
    mid = out[1]
    assert mid['seg_time_min'] == 240
    # 60 mi over 240 min (4h) = 15.0 mph.
    assert mid['seg_speed'] == 15.0
    assert mid['seg_speed_known'] is True


def test_to_v2_stops_start_row_has_no_speed_or_toughness():
    out = _to_v2_stops(_STOPS, _PLAN, _STOP_WIND)
    start = out[0]
    assert start['seg_mi'] == 0.0
    assert start['seg_speed_known'] is False
    assert start['tough_known'] is False


def test_to_v2_stops_headwind_converted_to_mph():
    out = _to_v2_stops(_STOPS, _PLAN, _STOP_WIND)
    # 16.1 km/h headwind -> ~10.0 mph (positive = headwind).
    assert out[1]['headwind_mph'] == 10.0
    # -12.9 km/h -> ~-8.0 mph (tailwind, negative).
    assert out[2]['headwind_mph'] == -8.0


def test_to_v2_stops_toughness_reflects_climb_plus_headwind():
    out = _to_v2_stops(_STOPS, _PLAN, _STOP_WIND)
    mid = out[1]
    # climb(67 ft/mi)=4.19 + headwind(10 mph -> +3.33) = 7.5, tier t4.
    assert mid['tough'] == 7.5
    assert mid['tough_class'] == 't4'
    assert mid['tough_known'] is True
    finish = out[2]
    # climb(29 ft/mi)=1.81 + tailwind(-8 mph -> -1.0) = 0.8, tier t1.
    assert finish['tough'] == 0.8
    assert finish['tough_class'] == 't1'


def test_to_v2_stops_toughness_degrades_without_wind():
    # No forecast at all -> climbing-only toughness, no crash.
    out = _to_v2_stops(_STOPS, _PLAN, None)
    mid = out[1]
    assert mid['headwind_mph'] == 0.0
    assert mid['tough'] == 4.2   # climb only (67 ft/mi / 16)
    assert mid['tough_class'] == 't2'


def test_to_v2_stops_surfaces_cumulative_elapsed_time():
    out = _to_v2_stops(_STOPS, _PLAN, _STOP_WIND)
    # arrival_time_min carried from the route -> "Hh MM" elapsed string.
    assert out[0]['elapsed'] == '0h00'
    assert out[1]['elapsed'] == '4h00'   # 240 min
    assert out[2]['elapsed'] == '8h40'   # 520 min
    assert out[1]['cumul_time_min'] == 240


def test_to_v2_stops_keeps_wind_label_for_snapshot():
    # The visible word is dropped in the table, but wind_label stays in the
    # dict so the snapshot card / journey SVG can still color by it.
    out = _to_v2_stops(_STOPS, _PLAN, _STOP_WIND)
    assert out[1]['wind_label'] == 'Head'
    assert out[2]['wind_label'] == 'Tail'
    assert out[1]['wind_known'] is True
