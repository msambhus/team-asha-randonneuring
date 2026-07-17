"""The vendored RWGPS engine (brevethub/shared/rwgps.py).

BrevetHub reuses Team Asha's proven RWGPS engine as a framework-agnostic, vendored
copy under brevethub/shared/. These tests pin the pure-Python behaviour BrevetHub
relies on — no network (fetch_route is never called here; route data is a fixture):
  - RWGPS URL → numeric route-id parsing,
  - extract_controls maps course-point types to the right stop_type and synthesizes
    start/finish,
  - build_ride_plan produces per-stop difficulty_score and gradient-aware pacing,
  - calculate_segment_speed is monotonic (steeper → slower) and clamped.

The engine is identical to the canonical shared/rwgps.py (test_vendored_shared_sync
guards that); importing from brevethub.shared keeps the isolation boundary honest.
"""
from brevethub.shared import rwgps


# A small fixture route (meters / meters-elevation, as RWGPS returns).
_ROUTE = {
    'id': 987654,
    'name': 'Fixture Populaire 200k',
    'distance': 200000,          # ~124.3 miles
    'elevation_gain': 390,       # meters (~1280 ft)
    'course_points': [
        {'t': 'Start', 'n': 'Downtown Start', 'd': 0, 'e': 10},
        {'t': 'Control', 'n': 'Midway Control', 'd': 100000, 'e': 40},
        {'t': 'Food', 'n': 'Lunch Cafe', 'd': 150000, 'e': 60},
        {'t': 'End', 'n': 'Downtown Finish', 'd': 200000, 'e': 10},
    ],
    'track_points': [
        {'d': 0, 'e': 10}, {'d': 50000, 'e': 25}, {'d': 100000, 'e': 40},
        {'d': 150000, 'e': 220}, {'d': 200000, 'e': 400},
    ],
}


# --------------------------------------------------------------------------- #
# URL / route-id parsing
# --------------------------------------------------------------------------- #
def test_extract_route_id_from_url():
    assert rwgps.extract_rwgps_route_id('https://ridewithgps.com/routes/12345') == '12345'
    assert rwgps.extract_rwgps_route_id('http://ridewithgps.com/routes/999?x=1') == '999'


def test_extract_route_id_missing_or_bad():
    assert rwgps.extract_rwgps_route_id('') is None
    assert rwgps.extract_rwgps_route_id(None) is None
    assert rwgps.extract_rwgps_route_id('https://example.com/no-route-here') is None


def test_slugify():
    assert rwgps.slugify('Fixture Populaire 200k') == 'fixture-populaire-200k'


# --------------------------------------------------------------------------- #
# Control extraction — stop_type mapping + synthesized bookends
# --------------------------------------------------------------------------- #
def test_extract_controls_stop_types():
    controls = rwgps.extract_controls(_ROUTE)
    # Sorted by distance, start first / finish last.
    assert controls[0]['stop_type'] == 'start'
    assert controls[-1]['stop_type'] == 'finish'
    by_name = {c['name']: c['stop_type'] for c in controls}
    assert by_name['Midway Control'] == 'control'   # RWGPS 'Control' → control
    assert by_name['Lunch Cafe'] == 'rest'          # RWGPS 'Food' → rest


def test_extract_controls_requires_waypoints():
    import pytest
    with pytest.raises(Exception):
        rwgps.extract_controls({'name': 'Empty', 'distance': 100000,
                                'course_points': []})


# --------------------------------------------------------------------------- #
# Gradient speed model — steeper is slower, clamped
# --------------------------------------------------------------------------- #
def test_speed_lower_for_higher_gradient():
    # Higher ft/mile → lower speed (the whole point of the model).
    assert rwgps.calculate_segment_speed(60) < rwgps.calculate_segment_speed(30)
    assert rwgps.calculate_segment_speed(100) < rwgps.calculate_segment_speed(60)


def test_speed_clamped_and_defaulted():
    assert rwgps.calculate_segment_speed(0) == 15.0          # flat → cap
    assert rwgps.calculate_segment_speed(1000) == 7.0        # very steep → floor
    assert rwgps.calculate_segment_speed(None) == 12.0       # unknown → baseline
    assert rwgps.calculate_segment_speed(-5) == 12.0         # negative → baseline


# --------------------------------------------------------------------------- #
# Full plan build — native units, difficulty score, gradient pacing
# --------------------------------------------------------------------------- #
def test_build_ride_plan_stops_and_difficulty():
    controls = rwgps.extract_controls(_ROUTE)
    result = rwgps.build_ride_plan(_ROUTE, controls)
    plan, stops = result['plan'], result['stops']

    # Native miles are stored (engine emits miles, not km): 200 km ≈ 124.3 mi.
    assert 123 < plan['total_distance_miles'] < 126
    assert plan['distance_km'] == 200
    assert stops[0]['stop_order'] == 1
    assert stops[-1]['location'] == 'Downtown Finish'

    # Every stop carries a difficulty_score; a climbing segment scores > 0.
    assert all('difficulty_score' in s for s in stops)
    climbing = [s for s in stops if s['ft_per_mi']]
    assert climbing, "fixture should produce at least one climbing segment"
    assert max(s['difficulty_score'] for s in climbing) > 0


def test_build_ride_plan_steeper_segment_is_slower():
    controls = rwgps.extract_controls(_ROUTE)
    stops = rwgps.build_ride_plan(_ROUTE, controls)['stops']
    graded = [s for s in stops if s['avg_speed'] is not None and s['ft_per_mi']]
    steepest = max(graded, key=lambda s: s['ft_per_mi'])
    gentlest = min(graded, key=lambda s: s['ft_per_mi'])
    assert steepest['avg_speed'] <= gentlest['avg_speed']
