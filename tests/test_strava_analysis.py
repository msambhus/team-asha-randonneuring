"""Regression tests for services.strava_analysis.build_comparison segment timing.

Focus: a PLANNED control's actual segment time and average speed must stay
physically sane no matter how long the rider stops AT that control and no matter
how many unplanned ("extra") breaks fall inside the leg. This guards the bug where
a multi-hour stop at a staffed control (e.g. a sleep stop on a 600K) collapsed the
control's segment to an impossible speed (~85 mph) because the segment used
`actual_arrival = actual_cum_time - stop_duration` — which, for a large stop, puts
the control's "arrival" earlier than prior waypoints.

All fixtures are synthetic (built here); no real ride data is committed. A DB-gated
test at the bottom re-confirms the real reported ride (match_id 212) when a database
is available.
"""

import os
import pytest

from services.strava_analysis import build_comparison, METERS_PER_MILE

# Constant riding pace used to synthesize streams: 15 mph = 4 min/mile.
_PACE_S_PER_MI = 240.0
_STEP_MI = 0.2


def _ride(events):
    """Synthesize a self-consistent (streams, detected_stops) pair from a script.

    events is a list of tuples:
      ('ride', miles)                      — ride `miles` at 15 mph
      ('stop', minutes, name)              — dwell in place; name=<str> makes it a
                                             matched control stop, name=None an
                                             unplanned (extra) stop.

    The detected_stops durations exactly match the stream dwells, so stop time
    subtracted by build_comparison equals stop time present in the wall clock.
    """
    dist_m = [0.0]
    time_s = [0.0]
    vel = [5.0]
    d = 0.0
    t = 0.0
    detected = []
    for ev in events:
        if ev[0] == 'ride':
            steps = int(round(ev[1] / _STEP_MI))
            for _ in range(steps):
                d += _STEP_MI
                t += _PACE_S_PER_MI * _STEP_MI
                dist_m.append(d * METERS_PER_MILE)
                time_s.append(t)
                vel.append(5.0)
        else:  # stop
            _, mins, name = ev
            start_s = t
            t += mins * 60.0
            dist_m.append(d * METERS_PER_MILE)
            time_s.append(t)
            vel.append(0.0)
            detected.append({
                'distance_miles': round(d, 2),
                'duration_min': float(mins),
                'start_time_s': int(start_s),
                'end_time_s': int(t),
                'matched_stop_name': name,
                'is_extra': name is None,
            })
    streams = {'distance': dist_m, 'time': time_s, 'velocity_smooth': vel}
    return streams, detected


def _plan(markers):
    """Build plan_stops from (location, distance_miles) markers at 15 mph pace."""
    stops = []
    cum = 0.0
    prev = 0.0
    for i, (loc, mi) in enumerate(markers):
        seg_dist = mi - prev
        seg_min = seg_dist * 4.0  # 15 mph
        cum += seg_min
        stops.append({
            'location': loc,
            'stop_type': 'start' if i == 0 else 'control',
            'distance_miles': float(mi),
            'seg_dist': round(seg_dist, 1),
            'segment_time_min': int(round(seg_min)),
            'stop_duration_min': 0,
            'cum_time_min': int(round(cum)),
        })
        prev = mi
    return stops


def _activity(total_mi, elapsed_min, moving_min):
    return {
        'distance': total_mi * METERS_PER_MILE,
        'moving_time': moving_min * 60,
        'elapsed_time': elapsed_min * 60,
        'total_elevation_gain': 0,
        'average_speed': 6.7,  # ~15 mph in m/s
    }


def _planned_row(comparison, location):
    for r in comparison['rows']:
        if not r.get('is_extra') and r.get('location') == location:
            return r
    raise AssertionError(f"planned row {location!r} not found")


# Markers shared by several tests: Start(0) B(60) C(120) D(180).
_MARKERS = [('Start', 0), ('B', 60), ('C', 120), ('D', 180)]


def _run(events, custom=False):
    streams, detected = _ride(events)
    plan = _plan(_MARKERS)
    total_mi = streams['distance'][-1] / METERS_PER_MILE
    elapsed_min = streams['time'][-1] / 60.0
    moving_min = sum(1 for v in streams['velocity_smooth'] if v > 0.5) * (_STEP_MI * 4.0)
    activity = _activity(total_mi, elapsed_min, moving_min)
    custom_stops = _plan(_MARKERS) if custom else None
    return build_comparison(
        plan_stops=plan, detected_stops=detected, activity=activity,
        custom_stops=custom_stops, plan_start_time=None,
        actual_start_time=None, streams=streams)


# ── the reported bug: a huge stop AT a control must not corrupt its segment ──

def test_long_control_stop_does_not_corrupt_segment_speed():
    """A 200-min stop at control C (endpoint of leg B→C) must leave B→C's speed
    sane (~15 mph), not the ~90 mph the old arrival-based formula produced."""
    events = [
        ('ride', 59.6), ('stop', 10, 'B'),   # B stop detected just before marker 60
        ('ride', 30.4), ('stop', 8, None),   # unplanned break at ~90 mi
        ('ride', 30.0),                       # reach C marker (120 mi)
        ('ride', 0.3), ('stop', 200, 'C'),   # 200-min sleep just past the marker
        ('ride', 59.7),                       # ride on to D (180 mi)
    ]
    comp = _run(events)
    c = _planned_row(comp, 'C')
    # Leg B→C is 60 mi at 15 mph = 240 min riding; speed must be physically sane.
    assert 12.0 <= c['actual_speed_mph'] <= 18.0, c['actual_speed_mph']
    assert 210 <= c['actual_segment_min'] <= 270, c['actual_segment_min']


def test_long_control_stop_matches_no_stop_baseline():
    """The control's own stop length must not change its segment speed at all:
    the same leg with a 200-min vs a 5-min stop at C yields the same B→C speed."""
    base = [('ride', 59.6), ('stop', 10, 'B'), ('ride', 30.4), ('stop', 8, None),
            ('ride', 30.0), ('ride', 0.3)]
    long_stop = _run(base + [('stop', 200, 'C'), ('ride', 59.7)])
    short_stop = _run(base + [('stop', 5, 'C'), ('ride', 59.7)])
    assert _planned_row(long_stop, 'C')['actual_speed_mph'] == \
        _planned_row(short_stop, 'C')['actual_speed_mph']


def test_zero_breaks_leg_unchanged():
    """A leg with no unplanned breaks: riding time == pure elapsed at 15 mph."""
    events = [('ride', 59.6), ('stop', 10, 'B'), ('ride', 60.4), ('stop', 12, 'C'),
              ('ride', 60.0), ('stop', 4, 'D')]
    comp = _run(events)
    c = _planned_row(comp, 'C')  # leg B→C, 60 mi, no extra breaks
    assert 14.0 <= c['actual_speed_mph'] <= 16.0, c['actual_speed_mph']
    assert abs(c['actual_segment_min'] - 240) <= 5, c['actual_segment_min']


def test_two_breaks_in_one_leg_subtracted_once_each():
    """Two unplanned breaks inside leg B→C are each removed exactly once — the
    riding time stays ~240 min and the speed stays ~15 mph."""
    events = [
        ('ride', 59.6), ('stop', 10, 'B'),
        ('ride', 20.4), ('stop', 15, None),   # break 1 at ~80 mi
        ('ride', 20.0), ('stop', 9, None),    # break 2 at ~100 mi
        ('ride', 20.0), ('stop', 30, 'C'),    # reach C at 120 mi
        ('ride', 60.0),
    ]
    comp = _run(events)
    c = _planned_row(comp, 'C')
    assert 14.0 <= c['actual_speed_mph'] <= 16.0, c['actual_speed_mph']
    assert abs(c['actual_segment_min'] - 240) <= 6, c['actual_segment_min']


def test_custom_plan_path_also_sane():
    """The custom-plan display path (custom_stops provided) must be equally sane."""
    events = [
        ('ride', 59.6), ('stop', 10, 'B'),
        ('ride', 30.4), ('stop', 8, None),
        ('ride', 30.0), ('ride', 0.3), ('stop', 200, 'C'),
        ('ride', 59.7),
    ]
    comp = _run(events, custom=True)
    c = _planned_row(comp, 'C')
    assert 12.0 <= c['actual_speed_mph'] <= 18.0, c['actual_speed_mph']


# ── DB-gated real-ride confirmation (the reported ride) ──────────────────────

@pytest.mark.skipif(
    not (os.environ.get('DATABASE_URL') or os.environ.get('TEST_DATABASE_URL')),
    reason="no database configured")
def test_real_ride_control5_speed_is_sane(app):
    """match_id 212 (Mendocino Coast 600K, rider 6 / ride 103, custom-plan path):
    Control #5 (183.0→227.0 mi leg) must render a physically sane speed, not the
    reported ~85 mph. The rider slept ~200 min at the staffed control; the correct
    riding time for the 44-mi leg is ~231 min (~11 mph), consistent with the
    neighbouring segments (~12 mph)."""
    with app.app_context():
        from models import (get_strava_ride_match, get_ride_plan_stops,
                            get_custom_plan)
        from services.custom_plan_service import get_merged_plan_stops
        from services.strava_analysis import fetch_and_analyze

        match = dict(get_strava_ride_match(6, 103))
        assert match['id'] == 212
        plan_stops = get_ride_plan_stops(58)
        custom_plan = get_custom_plan(6, 58)
        custom_stops, _ = get_merged_plan_stops(custom_plan['id'])

        analysis = fetch_and_analyze(
            rider_id=6, match_id=match['id'],
            strava_activity_id=match['strava_activity_id'],
            plan_stops=custom_stops)
        streams = analysis['streams']

        base_for_comparison = []
        cum = 0
        prev = 0.0
        for s in plan_stops:
            sd = dict(s)
            sd['distance_miles'] = float(sd['distance_miles'] or 0)
            sd['segment_time_min'] = int(sd.get('segment_time_min') or 0)
            sd['stop_duration_min'] = int(sd.get('stop_duration_min') or 0)
            sd['seg_dist'] = round(sd['distance_miles'] - prev, 1)
            cum += sd['segment_time_min'] + sd['stop_duration_min']
            sd['cum_time_min'] = cum
            sd['arrival_time_min'] = cum - sd['stop_duration_min']
            prev = sd['distance_miles']
            base_for_comparison.append(sd)

        comparison = build_comparison(
            plan_stops=custom_stops, detected_stops=analysis['detected_stops'],
            activity=match, custom_stops=base_for_comparison, plan_start_time=None,
            actual_start_time=match.get('start_date_local'), streams=streams)

    planned = [r for r in comparison['rows'] if not r.get('is_extra')]
    c5 = min(planned, key=lambda r: abs((r.get('distance_miles') or 0) - 227.0))
    assert c5['actual_speed_mph'] is not None
    assert 8.0 <= c5['actual_speed_mph'] <= 15.0, (
        f"Control #5 speed {c5['actual_speed_mph']} mph is not physically sane")
    assert 190 <= c5['actual_segment_min'] <= 270, c5['actual_segment_min']
