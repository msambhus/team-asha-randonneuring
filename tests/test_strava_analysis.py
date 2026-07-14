"""Pure unit tests for services.strava_analysis.build_comparison.

No DB, no network, no real ride data. All fixtures are synthetic.

Tests A-D verify the is_extra=True extra-stop arithmetic that is ALREADY
CORRECT in the current code. They are regression guards — they pass before and
after the pointer-sync fix.

Test E (test_finish_segment_no_streams) is the PRIMARY CORRECTNESS PROOF for the
no-streams pointer-sync fix: it exercises the pointer-desync bug
(prev_planned_dist advancing outside the actual_cum_time guard when streams are
absent) and FAILS with the unmodified code. It must fail before the fix is
applied and pass after.

Tests F-H are the PRIMARY CORRECTNESS PROOF for the streams-present interpolation
fix. A non-monotonic Strava distance stream (a GPS spike/dip) makes the plain
binary search in _build_stream_interpolator mis-bracket a control's distance and
return a too-early time, collapsing that leg's elapsed time and inflating its
average speed to a physically-impossible value. The monotonic-safe interpolation
resolves a control distance to the time it was *last* reached, recovering the true
arrival. Test F fails with the unmodified interpolator (impossible mph) and passes
after; Tests G/H guard that well-formed (monotonic) streams are unchanged.

Test Step-A (test_step_a_real_ride_control5_speed_is_sane) is the DB-gated real
reproduction the plan requires: it runs match_id 212 (rider 6 / ride 103) through
the exact custom-plan build_comparison path and asserts Control #5 renders a sane
speed. It SKIPS unless TEST_DATABASE_URL/DATABASE_URL is set (a skip is UNPROVEN,
not passing) and commits no real ride data — it only reads stored rows at runtime.
"""

import os

import pytest

from services.strava_analysis import build_comparison, _build_stream_interpolator


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


# ── Test F: non-monotonic stream (PRIMARY PROOF, streams-present) ─────────────

# A GPS distance stream for a 10→30 mi leg with a spurious forward spike to 35 mi
# early in the leg. C1 departs at t=50 min; the rider truly reaches (and passes)
# 30 mi at t=130 min. The plain binary search brackets the spike and resolves
# interp(30) to ~61 min — long before the rider was there — so C2's segment
# collapses to ~11 min and its speed reads ~109 mph. The monotonic-safe path
# resolves interp(30) to the last upward crossing (t=150, the departure sample),
# giving a physically sane speed.
_SPIKE_DIST_MI = [0, 10, 10, 20, 35, 25, 30, 30, 40]
_SPIKE_TIME_MIN = [0, 20, 50, 60, 62, 90, 130, 150, 170]


def _spike_streams():
    return {
        'distance': [mi * METERS_PER_MILE for mi in _SPIKE_DIST_MI],
        'time': [t * 60 for t in _SPIKE_TIME_MIN],
    }


def test_non_monotonic_stream_speed_is_physically_sane():
    """A GPS spike in the distance stream no longer inflates a planned leg's speed.

    Fails with the unmodified interpolator (C2 reads ~109 mph); passes after.
    """
    plan = [
        _plan_stop('Start', 0, 'start'),
        _plan_stop('C1', 10, 'control', stop_duration_min=30, cum_time_min=50,
                   segment_time_min=20, seg_dist=10),
        _plan_stop('C2', 30, 'control', stop_duration_min=0, cum_time_min=125,
                   segment_time_min=75, seg_dist=20),
        _plan_stop('Finish', 40, 'finish', cum_time_min=135, segment_time_min=10,
                   seg_dist=10),
    ]
    detected = [_detected_stop(10, 30, 'C1')]
    comparison = build_comparison(
        plan_stops=plan,
        detected_stops=detected,
        activity=_activity(170, 40),
        streams=_spike_streams(),
    )
    c2 = _row_by_location(comparison, 'C2')
    assert c2 is not None
    # 20 mi over ~100 min of riding ≈ 12 mph — a possible brevet pace, not 109.
    assert c2['actual_speed_mph'] is not None
    assert 5.0 < c2['actual_speed_mph'] < 40.0, (
        f"C2 speed {c2['actual_speed_mph']} mph is physically impossible; "
        f"the monotonic-safe interpolation fix is not applied."
    )
    # True arrival at 30 mi is t=130 (departure sample 150), C1 departs at 50,
    # so riding time is 100 min with no unplanned stops in the leg.
    assert c2['actual_segment_min'] == 100


def test_interpolator_recovers_true_time_on_spike():
    """_build_stream_interpolator resolves a control distance past a GPS spike.

    The naive bracket search returns ~61 min for 30 mi; the fix returns 150 min
    (the last time the odometer was at 30 mi — the departure sample).
    """
    interp = _build_stream_interpolator(_spike_streams())
    assert interp is not None
    assert abs(interp(30) - 150.0) < 0.5


# ── Test G/H: well-formed streams unchanged (happy-path guard) ────────────────

def test_interpolator_unchanged_on_monotonic_stream():
    """On a non-decreasing stream the interpolator matches a plain binary search.

    Guards that the monotonic-safe fallback never alters well-formed rides,
    including the "return departure time at a stop plateau" behaviour.
    """
    # Start@0 → arrive 10 @20, depart @50 (30-min stop) → arrive 30 @120,
    # depart @140 (20-min stop) → finish 40 @150.
    dist_mi = [0, 10, 10, 30, 30, 40]
    time_min = [0, 20, 50, 120, 140, 150]
    streams = {
        'distance': [mi * METERS_PER_MILE for mi in dist_mi],
        'time': [t * 60 for t in time_min],
    }

    def reference(target):
        dm = [d / METERS_PER_MILE for d in streams['distance']]
        ts = streams['time']
        if target <= 0:
            return 0.0
        if target >= dm[-1]:
            return ts[-1] / 60.0
        lo, hi = 0, len(dm) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if dm[mid] <= target:
                lo = mid
            else:
                hi = mid
        d0, d1 = dm[lo], dm[hi]
        t0, t1 = ts[lo], ts[hi]
        if d1 == d0:
            return t0 / 60.0
        return (t0 + (target - d0) / (d1 - d0) * (t1 - t0)) / 60.0

    interp = _build_stream_interpolator(streams)
    for target in (5, 10, 20, 30, 35, 40):
        assert abs(interp(target) - reference(target)) < 1e-9, (
            f"interp({target}) diverged from the binary-search reference on a "
            f"monotonic stream"
        )
    # Departure semantics at the 30-mi stop: last sample there is t=140.
    assert abs(interp(30) - 140.0) < 1e-9


# ── Step A: DB-gated real-ride reproduction (match_id 212) ────────────────────

_STEP_A_DB = os.environ.get('TEST_DATABASE_URL') or os.environ.get('DATABASE_URL')
_STEP_A_REASON = (
    "Step A real-ride reproduction is DB-gated: set TEST_DATABASE_URL/DATABASE_URL "
    "(and optionally STEP_A_* id overrides) to run match_id 212 through the "
    "custom-plan path. A skip here is UNPROVEN, not passing."
)


@pytest.mark.skipif(not _STEP_A_DB, reason=_STEP_A_REASON)
def test_step_a_real_ride_control5_speed_is_sane(app):
    """Step A: match_id 212 custom-plan path renders Control #5 at a sane speed.

    Reproduces the reported bug on the real ride (rider 6 / ride 103 / plan 58,
    Mendocino Coast 600K) by running the stored streams + detected stops + base
    plan + the rider's custom plan through build_comparison EXACTLY as
    routes/riders.py ride_strava_analysis does (custom_stops / base_for_comparison
    path). Confirms Control #5 (~227.0 mi) no longer renders the impossible
    ~85 mph / ~199 min segment, and dumps the Step-A intermediate terms.

    Reads stored rows at runtime only — commits no real ride data. This is the
    blocking acceptance gate the synthetic tests cannot satisfy; if it FAILS with
    a DB present, the interpolation diagnosis was wrong for this ride (e.g. a
    custom course-mile vs GPS-odometer scale mismatch) — rewind, do not merge.
    """
    rider_id = int(os.environ.get('STEP_A_RIDER_ID', '6'))
    ride_id = int(os.environ.get('STEP_A_RIDE_ID', '103'))
    base_plan_id = int(os.environ.get('STEP_A_BASE_PLAN_ID', '58'))
    match_id_expected = int(os.environ.get('STEP_A_MATCH_ID', '212'))
    control5_mi = float(os.environ.get('STEP_A_CONTROL5_MI', '227.0'))
    control4_mi = float(os.environ.get('STEP_A_CONTROL4_MI', '183.0'))

    with app.app_context():
        from models import (get_strava_ride_match, get_ride_plan_stops,
                            get_custom_plan)
        from services.custom_plan_service import get_merged_plan_stops
        from services.strava_analysis import fetch_and_analyze

        match_row = get_strava_ride_match(rider_id, ride_id)
        assert match_row is not None, (
            f"no Strava match for rider {rider_id} ride {ride_id}")
        match = dict(match_row)
        assert match['id'] == match_id_expected, (
            f"expected match_id {match_id_expected}, got {match['id']}")

        plan_stops = get_ride_plan_stops(base_plan_id)
        assert plan_stops, f"no plan stops for base plan {base_plan_id}"

        custom_plan = get_custom_plan(rider_id, base_plan_id)
        assert custom_plan is not None, (
            "expected a custom plan for this rider/base plan (custom path)")
        custom_stops, _ = get_merged_plan_stops(custom_plan['id'])
        assert custom_stops, "custom plan has no merged stops"
        primary_stops = custom_stops

        analysis = fetch_and_analyze(
            rider_id=rider_id,
            match_id=match['id'],
            strava_activity_id=match['strava_activity_id'],
            plan_stops=primary_stops,
        )
        assert not analysis.get('error'), analysis.get('error')
        streams = analysis.get('streams')
        assert streams, "expected cached activity_streams for match 212"

        # Recompute the base plan's cumulative times exactly like the route.
        base_for_comparison = []
        cum = 0
        prev_dist = 0.0
        for s in plan_stops:
            sd = dict(s)
            sd['distance_miles'] = (
                float(sd['distance_miles']) if sd.get('distance_miles') is not None else 0)
            sd['segment_time_min'] = int(sd.get('segment_time_min') or 0)
            sd['stop_duration_min'] = int(sd.get('stop_duration_min') or 0)
            sd['seg_dist'] = round(sd['distance_miles'] - prev_dist, 1)
            cum += sd['segment_time_min'] + sd['stop_duration_min']
            sd['cum_time_min'] = cum
            sd['arrival_time_min'] = cum - sd['stop_duration_min']
            prev_dist = sd['distance_miles']
            base_for_comparison.append(sd)

        comparison = build_comparison(
            plan_stops=primary_stops,
            detected_stops=analysis['detected_stops'],
            activity=match,
            custom_stops=base_for_comparison,
            plan_start_time=None,
            actual_start_time=match.get('start_date_local'),
            streams=streams,
        )

        interp = _build_stream_interpolator(streams)

    # Locate the Control #5 planned row (closest planned row to ~227.0 mi).
    planned = [r for r in comparison['rows'] if not r.get('is_extra')]
    assert planned, "no planned rows in comparison"
    c5 = min(planned, key=lambda r: abs((r.get('distance_miles') or 0) - control5_mi))

    # Step-A diagnostic dump (plan condition 1).
    dist_miles = [d / METERS_PER_MILE for d in streams['distance']]
    window = [d for d in dist_miles if control4_mi <= d <= control5_mi]
    window_monotonic = all(window[i] <= window[i + 1] for i in range(len(window) - 1))
    print(
        "\n[Step A] match_id=%s Control#5 dist=%.1f arrival=%s cum=%s seg_min=%s "
        "speed_mph=%s" % (
            match['id'], c5.get('distance_miles'),
            c5.get('actual_arrival_time_min'), c5.get('actual_cum_time_min'),
            c5.get('actual_segment_min'), c5.get('actual_speed_mph')))
    print(
        "[Step A] interp(%.1f)=%.1f interp(%.1f)=%.1f seg_elapsed=%.1f "
        "dist_miles[-1]=%.1f window_monotonic=%s" % (
            control4_mi, interp(control4_mi), control5_mi, interp(control5_mi),
            interp(control5_mi) - interp(control4_mi), dist_miles[-1],
            window_monotonic))

    # Acceptance (plan condition 10): Control #5 is physically sane — a finite,
    # brevet-plausible pace, not the ~85 mph / ~199 min corruption.
    assert c5.get('actual_speed_mph') is not None, "Control #5 speed is blank"
    assert 3.0 < c5['actual_speed_mph'] < 40.0, (
        f"Control #5 speed {c5['actual_speed_mph']} mph is physically impossible; "
        f"the fix does not resolve this ride — rewind, do not merge.")
    assert c5.get('actual_segment_min') is not None and c5['actual_segment_min'] < 600, (
        f"Control #5 segment {c5.get('actual_segment_min')} min is implausible")
