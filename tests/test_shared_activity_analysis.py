"""Unit tests for the plan-free per-segment engine in shared/strava_analysis.py.

Covers the club-agnostic per-segment metric computation the mission requires in the
shared library — average HR/cadence/power, normalized power, gradient, elevation
gain / climb — plus the ``build_activity_analysis`` entrypoint that partitions a ride
into inter-stop legs from ``detect_stops`` (using each stop's exact clock-time
boundary, so a dwell never leaks into the adjacent leg's pace). No DB, no Flask, no
network — synthetic 1 Hz streams in, expected values out.
"""
from shared import strava_analysis as sa


# --------------------------------------------------------------------------- #
# Per-segment metric helpers (positive-sample means; NP; signed grade; climb).
# --------------------------------------------------------------------------- #
def test_avg_stream_in_range_positive_only():
    dist_mi = [0.0, 1.0, 2.0, 3.0]
    stream = [100, 0, 200, None]         # zero + None excluded
    assert sa._avg_stream_in_range(stream, dist_mi, 0.0, 3.0) == 150
    assert sa._avg_stream_in_range([], dist_mi, 0.0, 3.0) is None


def test_avg_grade_in_range_keeps_negatives():
    dist_mi = [0.0, 1.0, 2.0]
    grade = [6.0, -4.0, 2.0]             # a descent must not be dropped
    assert sa._avg_grade_in_range(grade, dist_mi, 0.0, 2.0) == round((6 - 4 + 2) / 3, 1)


def test_elev_gain_in_range_sums_positive_deltas_in_feet():
    dist_mi = [0.0, 1.0, 2.0, 3.0]
    altitude_m = [100.0, 130.0, 120.0, 150.0]   # +30, -10, +30 -> +60 m of climbing
    assert sa._elev_gain_in_range(altitude_m, dist_mi, 0.0, 3.0) == round(60 * 3.28084)


def test_normalized_power_needs_thirty_samples():
    dist_mi = [i * 0.01 for i in range(20)]
    assert sa._normalized_power_in_range([200] * 20, dist_mi, 0.0, 1.0) is None
    dist_mi = [i * 0.01 for i in range(40)]
    # Constant 200 W over >=30 samples -> NP == 200.
    assert sa._normalized_power_in_range([200] * 40, dist_mi, 0.0, 1.0) == 200


# --------------------------------------------------------------------------- #
# build_activity_analysis — inter-stop legs, leak-free pace, summary, map.
# --------------------------------------------------------------------------- #
_MPS = 6.70560  # 15 mph in m/s, at 1 Hz sampling


def _ride_with_stop():
    """10 mi @ 15 mph, a 5-min stop, then 10 mi @ 15 mph — realistic 1 Hz streams."""
    dist = [0.0]; t = [0.0]; vel = [5.0]
    hr = [140]; watts = [200]; alt = [100.0]; grade = [2.0]; ll = [[37.0, -122.0]]

    def ride(secs):
        for _ in range(secs):
            dist.append(dist[-1] + _MPS); t.append(t[-1] + 1); vel.append(5.0)
            hr.append(150); watts.append(210); alt.append(alt[-1] + 0.05)
            grade.append(3.0); ll.append([ll[-1][0] + 1e-4, ll[-1][1]])

    def stop(secs):
        for _ in range(secs):
            dist.append(dist[-1]); t.append(t[-1] + 1); vel.append(0.0)
            hr.append(0); watts.append(0); alt.append(alt[-1])
            grade.append(0.0); ll.append(ll[-1])

    ride(2400); stop(300); ride(2400)
    return {'distance': dist, 'time': t, 'velocity_smooth': vel, 'heartrate': hr,
            'watts': watts, 'altitude': alt, 'grade_smooth': grade, 'latlng': ll}


def test_build_activity_analysis_legs_are_stop_leak_free():
    res = sa.build_activity_analysis(_ride_with_stop())
    assert len(res['stops']) == 1
    assert res['stops'][0]['duration_min'] == 5.0
    # Two 10-mi legs at a true 15 mph — the stop dwell must not deflate leg 2.
    speeds = [s['speed_mph'] for s in res['segments']]
    assert speeds == [15.0, 15.0], speeds
    # Effort metrics populate from the reused helpers.
    seg = res['segments'][0]
    assert seg['avg_hr'] == 150 and seg['np_watts'] == 210
    assert seg['climb_ft_per_mi'] is not None and seg['grade_pct'] == 3.0


def test_build_activity_analysis_enriches_summary_and_map():
    res = sa.build_activity_analysis(
        _ride_with_stop(),
        activity={'elapsed_time': 5100, 'moving_time': 4800,
                  'total_elevation_gain': 300, 'average_speed': 6.7056,
                  'name': 'Test Ride'})
    assert res['summary']['name'] == 'Test Ride'
    assert res['summary']['stopped_time_s'] == 300
    assert res['summary']['total_elevation_ft'] == round(300 * 3.28084)
    assert res['map'] is not None and len(res['map']['track']) >= 2


def test_build_activity_analysis_no_stops_single_leg():
    streams = _ride_with_stop()
    # Truncate to the first riding block (no stop) -> exactly one full leg.
    streams = {k: v[:2401] for k, v in streams.items()}
    res = sa.build_activity_analysis(streams)
    assert res['stops'] == []
    assert len(res['segments']) == 1
    assert res['segments'][0]['speed_mph'] == 15.0


def test_build_activity_analysis_empty_streams_safe():
    res = sa.build_activity_analysis({})
    assert res['segments'] == [] and res['stops'] == [] and res['map'] is None
