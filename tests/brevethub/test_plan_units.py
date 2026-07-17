"""The km / km-h unit boundary for real RWGPS plans (rev 3).

The reused engine stores NATIVE miles / mph / feet; BrevetHub's /plan UI is km /
km-h. brevethub.routes.plan is the single conversion boundary. These tests make the
mile→km / mph→km-h regression UN-passable — a bare relabel (no conversion) would
show ~0.62× the distance and mph-as-km-h and these assertions would fail:
  - a standalone conversion-helper test (1.609344),
  - _build_real_plan: final cumulative display distance ≈ total_miles × 1.609344
    (≈200 km for the fixture, explicitly NOT ≈124), within ~10% of event.distance_km,
  - a stop's displayed speed ≈ stored mph × 1.609344 (12.0 → 19.3),
  - elevation stays FEET (no accidental km/m conversion),
  - the SVG axis reflects the ~200 km total,
  - the guest page renders the real plan (SVG + real names, no "Scope A") and falls
    back to the synthetic km table when no real plan exists.
"""
from decimal import Decimal
from unittest.mock import patch

from brevethub.routes.plan import (KM_PER_MILE, _build_elevation_svg,
                                   _build_real_plan, _mi_to_km, _mph_to_kmh)


def _as_numeric(plan, stops):
    """Re-cast the fixture's distance/speed fields to Decimal, exactly as psycopg2's
    RealDictCursor returns NUMERIC columns from Postgres."""
    p = dict(plan)
    for k in ('total_distance_miles', 'avg_moving_speed', 'overall_ft_per_mile'):
        if p.get(k) is not None:
            p[k] = Decimal(str(p[k]))
    out = []
    for s in stops:
        s = dict(s)
        for k in ('distance_miles', 'seg_dist', 'avg_speed', 'ft_per_mi',
                  'difficulty_score'):
            if s.get(k) is not None:
                s[k] = Decimal(str(s[k]))
        out.append(s)
    return p, out


# A persisted real plan whose NATIVE total is 124.3 mi (the mile equivalent of
# 200 km) and a stop at 12.0 mph — the exact regression the redteam flagged.
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
def test_conversion_constant_and_helpers():
    assert abs(KM_PER_MILE - 1.609344) < 1e-9
    assert _mi_to_km(124.3) == round(124.3 * 1.609344, 1)   # ≈ 200.0
    assert _mph_to_kmh(12.0) == 19.3                        # 12 * 1.609344 = 19.31
    assert _mi_to_km(None) is None and _mph_to_kmh(None) is None


def test_converters_accept_decimal():
    """psycopg2 returns NUMERIC columns as Decimal; Decimal * float raises TypeError.
    The converters must coerce, not crash — the /plan 500 the council flagged."""
    assert _mi_to_km(Decimal('124.3')) == round(124.3 * KM_PER_MILE, 1)
    assert _mph_to_kmh(Decimal('12.0')) == 19.3
    # Result is a plain float, safe for further float arithmetic / the SVG geometry.
    assert isinstance(_mi_to_km(Decimal('62.1')), float)


# --------------------------------------------------------------------------- #
# _build_real_plan — the numeric boundary
# --------------------------------------------------------------------------- #
def test_final_distance_is_km_not_mislabeled_miles():
    real = _build_real_plan(_PLAN, _STOPS)
    final = real['final_distance_km']
    expected = 124.3 * KM_PER_MILE
    # Converted, not relabeled: ≈200 km, and explicitly NOT ≈124.
    assert abs(final - expected) < 0.5
    assert 0.9 * _EVENT['distance_km'] <= final <= 1.15 * _EVENT['distance_km']
    assert abs(final - 124.3) > 50           # would be ~124 if mislabeled


def test_stop_speed_is_converted_kmh():
    real = _build_real_plan(_PLAN, _STOPS)
    midway = real['stops'][1]
    assert midway['avg_speed_kmh'] == 19.3    # 12.0 mph converted, not 12.0
    assert midway['location'] == 'Midway Control'


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


def test_svg_axis_reflects_km_total():
    real = _build_real_plan(_PLAN, _STOPS)
    svg = real['svg']
    assert abs(svg['total_km'] - 124.3 * KM_PER_MILE) < 0.5   # ~200, not ~124
    assert svg['markers'], "SVG should have per-stop markers"
    assert svg['line_path'].startswith('M')


def test_build_svg_handles_empty_stops():
    svg = _build_elevation_svg([])
    assert svg['line_path'] == '' and svg['markers'] == []


def test_build_real_plan_with_decimal_numeric_columns():
    """The whole build must survive Decimal-valued NUMERIC columns (no TypeError),
    and still produce the same converted km / km-h values as the float fixture."""
    plan, stops = _as_numeric(_PLAN, _STOPS)
    real = _build_real_plan(plan, stops)
    assert real['final_distance_km'] == round(124.3 * KM_PER_MILE, 1)
    assert real['stops'][1]['avg_speed_kmh'] == 19.3
    assert real['stops'][1]['elevation_gain'] == 1600
    assert real['svg']['markers']                 # SVG geometry built without crashing
    assert abs(real['svg']['total_km'] - 124.3 * KM_PER_MILE) < 0.5


# --------------------------------------------------------------------------- #
# Route render — real plan vs synthetic fallback
# --------------------------------------------------------------------------- #
def test_guest_sees_real_plan_in_km(client):
    bundle = {'plan': _PLAN, 'stops': _STOPS}
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops', return_value=bundle):
        resp = client.get('/plan/11')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '<svg' in body                       # elevation profile present
    assert 'Midway Control' in body             # real control name (not "100 km")
    assert '200.0 km' in body                   # converted final cumulative (124.3 mi → km)
    assert '124.3 km' not in body               # NOT mile-mislabeled-as-km
    assert '19.3 km/h' in body                  # converted speed
    assert '1600 ft' in body                    # elevation in feet
    assert 'Scope A' not in body                # synthetic note is gone


def test_guest_real_plan_renders_200_with_decimal_columns(client):
    """Regression for the council finding: a persisted plan whose NUMERIC fields come
    back as Decimal must render 200 (converted km / km-h), not 500."""
    plan, stops = _as_numeric(_PLAN, _STOPS)
    bundle = {'plan': plan, 'stops': stops}
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops', return_value=bundle):
        resp = client.get('/plan/11')
    assert resp.status_code == 200                 # NOT a 500 from Decimal * float
    body = resp.get_data(as_text=True)
    assert '<svg' in body and '19.3 km/h' in body and '200.0 km' in body


def test_fallback_to_synthetic_when_no_real_plan(client):
    with patch('brevethub.models.get_brevet_event_full', return_value=_EVENT), \
         patch('brevethub.models.get_brevet_route_plan_with_stops', return_value=None):
        resp = client.get('/plan/11')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Scope A' in body                    # synthetic note present
    assert '<svg' not in body                   # no elevation profile
    assert '100 km' in body                     # evenly-spaced synthetic control
