"""Unit tests for shared/strava_analysis.py — the extracted, framework-free engine.

These pin the club-agnostic analysis core BrevetHub reuses (via the vendored copy)
and Team Asha keeps running (via the services/strava_analysis.py shim): stream
(de)compression round-trip, stop detection + coalescing + coord backfill, the stream
summary, the distance→time interpolator, the map payload, and the time formatter. No
DB, no Flask, no network — synthetic streams in, expected values out.
"""
from shared import strava_analysis as sa


def test_compress_decompress_round_trip():
    streams = {'time': [0, 1, 2], 'distance': [0, 10, 20],
               'latlng': [[37.5, -122.3], [37.6, -122.4]]}
    blob = sa._compress_streams(streams)
    assert isinstance(blob, (bytes, bytearray))
    assert sa._decompress_streams(blob) == streams


def _stopping_streams():
    """400 one-second samples at 10 m/s, with a 150 s stop starting at index 100."""
    time_arr = list(range(400))
    distance = [i * 10 for i in range(400)]          # 10 m/s
    velocity = [5.0] * 400
    for i in range(100, 250):                        # 150 s below threshold
        velocity[i] = 0.0
    latlng = [[37.0 + i * 0.001, -122.0 - i * 0.001] for i in range(400)]
    return {'time': time_arr, 'distance': distance,
            'velocity_smooth': velocity, 'latlng': latlng}


def test_detect_stops_finds_a_real_stop_with_coords():
    stops = sa.detect_stops(_stopping_streams())
    assert len(stops) == 1
    stop = stops[0]
    assert stop['duration_s'] == 150
    assert stop['duration_min'] == 2.5
    # distance[100] = 1000 m -> 1000 / 1609.34 = 0.6 mi (1 dp).
    assert stop['distance_miles'] == 0.6
    assert stop['lat'] is not None and stop['lng'] is not None


def test_detect_stops_ignores_short_stops():
    # A 60 s stop (< MIN_STOP_DURATION) is not recorded.
    time_arr = list(range(200))
    distance = [i * 10 for i in range(200)]
    velocity = [5.0] * 200
    for i in range(50, 110):                          # 60 s stopped
        velocity[i] = 0.0
    stops = sa.detect_stops({'time': time_arr, 'distance': distance,
                             'velocity_smooth': velocity})
    assert stops == []


def test_detect_stops_empty_without_required_streams():
    assert sa.detect_stops({}) == []
    assert sa.detect_stops({'velocity_smooth': [0, 0]}) == []


def test_coalesce_stops_merges_brief_gap():
    stops = [
        {'start_time_s': 100, 'duration_s': 60, 'duration_min': 1.0, 'distance_miles': 5.0},
        {'start_time_s': 165, 'duration_s': 30, 'duration_min': 0.5, 'distance_miles': 5.1},
    ]  # gap 165 - (100 + 60) = 5 s <= STOP_MERGE_GAP_S -> merge
    merged = sa._coalesce_stops(stops)
    assert len(merged) == 1
    assert merged[0]['duration_s'] == 90          # summed true stopped time
    assert merged[0]['duration_min'] == 1.5
    assert merged[0]['distance_miles'] == 5.0     # keeps the earliest position


def test_coalesce_stops_keeps_distinct_stops():
    stops = [
        {'start_time_s': 100, 'duration_s': 60, 'duration_min': 1.0, 'distance_miles': 5.0},
        {'start_time_s': 1000, 'duration_s': 60, 'duration_min': 1.0, 'distance_miles': 20.0},
    ]  # gap far exceeds the merge window
    assert len(sa._coalesce_stops(stops)) == 2


def test_backfill_stop_coords_fills_from_streams():
    streams = {'time': [0, 100, 200],
               'latlng': [[37.0, -122.0], [37.1, -122.1], [37.2, -122.2]]}
    stops = [{'start_time_s': 100, 'lat': None, 'lng': None}]
    filled = sa._backfill_stop_coords(stops, streams)
    assert filled[0]['lat'] == 37.1 and filled[0]['lng'] == -122.1


def test_build_stream_summary_metrics():
    summary = sa._build_stream_summary({
        'time': [0, 60, 120],
        'distance': [0, 500, 1000],
        'velocity_smooth': [5.0, 5.0, 5.0],
        'heartrate': [100, 120],
        'watts': [200, 0, 100],
    })
    assert summary['total_time_s'] == 120
    assert summary['total_distance_m'] == 1000
    assert summary['avg_moving_speed_mph'] == round(5.0 * 2.23694, 1)
    assert summary['avg_hr'] == 110.0 and summary['max_hr'] == 120
    assert summary['avg_watts'] == 150.0 and summary['max_watts'] == 200  # zero excluded


def test_stream_interpolator_linear_time_at_distance():
    interp = sa._build_stream_interpolator({
        'distance': [0, 1609.34, 3218.68],   # 0, 1, 2 miles
        'time': [0, 600, 1200],              # 0, 10, 20 minutes (in seconds)
    })
    assert interp is not None
    assert round(interp(1.0), 1) == 10.0     # exactly 1 mile
    assert round(interp(0.5), 1) == 5.0      # halfway -> linear
    assert round(interp(5.0), 1) == 20.0     # past the end -> clamps to last


def test_stream_interpolator_none_without_aligned_streams():
    assert sa._build_stream_interpolator({}) is None
    assert sa._build_stream_interpolator({'distance': [0, 1], 'time': [0]}) is None


def test_build_map_data_track_and_bounds():
    streams = {'latlng': [[37.0, -122.0], [37.1, -122.2], [37.2, -122.1]],
               'distance': [0, 1000, 2000]}
    payload = sa.build_map_data(streams, {}, [])
    assert payload is not None
    assert len(payload['track']) >= 2
    assert payload['bounds'] == [[37.0, -122.2], [37.2, -122.0]]


def test_build_map_data_none_without_latlng():
    assert sa.build_map_data({'distance': [0, 1]}, {}, []) is None
    assert sa.build_map_data({'latlng': [[37.0, -122.0]]}, {}, []) is None  # < 2 points


def test_fmt_seconds():
    assert sa._fmt_seconds(3661) == '1h 01m'
    assert sa._fmt_seconds(0) is None
    assert sa._fmt_seconds(None) is None


# NOTE: the plan-free ``build_activity_analysis`` entrypoint and its per-segment
# metric helpers (avg HR/watts/cadence, NP, grade, elevation) are covered in
# tests/test_shared_activity_analysis.py; this file focuses on the lower-level
# moved primitives above.
