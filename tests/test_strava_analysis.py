"""Pure unit tests for services.strava_analysis.build_comparison.

No DB, no network, no real ride data. All fixtures are synthetic.

Tests A-D verify the is_extra=True extra-stop arithmetic that is ALREADY
CORRECT in the current code. They are regression guards — they pass before and
after the pointer-sync fix.

Test E (test_finish_segment_no_streams) is the PRIMARY CORRECTNESS PROOF: it
exercises the pointer-desync bug (prev_planned_dist advancing outside the
actual_cum_time guard when streams are absent) and FAILS with the unmodified
code. It must fail before the fix is applied and pass after.
"""

from services.strava_analysis import build_comparison


# ── constants ────────────────────────────────────────────────────────────────

METERS_PER_MILE = 1609.34


# ── fixture helpers ──────────────────────────────────────────────────────────

def _make_streams(*checkpoints):
    """Build a minimal Strava streams dict from synthetic checkpoints.

    Each checkpoint is (miles, arrival_min, stop_min).  Emits a (distance_m,
    time_s) arrival sample; if stop_min > 0 also emits a same-distance
    departure sample, which causes _build_stream_interpolator's binary search
    to return the departure time when queried at that mileage.
    """
    dist_m, time_s = [], []
    for miles, arrival_min, stop_min in checkpoints:
        dist_m.append(miles * METERS_PER_MILE)
        time_s.append(arrival_min * 60)
        if stop_min > 0:
            dist_m.append(miles * METERS_PER_MILE)
            time_s.append((arrival_min + stop_min) * 60)
    return {'distance': dist_m, 'time': time_s}


def _plan_stop(location, distance_miles, stop_type,
               stop_duration_min=0, cum_time_min=0,
               segment_time_min=0, seg_dist=0):
    return {
        'location': location,
        'distance_miles': float(distance_miles),
        'stop_type': stop_type,
        'stop_duration_min': int(stop_duration_min),
        'cum_time_min': int(cum_time_min),
        'segment_time_min': int(segment_time_min),
        'seg_dist': float(seg_dist),
    }


def _detected_stop(distance_miles, duration_min, matched_stop_name=None, **kwargs):
    return {
        'distance_miles': float(distance_miles),
        'duration_min': float(duration_min),
        'duration_s': float(duration_min) * 60,
        'start_time_s': 0,
        'matched_stop_name': matched_stop_name,
        'matched_stop_type': 'control' if matched_stop_name else None,
        'is_extra': matched_stop_name is None,
        'planned_duration_min': 0,
    }


def _activity(elapsed_min, distance_miles):
    return {
        'elapsed_time': int(elapsed_min * 60),
        'moving_time': int(elapsed_min * 60),
        'distance': distance_miles * METERS_PER_MILE,
        'total_elevation_gain': 0,
        'average_speed': 0,
    }


# ── shared plan for Tests A-D ────────────────────────────────────────────────

def _base_plan():
    return [
        _plan_stop('Start', 0, 'start'),
        _plan_stop('C1', 10, 'control', stop_duration_min=30, cum_time_min=50,
                   segment_time_min=20, seg_dist=10),
        _plan_stop('C2', 30, 'control', stop_duration_min=20, cum_time_min=125,
                   segment_time_min=55, seg_dist=20),
        _plan_stop('Finish', 40, 'finish', cum_time_min=135, segment_time_min=10,
                   seg_dist=10),
    ]


def _row_by_location(comparison, location):
    for row in comparison['rows']:
        if row.get('location') == location:
            return row
    return None


# ── Test A: two unplanned breaks (regression guard) ─────────────────────────

def test_planned_segment_with_two_extras():
    """C2's actual riding time correctly excludes both interleaved extra stops.

    Regression guard — passes with the current unmodified code.
    Streams: Start@0→C1@10(30min)→extra@16(8min)→extra@22(4min)→C2@30(20min)→Finish@40.
    Expected C2: seg_elapsed=55, stops_in_seg=12, actual_riding=43, speed≈27.9 mph.
    """
    streams = _make_streams(
        (0, 0, 0),
        (10, 20, 30),
        (16, 65, 8),
        (22, 85, 4),
        (30, 105, 20),
        (40, 135, 0),
    )
    detected = [
        _detected_stop(10, 30, 'C1'),
        _detected_stop(16, 8),
        _detected_stop(22, 4),
        _detected_stop(30, 20, 'C2'),
    ]
    comparison = build_comparison(
        plan_stops=_base_plan(),
        detected_stops=detected,
        activity=_activity(135, 40),
        streams=streams,
    )
    c1 = _row_by_location(comparison, 'C1')
    c2 = _row_by_location(comparison, 'C2')
    assert c1 is not None and c2 is not None
    assert c1['actual_segment_min'] == 20
    assert c2['actual_segment_min'] == 43
    assert 25.0 < c2['actual_speed_mph'] < 30.0


# ── Test B: one unplanned break (regression guard) ──────────────────────────

def test_planned_segment_with_one_extra():
    """C2's actual riding time correctly excludes one interleaved extra stop.

    Regression guard — passes with the current unmodified code.
    Streams: Start@0→C1@10(30min)→extra@16(8min)→C2@30(20min)→Finish@40.
    Expected C2: seg_elapsed=35, stops_in_seg=8, actual_riding=27.
    """
    streams = _make_streams(
        (0, 0, 0),
        (10, 20, 30),
        (16, 65, 8),
        (30, 85, 20),
        (40, 110, 0),
    )
    detected = [
        _detected_stop(10, 30, 'C1'),
        _detected_stop(16, 8),
        _detected_stop(30, 20, 'C2'),
    ]
    comparison = build_comparison(
        plan_stops=_base_plan(),
        detected_stops=detected,
        activity=_activity(110, 40),
        streams=streams,
    )
    c2 = _row_by_location(comparison, 'C2')
    assert c2 is not None
    assert c2['actual_segment_min'] == 27
    assert c2['actual_speed_mph'] is not None and c2['actual_speed_mph'] > 0


# ── Test C: zero extras (regression guard) ──────────────────────────────────

def test_planned_segment_no_extras():
    """C2's actual riding time is unchanged when no extra stops exist.

    Regression guard — passes with the current unmodified code.
    Streams: Start@0→C1@10(30min)→C2@30(20min)→Finish@40.
    C1 departs at t=50. C2 arrives at t=120 (70 min of riding). C2 departs at t=140.
    Expected C2: seg_elapsed=70, stops_in_seg=0, actual_riding=70, speed≈17.1 mph.
    """
    streams = _make_streams(
        (0, 0, 0),
        (10, 20, 30),   # C1: arrive 20, stop 30, depart 50
        (30, 120, 20),  # C2: arrive 120, stop 20, depart 140
        (40, 150, 0),   # Finish at 150
    )
    detected = [
        _detected_stop(10, 30, 'C1'),
        _detected_stop(30, 20, 'C2'),
    ]
    comparison = build_comparison(
        plan_stops=_base_plan(),
        detected_stops=detected,
        activity=_activity(150, 40),
        streams=streams,
    )
    c2 = _row_by_location(comparison, 'C2')
    assert c2 is not None
    assert c2['actual_segment_min'] == 70
    expected_speed = round(20 / (70 / 60), 1)
    assert c2['actual_speed_mph'] == expected_speed


# ── Test D: custom-plan path (regression guard) ──────────────────────────────

def test_planned_segment_custom_plan_path():
    """Passing custom_stops does not alter C2's segment arithmetic.

    Regression guard — passes with the current unmodified code.
    Same scenario as Test A; custom_stops is a copy of the base plan (used for
    display only). Segment arithmetic must be identical to Test A.
    """
    streams = _make_streams(
        (0, 0, 0),
        (10, 20, 30),
        (16, 65, 8),
        (22, 85, 4),
        (30, 105, 20),
        (40, 135, 0),
    )
    detected = [
        _detected_stop(10, 30, 'C1'),
        _detected_stop(16, 8),
        _detected_stop(22, 4),
        _detected_stop(30, 20, 'C2'),
    ]
    plan = _base_plan()
    comparison = build_comparison(
        plan_stops=plan,
        detected_stops=detected,
        activity=_activity(135, 40),
        custom_stops=list(plan),
        streams=streams,
    )
    c2 = _row_by_location(comparison, 'C2')
    assert c2 is not None
    assert c2['actual_segment_min'] == 43
    assert 25.0 < c2['actual_speed_mph'] < 30.0
    assert c2.get('custom') is not None


# ── Test E: no-streams pointer desync (PRIMARY CORRECTNESS PROOF) ────────────

def test_finish_segment_no_streams():
    """Finish-row speed is correct when Strava streams are absent.

    This is the PRIMARY CORRECTNESS PROOF for the pointer-sync fix.

    WITHOUT the fix: prev_planned_dist advances to 100 (C2's distance) even
    though actual_cum_time_min = None for C1 and C2, so from_dist and
    from_departure anchor different intervals. The Finish row computes
    seg_dist = 100 mi with stops_in_seg = 0, producing a wrong speed.

    WITH the fix: prev_planned_dist stays at 0 (Start anchor) — both planned
    pointers advance together only when actual_cum_time_min is not None.
    The Finish row computes seg_dist = 200 mi with stops_in_seg = 50 min,
    producing the correct overall average speed.

    Route: Start@0 → C1@50 (30-min stop) → C2@100 (20-min stop) → Finish@200.
    Total elapsed: 660 min (610 min riding + 30 + 20 = 660).
    No Strava streams.
    """
    plan = [
        _plan_stop('Start', 0, 'start'),
        _plan_stop('C1', 50, 'control', stop_duration_min=30, cum_time_min=200),
        _plan_stop('C2', 100, 'control', stop_duration_min=20, cum_time_min=390),
        _plan_stop('Finish', 200, 'finish', cum_time_min=730),
    ]
    detected = [
        _detected_stop(50, 30, 'C1'),
        _detected_stop(100, 20, 'C2'),
    ]
    comparison = build_comparison(
        plan_stops=plan,
        detected_stops=detected,
        activity=_activity(660, 200),
        streams=None,
    )
    finish = _row_by_location(comparison, 'Finish')
    assert finish is not None

    # 200 miles / (610 min / 60) = 19.67 mph → 19.7
    expected_speed = round(200 / (610 / 60), 1)
    assert finish['actual_speed_mph'] == expected_speed, (
        f"Expected {expected_speed} mph but got {finish['actual_speed_mph']} mph. "
        f"If this is ~9.1, the pointer-sync fix is not applied."
    )
    assert finish['actual_segment_min'] == 610
