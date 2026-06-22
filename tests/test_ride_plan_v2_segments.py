"""Unit tests for the v2 itinerary per-segment metrics + toughness.

Covers the pure helpers (`_compute_segment_toughness`, `_toughness_class`) and
the new fields wired through `_to_v2_stops` (segment time/speed, signed
headwind, and the per-segment toughness score). These are pure functions, so no
Flask app or DB is needed.
"""
from routes.riders import (
    _compute_segment_toughness,
    _temp_penalty,
    _toughness_class,
    _to_v2_stops,
)


# ── _compute_segment_toughness (climbing + headwind-as-equiv-climb + temp) ──

def test_toughness_zero_when_flat_and_calm():
    assert _compute_segment_toughness(0, 0) == 0.0


def test_toughness_climbing_only_caps_at_8_5():
    # Climb maps at ~16 ft/mile per point; 80 ft/mi -> 5.0; cap at 8.5.
    assert _compute_segment_toughness(80, 0) == 5.0
    assert _compute_segment_toughness(160, 0) == 8.5
    assert _compute_segment_toughness(400, 0) == 8.5


def test_toughness_headwind_weighted_as_equivalent_climb():
    # Headwind is converted to equivalent climbing at 15 ft/mile per mph
    # (12 physics x 1.25 morale). So a 5 mph headwind on the flat scores the
    # same as a 75 ft/mile climb.
    assert _compute_segment_toughness(0, 5) == _compute_segment_toughness(75, 0)
    # And it stacks with real climbing: 50 ft/mi + 5 mph hw -> 125 eff -> 7.8.
    assert _compute_segment_toughness(50, 5) == 7.8


def test_toughness_headwind_now_dominates_old_underweighting():
    # The old model gave a 10 mph headwind only ~3/10; the physical model makes
    # it ~8.5 (150 equiv ft/mile), far above a numerically-equal 100 ft/mi climb.
    assert _compute_segment_toughness(0, 10) >= 8.0
    assert _compute_segment_toughness(0, 10) > _compute_segment_toughness(100, 0)


def test_toughness_tailwind_eases_at_lower_rate():
    # Tailwind helps (7 ft/mile per mph, less than headwind hurts); it can
    # reduce a mild climb's effective gradient toward zero (floored at 0).
    assert _compute_segment_toughness(50, -5) < _compute_segment_toughness(50, 0)
    assert _compute_segment_toughness(20, -10) == 0.0   # tailwind erases a mild climb


def test_toughness_temperature_adds_heat_penalty():
    # Same climb+wind, hotter -> tougher. 67 ft/mi + 3 mph hw = 112 eff -> 7.0.
    assert _compute_segment_toughness(67, 3, None) == 7.0
    assert _compute_segment_toughness(67, 3, 95) == 8.5   # +1.5 heat penalty
    assert _compute_segment_toughness(67, 3, 60) == 7.0   # comfort band, no penalty


def test_toughness_never_exceeds_10_or_below_0():
    assert _compute_segment_toughness(200, 200, 110) == 10.0
    assert _compute_segment_toughness(0, -200, None) == 0.0


def test_toughness_handles_none_inputs():
    # No ft/mi, no wind, no temperature must not raise.
    assert _compute_segment_toughness(None, None, None) == 0.0


# ── _temp_penalty ────────────────────────────────────────────────────

def test_temp_penalty_comfort_band_is_zero():
    assert _temp_penalty(50) == 0.0
    assert _temp_penalty(60) == 0.0
    assert _temp_penalty(68) == 0.0


def test_temp_penalty_heat_curve():
    assert _temp_penalty(75) == 0.25
    assert _temp_penalty(82) == 0.5
    assert _temp_penalty(90) == 1.0
    assert _temp_penalty(100) == 2.0
    assert _temp_penalty(110) == 2.5


def test_temp_penalty_cold_smaller_than_heat():
    assert _temp_penalty(32) == 0.5
    assert _temp_penalty(45) == 0.1
    # Cold's worst (freezing) is far below heat's worst.
    assert _temp_penalty(32) < _temp_penalty(100)


def test_temp_penalty_none_is_zero():
    assert _temp_penalty(None) == 0.0


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

# Index-aligned with _STOPS. headwind_kmh is the signed component (positive=head);
# temperature_f feeds the heat/cold penalty.
_STOP_WIND = [
    None,
    {'wind_speed_mph': 12.0, 'wind_arrow_deg': 170, 'wind_type': 'headwind',
     'headwind_kmh': 16.1, 'temperature_f': 95, 'label': 'headwind'},   # ~10 mph headwind, hot
    {'wind_speed_mph': 8.0, 'wind_arrow_deg': 10, 'wind_type': 'tailwind',
     'headwind_kmh': -12.9, 'temperature_f': 60, 'label': 'tailwind'},   # ~8 mph tailwind, comfort
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


def test_to_v2_stops_toughness_reflects_climb_headwind_and_temp():
    out = _to_v2_stops(_STOPS, _PLAN, _STOP_WIND)
    mid = out[1]
    # 67 ft/mi + 10 mph hw (150 equiv) = 217 eff -> base capped 8.5,
    # + heat penalty (95F = +1.5) -> clamped to 10.0, tier t4.
    assert mid['tough'] == 10.0
    assert mid['tough_class'] == 't4'
    assert mid['tough_known'] is True
    finish = out[2]
    # 29 ft/mi + 8 mph tailwind (-56 equiv) = 0 eff, 60F comfort -> 0.0, tier t1.
    assert finish['tough'] == 0.0
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
