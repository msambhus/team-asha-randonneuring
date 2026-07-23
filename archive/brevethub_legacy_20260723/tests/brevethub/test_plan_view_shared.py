"""Unit tests for the promoted shared plan-view + strategy helpers.

These are the pure functions BrevetHub reuses from Team Asha via shared/plan_view.py
and shared/strategies.py (no fork). They import stdlib only, so no Flask app or DB is
needed. Coverage:
  * per-segment toughness score + colour tier,
  * fuel-stop detection,
  * the wind-dict normalization (Team Asha shape AND BrevetHub shape → one canonical
    key set),
  * _to_v2_stops over BrevetHub-style stop rows,
  * the weather summary aggregates,
  * the 3-card pace strategy math (Comfort ≥ Standard ≥ Push total),
  * the risk-zone sunrise generalization: a non-SF latitude differs from the Bay Area
    table, while the lat=None path is byte-identical to the pre-promotion behaviour,
  * the stdlib solar helper (safe default outside ±65°).
"""
import datetime

from shared.plan_view import (
    _BAY_AREA_SUN, _compute_segment_toughness, _stop_is_fuel, _to_v2_stops,
    _toughness_class, _weather_summary_from_stop_wind, compute_risk_zones,
    compute_sun_times, normalize_wind,
)
from shared.strategies import compute_pace_strategies


# --------------------------------------------------------------------------- #
# Toughness + tiers + fuel
# --------------------------------------------------------------------------- #
def test_segment_toughness_climb_wind_temp():
    assert _compute_segment_toughness(0, 0) == 0.0
    assert _compute_segment_toughness(80, 0) == 5.0
    assert _compute_segment_toughness(160, 0) == 8.5          # base capped
    assert _compute_segment_toughness(67, 3, 95) == 8.5       # +1.5 heat penalty
    assert _compute_segment_toughness(200, 200, 110) == 10.0  # clamped
    assert _compute_segment_toughness(20, -10) == 0.0         # tailwind erases climb
    assert _compute_segment_toughness(None, None, None) == 0.0


def test_toughness_class_tiers():
    assert _toughness_class(2.9) == 't1'
    assert _toughness_class(3.0) == 't2'
    assert _toughness_class(5.0) == 't3'
    assert _toughness_class(7.0) == 't4'


def test_stop_is_fuel_keyword_match():
    assert _stop_is_fuel({'note': 'Lunch — refuel', 'name': 'Control'}) is True
    assert _stop_is_fuel({'notes': None, 'location': 'Safeway'}) is True
    assert _stop_is_fuel({'note': '', 'name': 'Open Control #3'}) is False


# --------------------------------------------------------------------------- #
# Wind-dict normalization — Team Asha shape AND BrevetHub shape agree
# --------------------------------------------------------------------------- #
def test_normalize_wind_canonical_keys_match_across_surfaces():
    ta = {'wind_speed_mph': 12.0, 'wind_arrow_deg': 170, 'wind_type': 'headwind',
          'headwind_kmh': 16.1, 'temperature_f': 95, 'label': 'headwind'}
    bh = {'wind_speed_kmh': 19.3, 'wind_speed_mph': 12.0, 'headwind_kmh': 16.1,
          'wind_type': 'headwind', 'wind_arrow_deg': 170, 'arrow_rotation': 170,
          'label': 'headwind', 'temperature_f': 95}
    na, nb = normalize_wind(ta), normalize_wind(bh)
    for key in ('wind_speed_mph', 'headwind_mph', 'wind_type', 'arrow_rotation',
                'wind_label', 'temperature_f'):
        assert na[key] == nb[key], key
    assert na['headwind_mph'] == 10.0        # 16.1 km/h → ~10 mph, positive = head
    assert na['wind_label'] == 'Head'
    assert na['arrow_rotation'] == 170


def test_normalize_wind_none():
    assert normalize_wind(None) is None
    assert normalize_wind({}) is None    # an empty stop-wind entry is treated as missing


# --------------------------------------------------------------------------- #
# _to_v2_stops over BrevetHub-style rows
# --------------------------------------------------------------------------- #
_PLAN = {'start_time': '06:00', 'total_distance_miles': 124.3, 'cutoff_hours': 13.5}
_ROWS = [
    {'location': 'Start', 'stop_type': 'start', 'distance_miles': 0.0, 'seg_dist': 0.0,
     'elevation_gain': 0, 'ft_per_mi': 0, 'segment_time_min': 0, 'stop_duration_min': 0,
     'arrival_time_min': 0, 'time_bank_min': 0, 'notes': None},
    {'location': 'Midway Control', 'stop_type': 'control', 'distance_miles': 62.1,
     'seg_dist': 62.1, 'elevation_gain': 1600, 'ft_per_mi': 26, 'segment_time_min': 266,
     'stop_duration_min': 0, 'arrival_time_min': 266, 'time_bank_min': 120, 'notes': None},
    {'location': 'Lunch', 'stop_type': 'rest', 'distance_miles': 62.1, 'seg_dist': 0.0,
     'elevation_gain': 0, 'ft_per_mi': 0, 'segment_time_min': 0, 'stop_duration_min': 30,
     'arrival_time_min': 266, 'time_bank_min': None, 'notes': 'Lunch'},
    {'location': 'Finish', 'stop_type': 'finish', 'distance_miles': 124.3,
     'seg_dist': 62.2, 'elevation_gain': 1680, 'ft_per_mi': 27, 'segment_time_min': 268,
     'stop_duration_min': 0, 'arrival_time_min': 564, 'time_bank_min': 150, 'notes': None},
]


def test_to_v2_stops_over_bh_rows():
    out = _to_v2_stops(_ROWS, _PLAN, None)
    assert [s['type'] for s in out] == ['start', 'control', 'rest', 'finish']
    # Start row: no segment, so no speed/toughness.
    assert out[0]['seg_speed_known'] is False and out[0]['tough_known'] is False
    # Control: 62.1 mi over 266 min → implied speed known, toughness from climb.
    assert out[1]['seg_mi'] == 62.1 and out[1]['seg_speed_known'] is True
    assert out[1]['tough_known'] is True
    # Lunch rest row: fuel flag + break minutes surfaced.
    assert out[2]['is_fuel'] is True and out[2]['break_min'] == 30
    # No wind forecast → wind unknown, but no crash.
    assert out[1]['wind_known'] is False


def test_to_v2_stops_wind_through_normalized_dict():
    stop_wind = [None,
                 {'wind_speed_mph': 12.0, 'wind_arrow_deg': 170, 'wind_type': 'headwind',
                  'headwind_kmh': 16.1, 'temperature_f': 95, 'label': 'headwind'},
                 None,
                 {'wind_speed_mph': 8.0, 'wind_arrow_deg': 10, 'wind_type': 'tailwind',
                  'headwind_kmh': -12.9, 'temperature_f': 60, 'label': 'tailwind'}]
    out = _to_v2_stops(_ROWS, _PLAN, stop_wind)
    assert out[1]['wind_known'] is True and out[1]['wind_label'] == 'Head'
    assert out[1]['headwind_mph'] == 10.0
    assert out[3]['wind_label'] == 'Tail' and out[3]['headwind_mph'] == -8.0


# --------------------------------------------------------------------------- #
# Weather summary
# --------------------------------------------------------------------------- #
def test_weather_summary_aggregates():
    stop_wind = [
        {'temperature_f': 95, 'wind_speed_mph': 12.0, 'wind_type': 'headwind'},
        {'temperature_f': 60, 'wind_speed_mph': 8.0, 'wind_type': 'crosswind'},
    ]
    ws = _weather_summary_from_stop_wind(stop_wind, _ROWS)
    assert ws['temp_low'] == 60 and ws['temp_high'] == 95
    assert ws['wind_max'] == 12 and ws['headwind_segs'] == 1 and ws['crosswind_segs'] == 1
    assert _weather_summary_from_stop_wind(None, _ROWS)['wind_max'] is None


# --------------------------------------------------------------------------- #
# Pace strategies
# --------------------------------------------------------------------------- #
def test_pace_strategies_three_cards_ordered():
    paces = compute_pace_strategies(_ROWS, _PLAN, '06:00', 13.5)
    assert [p['id'] for p in paces] == ['comfort', 'standard', 'push']
    assert paces[1]['recommended'] is True

    def total_min(p):
        h, m = p['total'].split(':')
        return int(h) * 60 + int(m)

    # Comfort is the slowest, Push the fastest (within the cutoff).
    assert total_min(paces[0]) >= total_min(paces[1]) >= total_min(paces[2])


# --------------------------------------------------------------------------- #
# Risk-zone sunrise generalization
# --------------------------------------------------------------------------- #
def test_risk_zones_lat_none_is_bay_area_identity():
    """No coordinates → the Bay Area monthly table, byte-identical to the pre-promotion
    behaviour Team Asha still relies on."""
    v2 = _to_v2_stops(_ROWS, _PLAN, None)
    risks = compute_risk_zones(_ROWS, v2, _PLAN, '06:00', datetime.date(2026, 5, 10))
    assert risks['sunrise_str'] == _BAY_AREA_SUN[5][0]
    assert risks['sunset_str'] == _BAY_AREA_SUN[5][1]


def test_risk_zones_non_sf_latitude_differs_from_bay_area():
    """A route far from the Bay Area derives its OWN sunrise from lat/lon — proof the
    heuristic table is no longer hard-wired."""
    v2 = _to_v2_stops(_ROWS, _PLAN, None)
    # Bend, Oregon (~44.06 N, -121.3) in May — clearly different daylight than the Bay.
    risks = compute_risk_zones(_ROWS, v2, _PLAN, '06:00', datetime.date(2026, 5, 10),
                               lat=44.06, lon=-121.3)
    assert risks['sunrise_str'] != _BAY_AREA_SUN[5][0]
    assert risks['has_data'] is True


def test_solar_helper_bounds_and_safe_default():
    # Summer solstice at a mid-latitude: a long day (early sunrise, late sunset).
    sr, ss = compute_sun_times(44.06, -121.3, datetime.date(2026, 6, 21))
    assert sr < '07:00' and ss > '18:30'
    # Beyond ±65° the equation is refused (polar day/night) → safe default None.
    assert compute_sun_times(80.0, 10.0, datetime.date(2026, 6, 21)) is None
    assert compute_sun_times(None, None, datetime.date(2026, 6, 21)) is None
