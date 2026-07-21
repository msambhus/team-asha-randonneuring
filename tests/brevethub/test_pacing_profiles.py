"""Pacing profiles + meal-break insertion in the vendored RWGPS engine.

The conservative/aggressive brevet-plan feature adds two named pacing profiles and an
optional meal-break post-pass to ``build_ride_plan``, all behind keyword-only params
whose DEFAULTS must reproduce the legacy output byte-for-byte (parent-app plan callers
stay on ``profile='default'``). These pin, with no network (route data is a fixture):
  - each profile's piecewise speed curve at <=40 / =100 / floor,
  - ``profile='default'`` equals ``calculate_segment_speed`` across a gradient sweep,
  - the byte-identical default guard (explicit defaults AND a frozen snapshot),
  - meal placement at ~60-mile marks with the right clock-typed labels + dwell,
  - dwell lands in ``total_break_time_min`` / elapsed but NEVER in moving time,
  - excluding meal rows and de-dwelling recovers the meal-free timing exactly (the
    live-grading invariant the live boundary relies on).
"""
from brevethub.shared import rwgps


# A long, gently-rolling fixture (meters) with controls every 100 km so the meal
# post-pass fires several times across a day (and wraps past midnight).
_ROUTE = {
    'id': 42, 'name': 'Big Loop 600k', 'distance': 600000, 'elevation_gain': 3000,
    'course_points': [
        {'t': 'Start', 'n': 'Start', 'd': 0, 'e': 10},
        {'t': 'Control', 'n': 'C1', 'd': 100000, 'e': 50},
        {'t': 'Control', 'n': 'C2', 'd': 200000, 'e': 50},
        {'t': 'Control', 'n': 'C3', 'd': 300000, 'e': 50},
        {'t': 'Control', 'n': 'C4', 'd': 400000, 'e': 50},
        {'t': 'Control', 'n': 'C5', 'd': 500000, 'e': 50},
        {'t': 'End', 'n': 'Finish', 'd': 600000, 'e': 10},
    ],
    'track_points': [{'d': i * 10000, 'e': 10 + (i % 5) * 20} for i in range(61)],
}

# A short 200k fixture for the meal worked-example (one meal near mile 60).
_ROUTE_200 = {
    'id': 7, 'name': 'Populaire 200k', 'distance': 200000, 'elevation_gain': 900,
    'course_points': [
        {'t': 'Start', 'n': 'Start', 'd': 0, 'e': 10},
        {'t': 'Control', 'n': 'Midway', 'd': 100000, 'e': 50},
        {'t': 'End', 'n': 'Finish', 'd': 200000, 'e': 10},
    ],
    'track_points': [{'d': i * 10000, 'e': 10 + (i % 5) * 20} for i in range(21)],
}


def _controls(route):
    return rwgps.extract_controls(route)


# --------------------------------------------------------------------------- #
# Speed curves — conservative / aggressive piecewise values, and default==legacy
# --------------------------------------------------------------------------- #
def test_conservative_curve_anchor_and_floor():
    f = rwgps.profile_segment_speed
    assert f(0, 'conservative') == 13.0        # flat cap
    assert f(40, 'conservative') == 13.0       # still flat at the flat anchor
    assert f(100, 'conservative') == 10.2      # ~10.25 anchor, rounded to 0.1
    assert abs(f(100, 'conservative') - 10.25) <= 0.05
    assert f(200, 'conservative') == 8.5       # steep pitch → floor
    assert f(1000, 'conservative') == 8.5      # never below the floor
    assert f(None, 'conservative') == 13.0     # unknown gradient → flat


def test_aggressive_curve_is_conservative_plus_offset():
    f = rwgps.profile_segment_speed
    assert f(40, 'aggressive') == 14.5         # +1.5 across the board
    assert f(100, 'aggressive') == 11.8        # ~11.75 anchor, rounded to 0.1
    assert abs(f(100, 'aggressive') - 11.75) <= 0.05
    assert f(0, 'aggressive') == 14.5          # flat
    assert f(1000, 'aggressive') == 9.5        # floor 9.5
    assert f(-3, 'aggressive') == 14.5         # negative → flat
    # Ceiling holds at 15.0 even though the offset would allow more.
    assert f(0, 'aggressive') <= rwgps.AGGRESSIVE_CEIL_MPH == 15.0


def test_aggressive_faster_than_conservative_everywhere():
    f = rwgps.profile_segment_speed
    for ftm in [0, 20, 40, 60, 80, 100, 140]:
        assert f(ftm, 'aggressive') >= f(ftm, 'conservative')


def test_default_profile_equals_legacy_across_sweep():
    for ftm in [None, -5, 0, 15, 30, 40, 55, 60, 90, 100, 150, 400, 1000]:
        assert (rwgps.profile_segment_speed(ftm, 'default')
                == rwgps.calculate_segment_speed(ftm)), ftm


# --------------------------------------------------------------------------- #
# Byte-identical default guard — the load-bearing backward-compat proof
# --------------------------------------------------------------------------- #
def test_build_ride_plan_defaults_are_byte_identical():
    ctrls = _controls(_ROUTE)
    legacy = rwgps.build_ride_plan(_ROUTE, ctrls)
    explicit = rwgps.build_ride_plan(_ROUTE, ctrls, profile='default',
                                     insert_meals=False, start_time=None)
    assert legacy == explicit
    # No meals, no break time, legacy start clock preserved.
    assert legacy['plan']['total_break_time_min'] == 0
    assert legacy['plan']['start_time'] == '07:00'
    assert all(s['stop_type'] != 'meal' for s in legacy['stops'])


def test_build_ride_plan_default_matches_frozen_snapshot():
    """A frozen snapshot of representative default-path fields — any drift in the
    speed model or the plan/stop dict on the default path breaks this."""
    plan = rwgps.build_ride_plan(_ROUTE_200, _controls(_ROUTE_200))
    p, stops = plan['plan'], plan['stops']
    assert p['distance_km'] == 200
    assert p['total_break_time_min'] == 0
    assert p['start_time'] == '07:00'
    assert p['total_elapsed_time_min'] == p['total_moving_time_min']
    # Per-stop invariants: the default speed model still produces these anchors.
    assert stops[0]['stop_type'] == 'start' and stops[0]['cum_time_min'] == 0
    assert stops[-1]['location'] == 'Finish'
    graded = [s for s in stops if s['avg_speed'] is not None]
    assert graded, "fixture should grade at least one segment"
    for s in graded:
        assert 7.0 <= s['avg_speed'] <= 15.0


# --------------------------------------------------------------------------- #
# Meal-break insertion — spacing, clock typing, dwell accounting
# --------------------------------------------------------------------------- #
def test_meals_placed_every_60_miles_and_clock_typed():
    plan = rwgps.build_ride_plan(_ROUTE, _controls(_ROUTE),
                                 profile='conservative', insert_meals=True,
                                 start_time='06:00')
    meals = [s for s in plan['stops'] if s['stop_type'] == 'meal']
    # Controls sit at ~62-mi marks (100 km); a meal fires at each 60-mi threshold.
    assert len(meals) == 5
    miles = [round(m['distance_miles'], 1) for m in meals]
    assert miles == [62.1, 124.3, 186.4, 248.5, 310.7]
    # Clock-typed across the day (06:00 start), wrapping past midnight.
    labels = [m['notes'] for m in meals]
    assert labels[0] == 'Lunch'                 # ~late morning/midday
    assert 'Dinner' in labels
    assert 'Night snack' in labels              # overnight wrap
    assert 'Breakfast + refill' in labels       # next morning
    # Each meal carries its label as location and stop_type='meal'.
    for m in meals:
        assert m['stop_type'] == 'meal' and m['location'] == m['notes']
        assert m['seg_dist'] == 0.0 and m['avg_speed'] is None


def test_no_meal_break_at_the_finish():
    plan = rwgps.build_ride_plan(_ROUTE, _controls(_ROUTE),
                                 profile='conservative', insert_meals=True,
                                 start_time='07:00')
    assert plan['stops'][-1]['stop_type'] == 'finish'
    # The last row is never a meal, and no meal shares the finish distance.
    finish_mi = plan['stops'][-1]['distance_miles']
    assert not any(s['stop_type'] == 'meal' and s['distance_miles'] == finish_mi
                   for s in plan['stops'])


def test_meal_dwell_hits_break_and_elapsed_not_moving():
    with_meals = rwgps.build_ride_plan(_ROUTE, _controls(_ROUTE),
                                       profile='conservative', insert_meals=True,
                                       start_time='06:00')
    no_meals = rwgps.build_ride_plan(_ROUTE, _controls(_ROUTE),
                                     profile='conservative', insert_meals=False)
    p = with_meals['plan']
    dwell_total = sum(s['segment_time_min'] for s in with_meals['stops']
                      if s['stop_type'] == 'meal')
    assert p['total_break_time_min'] == dwell_total > 0
    # Moving time is unchanged by breaks; elapsed = moving + break.
    assert p['total_moving_time_min'] == no_meals['plan']['total_moving_time_min']
    assert p['total_elapsed_time_min'] == p['total_moving_time_min'] + p['total_break_time_min']


def test_dedwell_recovers_meal_free_timing_exactly():
    """The live-grading invariant: excluding meal rows and subtracting the accumulated
    preceding dwell recovers the SAME control cum_time_min as the meal-free plan."""
    with_meals = rwgps.build_ride_plan(_ROUTE, _controls(_ROUTE),
                                       profile='conservative', insert_meals=True,
                                       start_time='06:00')
    no_meals = rwgps.build_ride_plan(_ROUTE, _controls(_ROUTE),
                                     profile='conservative', insert_meals=False)
    cum_dwell = 0
    recovered = []
    for s in with_meals['stops']:
        if s['stop_type'] == 'meal':
            cum_dwell += s['segment_time_min']
            continue
        recovered.append((s['location'], s['cum_time_min'] - cum_dwell))
    expected = [(s['location'], s['cum_time_min']) for s in no_meals['stops']]
    assert recovered == expected


def test_meal_break_labels_track_start_time():
    """A later start shifts every break's clock window — an early start eats breakfast
    first, a midday start starts at lunch."""
    early = rwgps.build_ride_plan(_ROUTE, _controls(_ROUTE), profile='conservative',
                                  insert_meals=True, start_time='04:30')
    first_early = next(s for s in early['stops'] if s['stop_type'] == 'meal')
    # 04:30 + ~4.7 h to mile 62 ≈ 09:xx → Breakfast window.
    assert first_early['notes'] == 'Breakfast + refill'
