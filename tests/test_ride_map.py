"""Tests for the ride-analysis map: stop coordinates + map payload assembly.

Pure-function tests (no DB, no external APIs):
  - detect_stops() now emits lat/lng at each stop's stream index, and None when
    the latlng stream is absent or malformed.
  - build_map_data() assembles a downsampled track, per-segment speed overlays,
    stop markers, and bounds — and degrades to None without usable GPS.
"""
from services.strava_analysis import detect_stops, build_map_data, METERS_PER_MILE


# ── detect_stops lat/lng ─────────────────────────────────────────────

def _stop_streams(with_latlng=True, latlng=None):
    """Streams with a single 200s stop starting at index 100."""
    n = 400
    streams = {
        'time': list(range(n)),
        'velocity_smooth': [5.0] * 100 + [0.0] * 200 + [5.0] * 100,
        'distance': [i * 5.0 for i in range(n)],
    }
    if with_latlng:
        streams['latlng'] = latlng if latlng is not None else \
            [[37.0 + i * 0.001, -122.0 - i * 0.001] for i in range(n)]
    return streams


def test_detect_stops_populates_latlng():
    stops = detect_stops(_stop_streams())
    assert len(stops) == 1
    stop = stops[0]
    # Stop began at index 100 → lat/lng from that point.
    assert stop['lat'] == round(37.0 + 100 * 0.001, 6)
    assert stop['lng'] == round(-122.0 - 100 * 0.001, 6)
    # Existing fields still present.
    assert stop['distance_miles'] == round(100 * 5.0 / METERS_PER_MILE, 1)
    assert stop['start_time_s'] == 100
    assert stop['duration_min'] == round(200 / 60, 1)


def test_detect_stops_latlng_none_when_stream_absent():
    stops = detect_stops(_stop_streams(with_latlng=False))
    assert len(stops) == 1
    assert stops[0]['lat'] is None
    assert stops[0]['lng'] is None


def test_detect_stops_latlng_none_when_malformed():
    # latlng present but entries are malformed (None / wrong shape).
    bad = [[None, None]] * 400
    stops = detect_stops(_stop_streams(latlng=bad))
    assert stops[0]['lat'] is None and stops[0]['lng'] is None


def test_detect_stops_end_of_ride_stop_gets_latlng():
    # Ride that ends while stopped — the trailing-stop branch must set lat/lng.
    n = 300
    streams = {
        'time': list(range(n)),
        'velocity_smooth': [5.0] * 100 + [0.0] * 200,
        'distance': [i * 5.0 for i in range(n)],
        'latlng': [[37.5, -122.5] for _ in range(n)],
    }
    stops = detect_stops(streams)
    assert len(stops) == 1
    assert stops[0]['lat'] == 37.5 and stops[0]['lng'] == -122.5


# ── build_map_data ───────────────────────────────────────────────────

def _map_streams(n=10):
    return {
        'latlng': [[37.0 + i * 0.001, -122.0] for i in range(n)],
        'distance': [i * 1000.0 for i in range(n)],  # meters
    }


_COMPARISON = {
    'rows': [
        {'location': 'Start', 'is_extra': False, 'distance_miles': 0.0,
         'actual_speed_mph': None},
        {'location': 'Mid', 'is_extra': False, 'distance_miles': 3.0,
         'actual_speed_mph': 14.0},
        {'location': 'Finish', 'is_extra': False, 'distance_miles': 5.5,
         'actual_speed_mph': 12.0},
        # Extra stop must be ignored for segment overlays.
        {'location': 'Unplanned', 'is_extra': True, 'distance_miles': 2.0,
         'actual_speed_mph': 9.0},
    ]
}


def test_build_map_data_basic_shape():
    stops = [{'distance_miles': 2.0, 'duration_min': 5.0, 'start_time_s': 900,
              'lat': 37.002, 'lng': -122.0, 'matched_stop_name': 'Water'}]
    data = build_map_data(_map_streams(), _COMPARISON, stops)

    assert data is not None
    assert len(data['track']) >= 2
    assert data['track'][0] == [37.0, -122.0]
    # Bounds cover the track.
    assert data['bounds'][0][0] <= 37.0 <= data['bounds'][1][0]

    # Two planned segments (Start→Mid, Mid→Finish); extra row excluded.
    assert len(data['segments']) == 2
    assert data['segments'][0]['location'] == 'Mid'
    assert data['segments'][0]['speed_mph'] == 14.0
    assert len(data['segments'][0]['points']) >= 2

    # Stop marker is informational only (coords + label + distance/duration);
    # notes are NOT attached to map stops any more.
    assert len(data['stops']) == 1
    stop = data['stops'][0]
    assert stop['lat'] == 37.002 and stop['lng'] == -122.0
    assert stop['location'] == 'Water'
    assert stop['distance_miles'] == 2.0 and stop['duration_min'] == 5.0
    assert 'commentary' not in stop and 'stop_index' not in stop


def test_build_map_data_none_without_latlng():
    assert build_map_data({'distance': [0, 100]}, _COMPARISON, []) is None
    assert build_map_data({}, _COMPARISON, []) is None
    assert build_map_data(None, _COMPARISON, []) is None


def test_build_map_data_none_when_track_too_short():
    assert build_map_data({'latlng': [[37.0, -122.0]]}, _COMPARISON, []) is None


def test_build_map_data_skips_stops_without_coords():
    stops = [
        {'distance_miles': 2.0, 'duration_min': 5.0, 'lat': None, 'lng': None},
        {'distance_miles': 4.0, 'duration_min': 3.0, 'lat': 37.004, 'lng': -122.0},
    ]
    data = build_map_data(_map_streams(), _COMPARISON, stops)
    # Only the coordinate-bearing stop survives.
    assert len(data['stops']) == 1
    assert data['stops'][0]['lat'] == 37.004


def test_build_map_data_downsamples_track():
    n = 5000
    streams = {
        'latlng': [[37.0 + i * 0.0001, -122.0] for i in range(n)],
        'distance': [i * 10.0 for i in range(n)],
    }
    data = build_map_data(streams, {'rows': []}, [], max_points=500)
    assert data is not None
    # Downsampled to the cap (+ possible final point).
    assert len(data['track']) <= 501
    # No segments when there are no planned rows, but the track still renders.
    assert data['segments'] == []


# ── per-segment SVG thumbnails ───────────────────────────────────────

def _points_within(points_str, w, h):
    """Every 'x,y' pair in an SVG points string lies inside the viewbox."""
    for pair in points_str.split():
        x, y = (float(v) for v in pair.split(','))
        if not (0 <= x <= w and 0 <= y <= h):
            return False
    return True


def test_thumbnails_present_per_segment():
    data = build_map_data(_map_streams(), _COMPARISON, [])
    thumb = data['thumb']
    assert thumb is not None
    assert thumb['viewbox'] == '0 0 100 60'
    assert thumb['track']  # non-empty full-track polyline
    # One thumbnail per PLANNED segment (Mid, Finish); Start has no leg, extras excluded.
    assert set(thumb['segments'].keys()) == {'Mid', 'Finish'}
    for pts in thumb['segments'].values():
        assert len(pts.split()) >= 2


def test_thumbnail_points_within_viewbox():
    data = build_map_data(_map_streams(), _COMPARISON, [])
    thumb = data['thumb']
    assert _points_within(thumb['track'], 100, 60)
    for pts in thumb['segments'].values():
        assert _points_within(pts, 100, 60)


def test_thumbnails_include_segment_pins():
    # Each planned segment gets a pin at its arrival (end) point, in the SAME
    # projection as the polyline — so the pin lands on the segment's last point.
    data = build_map_data(_map_streams(), _COMPARISON, [])
    thumb = data['thumb']
    assert set(thumb['pins'].keys()) == {'Mid', 'Finish'}
    for loc, pin in thumb['pins'].items():
        assert _points_within(pin, 100, 60)
        # Pin == the last "x,y" of that segment's polyline (its endpoint).
        assert pin == thumb['segments'][loc].split()[-1]


def test_thumbnails_include_stop_pins():
    # Unplanned stops get a pin keyed by distance (miles, 1 decimal) matching the
    # template's stop-note key.
    stops = [{'distance_miles': 2.0, 'duration_min': 5.0,
              'lat': 37.004, 'lng': -122.0, 'matched_stop_name': None}]
    data = build_map_data(_map_streams(), _COMPARISON, stops)
    thumb = data['thumb']
    assert set(thumb['stop_pins'].keys()) == {'2.0'}
    assert _points_within(thumb['stop_pins']['2.0'], 100, 60)


def test_stop_pins_empty_without_stops():
    data = build_map_data(_map_streams(), _COMPARISON, [])
    assert data['thumb']['stop_pins'] == {}
    # A stop lacking coords contributes no pin.
    stops = [{'distance_miles': 4.0, 'duration_min': 3.0, 'lat': None, 'lng': None}]
    data2 = build_map_data(_map_streams(), _COMPARISON, stops)
    assert data2['thumb']['stop_pins'] == {}


def test_thumbnails_stops_without_segments():
    # A track + stops but no planned segments: no segment pins, but stops still pin.
    from services.strava_analysis import _segment_thumbnails
    track = [[37.0 + i * 0.001, -122.0] for i in range(10)]
    stops = [{'distance_miles': 1.5, 'lat': 37.003, 'lng': -122.0}]
    thumb = _segment_thumbnails(track, [], stops)
    assert thumb['segments'] == {} and thumb['pins'] == {}
    assert set(thumb['stop_pins'].keys()) == {'1.5'}


def test_thumbnails_none_without_track():
    from services.strava_analysis import _segment_thumbnails
    assert _segment_thumbnails([], []) is None
    assert _segment_thumbnails([[37.0, -122.0]], []) is None  # <2 points


# ── stop coalescing + coord backfill ─────────────────────────────────

def test_coalesce_merges_split_stops():
    from services.strava_analysis import _coalesce_stops
    # One physical ~25-min stop split into 3 by brief velocity blips.
    stops = [
        {'distance_miles': 134.7, 'start_time_s': 48829, 'duration_s': 906, 'duration_min': 15.1},
        {'distance_miles': 134.7, 'start_time_s': 49739, 'duration_s': 162, 'duration_min': 2.7},
        {'distance_miles': 134.7, 'start_time_s': 49922, 'duration_s': 420, 'duration_min': 7.0},
    ]
    merged = _coalesce_stops(stops)
    assert len(merged) == 1
    m = merged[0]
    assert m['distance_miles'] == 134.7
    assert m['start_time_s'] == 48829
    # Duration is the SUM of true stopped time (moving blips excluded):
    # 906 + 162 + 420 = 1488s.
    assert m['duration_s'] == 1488
    assert m['duration_min'] == round(1488 / 60, 1)


def test_coalesce_keeps_distinct_stops():
    from services.strava_analysis import _coalesce_stops
    # Two stops separated by ~10 min of moving → NOT merged.
    stops = [
        {'distance_miles': 40.0, 'start_time_s': 3600, 'duration_s': 300},
        {'distance_miles': 45.0, 'start_time_s': 4500, 'duration_s': 300},  # gap 600s > 120
    ]
    assert len(_coalesce_stops(stops)) == 2


def test_coalesce_does_not_collapse_two_distinct_controls():
    from services.strava_analysis import _coalesce_stops
    # Two DIFFERENT matched controls close in time must stay separate.
    stops = [
        {'distance_miles': 90.0, 'start_time_s': 10000, 'duration_s': 300,
         'matched_stop_name': 'Control #3', 'is_extra': False},
        {'distance_miles': 90.1, 'start_time_s': 10350, 'duration_s': 300,
         'matched_stop_name': 'Control #4', 'is_extra': False},  # gap 50s but distinct
    ]
    out = _coalesce_stops(stops)
    assert len(out) == 2
    assert [o['matched_stop_name'] for o in out] == ['Control #3', 'Control #4']


def test_coalesce_is_idempotent_and_handles_edges():
    from services.strava_analysis import _coalesce_stops
    assert _coalesce_stops([]) == []
    one = [{'distance_miles': 5.0, 'start_time_s': 100, 'duration_s': 200}]
    assert _coalesce_stops(one) == one
    stops = [
        {'distance_miles': 134.7, 'start_time_s': 48829, 'duration_s': 906},
        {'distance_miles': 134.7, 'start_time_s': 49739, 'duration_s': 162},
        {'distance_miles': 134.7, 'start_time_s': 49922, 'duration_s': 420},
    ]
    once = _coalesce_stops(stops)
    twice = _coalesce_stops(once)
    assert twice == once  # re-running on merged output is a no-op


def test_coalesce_preserves_matched_identity():
    from services.strava_analysis import _coalesce_stops
    # An extra blip right after a matched control keeps the control identity.
    stops = [
        {'distance_miles': 90.0, 'start_time_s': 10000, 'duration_s': 600,
         'matched_stop_name': 'Control #3', 'is_extra': False},
        {'distance_miles': 90.0, 'start_time_s': 10650, 'duration_s': 120,
         'matched_stop_name': None, 'is_extra': True},  # gap 50s → merged
    ]
    merged = _coalesce_stops(stops)
    assert len(merged) == 1
    assert merged[0]['matched_stop_name'] == 'Control #3'
    assert merged[0]['is_extra'] is False


def test_backfill_stop_coords_fills_missing():
    from services.strava_analysis import _backfill_stop_coords
    streams = {
        'time': [0, 10, 20, 30, 40],
        'latlng': [[37.0, -122.0], [37.1, -122.1], [37.2, -122.2],
                   [37.3, -122.3], [37.4, -122.4]],
    }
    stops = [
        {'start_time_s': 19, 'lat': None, 'lng': None},   # nearest idx 2 → 37.2
        {'start_time_s': 40, 'lat': 1.0, 'lng': 2.0},     # already has coords → untouched
    ]
    out = _backfill_stop_coords(stops, streams)
    assert out[0]['lat'] == 37.2 and out[0]['lng'] == -122.2
    assert out[1]['lat'] == 1.0 and out[1]['lng'] == 2.0  # preserved


def test_backfill_noop_without_latlng_stream():
    from services.strava_analysis import _backfill_stop_coords
    stops = [{'start_time_s': 10, 'lat': None, 'lng': None}]
    out = _backfill_stop_coords(stops, {'time': [0, 10]})  # no latlng
    assert out[0]['lat'] is None


def test_detect_stops_coalesces_blip_split_stop():
    from services.strava_analysis import detect_stops
    n = 400
    streams = {
        'time': list(range(n)),
        # one long stop (idx 50..351) broken by a 2-sample moving blip at 200-201
        'velocity_smooth': [5.0] * 50 + [0.0] * 150 + [5.0] * 2 + [0.0] * 150 + [5.0] * 48,
        'distance': [i * 5.0 for i in range(n)],
        'latlng': [[37.0 + i * 0.0001, -122.0] for i in range(n)],
    }
    stops = detect_stops(streams)
    assert len(stops) == 1  # the two halves merge into one physical stop
