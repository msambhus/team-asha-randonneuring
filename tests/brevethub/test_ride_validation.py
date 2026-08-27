from datetime import date, datetime, timedelta, timezone

from brevethub.services.ride_validation import (
    TrackPoint, combine_recordings, parse_gpx, validate_submission,
)


def _point(minute, lat=37.0, lng=-122.0, elevation=10):
    return TrackPoint(datetime(2026, 8, 8, 13, tzinfo=timezone.utc) + timedelta(minutes=minute),
                      lat, lng, elevation)


def _event(**overrides):
    event = {'date': date(2026, 8, 8), 'distance_km': 2, 'time_limit_hours': 2}
    event.update(overrides)
    return event


def test_gpx_requires_timestamped_points_and_retains_creator():
    data = b'''<?xml version="1.0"?><gpx creator="Garmin Edge"><trk><trkseg>
      <trkpt lat="37.0" lon="-122.0"><ele>12</ele><time>2026-08-08T13:00:00Z</time></trkpt>
      <trkpt lat="37.01" lon="-122.0"><ele>22</ele><time>2026-08-08T13:05:00Z</time></trkpt>
    </trkseg></trk></gpx>'''
    points, metadata = parse_gpx(data)
    assert len(points) == 2
    assert points[-1].distance_m > 1000
    assert metadata == {'format': 'gpx', 'creator': 'Garmin Edge'}


def test_split_recordings_are_chronological_and_keep_real_gap():
    combined = combine_recordings([[_point(20), _point(30, 37.02)],
                                   [_point(0), _point(10, 37.01)]])
    assert [p.timestamp.minute for p in combined] == [0, 10, 20, 30]
    assert combined[-1].distance_m > combined[1].distance_m


def test_clear_track_is_ready_for_organizer_approval():
    points = [_point(0, elevation=10), _point(5, 37.01, elevation=20),
              _point(10, 37.02, elevation=10)]
    route = [
        {'lat': 37.0, 'lng': -122.0, 'dist_m': 0, 'e_m': 10},
        {'lat': 37.01, 'lng': -122.0, 'dist_m': 1112, 'e_m': 20},
        {'lat': 37.02, 'lng': -122.0, 'dist_m': 2224, 'e_m': 10},
    ]
    decision, checks = validate_submission(
        points=points, route=route, controls=[], event=_event(),
        official_start=points[0].timestamp,
        source_metadata={'format': 'gpx', 'creator': 'Garmin Edge'},
    )
    assert decision == 'clear'
    assert {check.result for check in checks} == {'clear'}


def test_route_matching_uses_segments_not_only_route_vertices():
    # The middle activity point is more than 500 m from either endpoint but
    # lies exactly on the official segment between them.
    points = [_point(0, 37.0, -122.0), _point(5, 37.0, -121.99),
              _point(10, 37.0, -121.98)]
    route = [
        {'lat': 37.0, 'lng': -122.0, 'dist_m': 0, 'e_m': 10},
        {'lat': 37.0, 'lng': -121.98, 'dist_m': 1780, 'e_m': 20},
    ]
    _, checks = validate_submission(
        points=points, route=route, controls=[], event=_event(distance_km=1.7),
        official_start=points[0].timestamp,
        source_metadata={'format': 'gpx', 'creator': 'device'},
    )
    by_code = {check.code: check for check in checks}
    assert by_code['route_coverage'].result == 'clear'
    assert by_code['route_departures'].result == 'clear'


def test_anomaly_never_becomes_automatic_disqualification():
    points = [_point(0), _point(1, 38.0), _point(2, 37.02)]
    route = [
        {'lat': 37.0, 'lng': -122.0, 'dist_m': 0, 'e_m': 10},
        {'lat': 37.01, 'lng': -122.0, 'dist_m': 1112, 'e_m': 20},
        {'lat': 37.02, 'lng': -122.0, 'dist_m': 2224, 'e_m': 10},
    ]
    decision, checks = validate_submission(
        points=points, route=route, controls=[], event=_event(),
        official_start=points[0].timestamp,
        source_metadata={'format': 'gpx', 'creator': 'device'},
    )
    assert decision == 'needs_review'
    assert 'disqualified' not in {check.result for check in checks}
    assert any(check.map_segments for check in checks if check.result == 'needs_review')


def test_traditional_proof_without_gps_goes_to_human_review():
    decision, checks = validate_submission(
        points=[], route=[], controls=[], event=_event(), official_start=None,
        has_traditional_evidence=True,
    )
    assert decision == 'needs_review'
    assert all(check.result == 'needs_review' for check in checks)


def test_missing_all_evidence_is_incomplete():
    decision, checks = validate_submission(
        points=[], route=[], controls=[], event=_event(), official_start=None,
    )
    assert decision == 'incomplete'
    assert any(check.result == 'incomplete' for check in checks)


def test_route_departures_include_activity_mile_ranges():
    points = [_point(0, 37.0), _point(1, 37.001), _point(2, 37.02),
              _point(3, 37.021), _point(4, 37.022), _point(5, 37.001),
              _point(6, 37.0)]
    route = [
        {'lat': 37.0, 'lng': -122.0, 'dist_m': 0, 'e_m': 10},
        {'lat': 37.001, 'lng': -122.0, 'dist_m': 1000, 'e_m': 20},
        {'lat': 37.0, 'lng': -122.0, 'dist_m': 2000, 'e_m': 10},
    ]
    _, checks = validate_submission(
        points=points, route=route, controls=[], event=_event(distance_km=1),
        official_start=points[0].timestamp,
    )

    departures = next(check for check in checks if check.code == 'route_departures')
    assert departures.metrics['departures']
    assert {'start_mile', 'end_mile'} <= departures.metrics['departures'][0].keys()


def test_duplicate_and_ebike_are_review_flags_not_rejections():
    points = [_point(0), _point(5, 37.01), _point(10, 37.02)]
    route = [
        {'lat': 37.0, 'lng': -122.0, 'dist_m': 0, 'e_m': 10},
        {'lat': 37.01, 'lng': -122.0, 'dist_m': 1112, 'e_m': 20},
        {'lat': 37.02, 'lng': -122.0, 'dist_m': 2224, 'e_m': 10},
    ]
    decision, checks = validate_submission(
        points=points, route=route, controls=[], event=_event(),
        official_start=points[0].timestamp,
        source_metadata={'format': 'fit', 'sport': 'e-bike'},
        duplicate_conflicts=[{'submission_id': 7}],
    )
    by_code = {check.code: check for check in checks}
    assert decision == 'needs_review'
    assert by_code['human_powered'].result == 'needs_review'
    assert by_code['duplicate_evidence'].result == 'needs_review'


def test_long_stationary_control_stop_is_not_a_track_gap():
    start = _point(0)
    stopped = TrackPoint(start.timestamp + timedelta(hours=4), start.lat, start.lng, 10)
    finish = TrackPoint(stopped.timestamp + timedelta(minutes=10), 37.02, -122.0, 10)
    route = [
        {'lat': 37.0, 'lng': -122.0, 'dist_m': 0, 'e_m': 10},
        {'lat': 37.02, 'lng': -122.0, 'dist_m': 2224, 'e_m': 10},
    ]
    _, checks = validate_submission(
        points=[start, stopped, finish], route=route, controls=[],
        event=_event(time_limit_hours=8), official_start=start.timestamp,
        source_metadata={'format': 'gpx', 'creator': 'device'},
    )
    continuity = next(check for check in checks if check.code == 'track_continuity')
    assert continuity.metrics['long_gaps'] == 0
