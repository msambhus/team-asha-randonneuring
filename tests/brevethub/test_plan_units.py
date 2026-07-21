"""The miles / mph display contract for the /plan page (rev 4).

The reused engine stores NATIVE miles / mph / feet. BrevetHub's /plan page now DISPLAYS
per-stop distance in miles and speed in mph (US convention), while the ride's TOTAL
distance stays km (a brevet is a 200/300/400/600 km event). So:
  - real RWGPS plans show the engine's native miles / mph directly (round only),
  - synthetic Scope-A plans (km-native ACP math) CONVERT km→miles / km-h→mph at display,
  - the ?speed= input is mph, converted to km-h for the internal engine.
These tests make a regression back to km UN-passable — a stop distance would read
~1.6× larger and speeds ~1.6× larger if km leaked into the display:
  - the conversion helpers (1.609344),
  - _build_real_plan: final cumulative display distance == native total miles (≈124,
    explicitly NOT ≈200 km), a stop's displayed speed == native mph (12.0, not 19.3),
  - elevation stays FEET,
  - the SVG axis reflects the ~124 mi total,
  - the guest page renders the real plan in miles (SVG + real names, no "Scope A") and
    falls back to the synthetic miles table when no real plan exists,
  - the headline total distance stays km.
"""
from decimal import Decimal
from unittest.mock import patch

from brevethub.routes.plan import (KM_PER_MILE, MI_PER_KM, _build_elevation_svg,
                                   _build_real_plan, _km_to_mi, _kmh_to_mph, _round1)


# A persisted real plan whose NATIVE total is 124.3 mi (the mile equivalent of
# 200 km) and a stop at 12.0 mph.
_PLAN = {
    'name': 'Fixture 200', 'rwgps_url': 'https://ridewithgps.com/routes/1',
    'total_distance_miles': 124.3, 'total_elevation_ft': 3280,
    'overall_ft_per_mile': 26.4, 'avg_moving_speed': 12.0,
}
_STOPS = [
    {'stop_order': 1, 'location': 'Downtown Start', 'stop_type': 'start',
     'distance_miles': 0.0, 'seg_dist': 0.0, 'elevation_gain': 0,
     'ft_per_mi': None, 'avg_speed': None, 'cum_time_min': 0,
     'time_bank_min': None, 'difficulty_score': 0.0},
    {'stop_order': 2, 'location': 'Midway Control', 'stop_type': 'control',
     'distance_miles': 62.1, 'seg_dist': 62.1, 'elevation_gain': 1600,
     'ft_per_mi': 26, 'avg_speed': 12.0, 'cum_time_min': 310,
     'time_bank_min': 90, 'difficulty_score': 2.6},
    {'stop_order': 3, 'location': 'Downtown Finish', 'stop_type': 'finish',
     'distance_miles': 124.3, 'seg_dist': 62.2, 'elevation_gain': 1680,
     'ft_per_mi': 27, 'avg_speed': 11.9, 'cum_time_min': 625,
     'time_bank_min': 125, 'difficulty_score': 2.7},
]
_EVENT = {'id': 11, 'name': 'Fixture 200', 'date': '2026-08-15',
          'distance_km': 200, 'region': 'CA', 'rwgps_url': None,
          'time_limit_hours': 13.5, 'elevation_ft': 3280}


# --------------------------------------------------------------------------- #
# Standalone conversion helpers
# --------------------------------------------------------------------------- #
def test_conversion_constants_and_helpers():
    assert abs(KM_PER_MILE - 1.609344) < 1e-9
    assert abs(MI_PER_KM - 1.0 / 1.609344) < 1e-9
    assert _km_to_mi(200.0) == round(200.0 * MI_PER_KM, 1)   # ≈ 124.3
    assert _kmh_to_mph(19.31) == 12.0                        # 19.31 * 0.621371 ≈ 12.0
    assert _round1(12.34) == 12.3
    assert _km_to_mi(None) is None and _kmh_to_mph(None) is None and _round1(None) is None


def test_helpers_accept_decimal():
    # psycopg2 returns NUMERIC columns as Decimal; Decimal mixed with float raises.
    # The helpers must coerce so a real plan's stored values never 500 the page.
    assert _round1(Decimal('124.3')) == 124.3
    assert _km_to_mi(Decimal('200.0')) == round(200.0 * MI_PER_KM, 1)
    assert _kmh_to_mph(Decimal('19.31')) == 12.0


def test_build_real_plan_accepts_numeric_decimal_fields():
    # Re-cast the fixture's distance/speed fields to Decimal, exactly as psycopg2's
    # NUMERIC columns arrive — the whole miles/mph build must complete without raising.
    def _dec(d, keys):
        return {**d, **{k: (Decimal(str(d[k])) if d.get(k) is not None else None)
                         for k in keys}}
    plan = _dec(_PLAN, ['total_distance_miles', 'avg_moving_speed', 'overall_ft_per_mile'])
    stops = [_dec(s, ['distance_miles', 'seg_dist', 'avg_speed', 'difficulty_score'])
             for s in _STOPS]
    real = _build_real_plan(plan, stops)
    # Native miles/mph shown directly, not crashed or converted.
    assert real['final_distance_mi'] == 124.3
    assert real['stops'][1]['avg_speed_mph'] == 12.0
    assert real['svg']['line_path'].startswith('M')


# --------------------------------------------------------------------------- #
# _build_real_plan — native miles/mph display
# --------------------------------------------------------------------------- #
def test_final_distance_is_native_miles_not_km():
    real = _build_real_plan(_PLAN, _STOPS)
    final = real['final_distance_mi']
    # Native miles, NOT converted to km: ≈124.3, explicitly NOT ≈200.
    assert final == 124.3
    assert abs(final - _EVENT['distance_km']) > 50     # would be ~200 if km leaked in
    # total_distance_mi (the difficulty-strip denominator) is the native route length.
    assert real['total_distance_mi'] == 124.3


def test_stop_speed_is_native_mph():
    real = _build_real_plan(_PLAN, _STOPS)
    midway = real['stops'][1]
    assert midway['avg_speed_mph'] == 12.0    # native mph, NOT 19.3 (km-h)
    assert midway['location'] == 'Midway Control'
    assert real['avg_moving_speed_mph'] == 12.0


def test_elevation_stays_feet():
    real = _build_real_plan(_PLAN, _STOPS)
    # Stored feet render verbatim — no km/m conversion of the elevation axis.
    assert real['stops'][1]['elevation_gain'] == 1600
    assert real['total_elevation_ft'] == 3280


def test_difficulty_colored_per_segment():
    real = _build_real_plan(_PLAN, _STOPS)
    colors = {s['difficulty_color'] for s in real['stops']}
    assert all(c.startswith('#') for c in colors)
    # A harder stop and the flat start should not share a color.
    assert real['stops'][0]['difficulty_color'] != real['stops'][2]['difficulty_color']


def test_svg_axis_reflects_miles_total():
    real = _build_real_plan(_PLAN, _STOPS)
    svg = real['svg']
    assert svg['total_mi'] == 124.3          # native miles, not ~200 km
    assert svg['markers'], "SVG should have per-stop markers"
    assert svg['line_path'].startswith('M')
    # Gridline labels are miles (present at 0, and a 25-mi step within range).
    labels = {g['label'] for g in svg['gridlines']}
    assert 0 in labels and 100 in labels


def test_build_svg_handles_empty_stops():
    svg = _build_elevation_svg([])
    assert svg['line_path'] == '' and svg['markers'] == []


# --------------------------------------------------------------------------- #
# Route render — real plan vs synthetic fallback
# --------------------------------------------------------------------------- #
def test_guest_sees_real_plan_in_miles(client):
    bundle = {'plan': _PLAN, 'stops': _STOPS}
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops', return_value=bundle):
        resp = client.get('/plan/11')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'class="plan-elev-svg"' in body      # elevation profile chart element present
    assert 'Midway Control' in body             # real control name (not "62 mi")
    assert '124.3 mi' in body                   # native final cumulative distance (miles)
    assert '12.0 mph' in body                   # native speed (NOT 19.3 km/h)
    assert '19.3 km/h' not in body              # km-h must not leak back in
    assert '1600 ft' in body                    # elevation in feet
    # The headline TOTAL distance stays km (the brevet's nominal ACP distance).
    assert '<div class="label">Distance (km)</div>' in body
    assert 'Scope A' not in body                # synthetic note is gone


def test_fallback_to_synthetic_when_no_real_plan(client):
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops', return_value=None):
        resp = client.get('/plan/11')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Scope A' in body                    # synthetic note present
    # Nav has its own hamburger <svg>; assert the elevation-profile chart
    # specifically is absent in synthetic mode.
    assert 'class="plan-elev-svg"' not in body  # no elevation profile chart element
    assert '62.1 mi' in body                    # 100 km evenly-spaced control → 62.1 mi
    # The headline total stays km even in synthetic mode.
    assert '<div class="label">Distance (km)</div>' in body


# --------------------------------------------------------------------------- #
# Conservative/aggressive variant toggle + meal-break rows
# --------------------------------------------------------------------------- #
# An aggressive plan (14.0 mph, a value UNIQUE to it vs conservative's 13.0) carrying
# a 30-min meal break after the midway control.
_AGG_PLAN = {
    'name': 'Fixture 200', 'variant': 'aggressive',
    'rwgps_url': 'https://ridewithgps.com/routes/1',
    'total_distance_miles': 124.3, 'total_elevation_ft': 3280,
    'overall_ft_per_mile': 26.4, 'avg_moving_speed': 14.0,
    'total_break_time_min': 30,
}
_AGG_STOPS = [
    {'stop_order': 1, 'location': 'Downtown Start', 'stop_type': 'start',
     'distance_miles': 0.0, 'seg_dist': 0.0, 'elevation_gain': 0, 'notes': '',
     'ft_per_mi': None, 'avg_speed': None, 'segment_time_min': 0,
     'cum_time_min': 0, 'time_bank_min': None, 'difficulty_score': 0.0},
    {'stop_order': 2, 'location': 'Midway Control', 'stop_type': 'control',
     'distance_miles': 62.1, 'seg_dist': 62.1, 'elevation_gain': 1600, 'notes': '',
     'ft_per_mi': 26, 'avg_speed': 14.0, 'segment_time_min': 266,
     'cum_time_min': 266, 'time_bank_min': 120, 'difficulty_score': 2.6},
    {'stop_order': 3, 'location': 'Lunch', 'stop_type': 'meal',
     'distance_miles': 62.1, 'seg_dist': 0.0, 'elevation_gain': 0, 'notes': 'Lunch',
     'ft_per_mi': None, 'avg_speed': None, 'segment_time_min': 30,
     'cum_time_min': 296, 'time_bank_min': None, 'difficulty_score': 0.0},
    {'stop_order': 4, 'location': 'Downtown Finish', 'stop_type': 'finish',
     'distance_miles': 124.3, 'seg_dist': 62.2, 'elevation_gain': 1680, 'notes': '',
     'ft_per_mi': 27, 'avg_speed': 13.9, 'segment_time_min': 268,
     'cum_time_min': 564, 'time_bank_min': 150, 'difficulty_score': 2.7},
]


def test_build_real_plan_renders_meal_rows_and_break_total():
    real = _build_real_plan(_AGG_PLAN, _AGG_STOPS)
    meals = [s for s in real['stops'] if s['is_meal']]
    assert len(meals) == 1
    m = meals[0]
    assert m['meal_label'] == 'Lunch' and m['dwell_min'] == 30
    assert m['avg_speed_mph'] is None and m['seg_dist_mi'] is None
    # Plan-level break total surfaces for the summary.
    assert real['total_break_time_min'] == 30
    assert real['total_break_hm'] == '0h 30m'
    assert real['variant'] == 'aggressive'
    # The SVG excludes meal rows (3 control markers, not 4).
    assert len(real['svg']['markers']) == 3


def test_variant_param_selects_aggressive_and_renders_toggle(client):
    captured = {}

    def _bundle(event_id, variant='conservative'):
        captured['variant'] = variant
        return {'plan': _AGG_PLAN, 'stops': _AGG_STOPS}

    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops', side_effect=_bundle):
        resp = client.get('/plan/11?variant=aggressive')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert captured['variant'] == 'aggressive'          # the stored aggressive plan
    assert '14.0 mph' in body                           # native mph (unique to aggressive)
    # Meal-break row + dwell + total break time render.
    assert 'Lunch' in body
    assert 'Break · 30 min' in body
    assert '0h 30m' in body                             # total break time in the summary
    # The toggle shows both options, aggressive marked active (aria-current only on it).
    assert 'aria-current="true">Aggressive' in body
    assert '>Conservative</a>' in body
    assert 'is-active' in body


def test_variant_defaults_to_conservative_without_param(client):
    captured = {}

    def _bundle(event_id, variant='conservative'):
        captured['variant'] = variant
        return {'plan': dict(_AGG_PLAN, variant='conservative'), 'stops': _AGG_STOPS}

    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops', side_effect=_bundle):
        resp = client.get('/plan/11')
    assert resp.status_code == 200
    assert captured['variant'] == 'conservative'
    body = resp.get_data(as_text=True)
    assert 'aria-current="true">Conservative' in body


def test_bad_variant_param_falls_back_to_conservative(client):
    captured = {}

    def _bundle(event_id, variant='conservative'):
        captured['variant'] = variant
        return {'plan': dict(_AGG_PLAN, variant='conservative'), 'stops': _AGG_STOPS}

    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops', side_effect=_bundle):
        resp = client.get('/plan/11?variant=nonsense')
    assert resp.status_code == 200
    assert captured['variant'] == 'conservative'        # invalid → conservative
