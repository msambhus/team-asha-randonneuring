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
              'lat': 37.002, 'lng': -122.0, 'commentary': 'water stop'}]
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

    # Stop marker carries index, stable identity, and saved commentary.
    assert len(data['stops']) == 1
    assert data['stops'][0]['stop_index'] == 0
    assert data['stops'][0]['start_time_s'] == 900
    assert data['stops'][0]['commentary'] == 'water stop'
    assert data['stops'][0]['lat'] == 37.002


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
    # Only the coordinate-bearing stop survives; index reflects original position.
    assert len(data['stops']) == 1
    assert data['stops'][0]['stop_index'] == 1


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
