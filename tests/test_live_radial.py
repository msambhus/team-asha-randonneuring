"""Pure unit tests for the shared Radial live-view builders (shared/live_radial.py).

No DB, no network, no Flask — every input is passed in. Covers the privacy-shaped
progress-sorted roster and the server-computed altitude profile geometry.
"""
from datetime import datetime, timedelta, timezone

import pytest

from shared import live_radial as lr


# --------------------------------------------------------------------------- #
# A synthetic straight route (~40 mi, +10 m/mile climb) + rider fixtures.
# --------------------------------------------------------------------------- #
def _track_and_ctx():
    track, cum, c, prev = [], [], 0.0, None
    for i in range(0, 41):
        e_m = 100.0 + i * 10
        e_ft = e_m * 3.28084
        if prev is not None and e_ft > prev:
            c += e_ft - prev
        prev = e_ft
        track.append({'lat': 37.0, 'lng': -122.0 + i * 0.01,
                      'dist_m': i * lr.tlm.METERS_TO_MILES ** -1, 'e_m': e_m})
    # dist_m computed cleanly as i miles in meters:
    for i, tp in enumerate(track):
        tp['dist_m'] = i * 1609.344
        cum.append(round(sum(max(0.0, (100.0 + k * 10 - (100.0 + (k - 1) * 10)))
                             for k in range(1, i + 1)) * 3.28084))
    ctx = {
        'has_route': True, 'track': track, 'cum_ascent_ft': cum,
        'total_dist_m': track[-1]['dist_m'], 'total_ascent_ft': cum[-1],
        'plan_stops': [
            {'distance_miles': 0.0, 'cum_time_min': 0.0, 'location': 'Start', 'stop_type': 'start'},
            {'distance_miles': 20.0, 'cum_time_min': 120.0, 'arrival_time_min': 120.0,
             'location': 'Control A', 'stop_type': 'control'},
            {'distance_miles': 40.0, 'cum_time_min': 240.0, 'arrival_time_min': 240.0,
             'location': 'Finish', 'stop_type': 'finish'}],
        'plan_total_mi': 40.0, 'plan_cutoff_hours': 4.0,
        'ride_start_iso': None, 'time_limit_min': 240,
    }
    return track, ctx


_NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _history(track, rider_miles, elapsed_min=100):
    start = _NOW - timedelta(minutes=elapsed_min)
    pts = []
    for k in range(7):
        frac = k / 6
        idx = min(int(rider_miles * frac), 40)
        p = track[idx]
        pts.append({'lat': p['lat'], 'lng': p['lng'], 'speed': 5.0,
                    'recorded_at': start + timedelta(minutes=elapsed_min * frac)})
    return pts


# --------------------------------------------------------------------------- #
# Roster builder.
# --------------------------------------------------------------------------- #
def test_roster_is_sorted_leader_first():
    track, ctx = _track_and_ctx()
    rows = [
        {'rider_id': 1, 'display_name': 'Alice Zhang', 'lat': track[10]['lat'],
         'lng': track[10]['lng'], 'source': 'beacon', 'recorded_at': _NOW},
        {'rider_id': 2, 'display_name': 'Bob Lee', 'lat': track[30]['lat'],
         'lng': track[30]['lng'], 'source': 'garmin', 'recorded_at': _NOW},
    ]
    hb = {1: _history(track, 10), 2: _history(track, 30)}
    roster = lr.build_radial_roster(rows, ctx, _NOW, hb, ride_id=99,
                                    min_history=2, stateless_fallback=False)
    assert [r['display_name'] for r in roster] == ['Bob Lee', 'Alice Zhang']
    assert roster[0]['route_position_mi'] == 30.0
    assert roster[1]['route_position_mi'] == 10.0


def test_roster_row_has_no_pii_identifiers():
    track, ctx = _track_and_ctx()
    rows = [{'rider_id': 42, 'display_name': 'Dana Fox', 'email': 'dana@x.com',
             'google_id': 'g-123', 'lat': track[5]['lat'], 'lng': track[5]['lng'],
             'source': 'beacon', 'recorded_at': _NOW}]
    roster = lr.build_radial_roster(rows, ctx, _NOW, {42: _history(track, 5)},
                                    ride_id=7, min_history=2, stateless_fallback=False)
    row = roster[0]
    for leaked in ('rider_id', 'email', 'google_id'):
        assert leaked not in row
    assert row['display_name'] == 'Dana Fox'
    assert row['initials'] == 'DF'
    assert row['key'] and len(row['key']) == 12


def test_roster_key_is_stable_and_opaque():
    k1 = lr.roster_key(5, 9)
    k2 = lr.roster_key(5, 9)
    assert k1 == k2 and len(k1) == 12
    assert lr.roster_key(6, 9) != k1           # different rider → different key
    assert str(5) not in k1                     # not just the id


def test_roster_computes_route_position_and_stats():
    track, ctx = _track_and_ctx()
    rows = [{'rider_id': 1, 'display_name': 'Bob Lee', 'lat': track[20]['lat'],
             'lng': track[20]['lng'], 'source': 'garmin', 'recorded_at': _NOW}]
    roster = lr.build_radial_roster(rows, ctx, _NOW, {1: _history(track, 20)},
                                    ride_id=1, min_history=2, stateless_fallback=False)
    row = roster[0]
    assert row['on_route'] is True
    assert row['route_position_mi'] == 20.0
    assert row['dist_mi'] == 20.0
    assert row['next_control'] is not None
    assert row['ascent_done_ft'] is not None
    assert row['marker_color'] in (lr.MARKER_AHEAD_COLOR, lr.MARKER_BEHIND_COLOR,
                                   lr.MARKER_UNKNOWN_COLOR)


def test_roster_is_fail_soft_per_rider():
    track, ctx = _track_and_ctx()
    # A malformed row (no lat/lng) must degrade to a base row, not raise.
    rows = [{'rider_id': 1, 'display_name': 'Broken', 'lat': None, 'lng': None,
             'source': 'beacon', 'recorded_at': _NOW}]
    roster = lr.build_radial_roster(rows, ctx, _NOW, {1: [{'lat': 'x', 'lng': 'y',
                                    'recorded_at': _NOW}]}, ride_id=1)
    assert len(roster) == 1
    assert roster[0]['display_name'] == 'Broken'
    assert 'rider_id' not in roster[0]


def test_roster_display_name_falls_back_without_pii():
    track, ctx = _track_and_ctx()
    rows = [{'rider_id': 1, 'lat': 37.0, 'lng': -122.0, 'source': 'beacon',
             'recorded_at': _NOW}]  # no display_name / name
    roster = lr.build_radial_roster(rows, ctx, _NOW, {}, ride_id=1)
    assert roster[0]['display_name'] == 'Rider'  # never an email local-part


def test_roster_distance_unit_is_flippable():
    track, ctx = _track_and_ctx()
    rows = [{'rider_id': 1, 'display_name': 'Bob Lee', 'lat': track[20]['lat'],
             'lng': track[20]['lng'], 'source': 'garmin', 'recorded_at': _NOW}]
    hb = {1: _history(track, 20)}
    mi = lr.build_radial_roster(rows, ctx, _NOW, hb, ride_id=1, min_history=2,
                                stateless_fallback=False, dist_unit='mi')[0]
    km = lr.build_radial_roster(rows, ctx, _NOW, hb, ride_id=1, min_history=2,
                                stateless_fallback=False, dist_unit='km')[0]
    assert mi['dist_display'] == 20.0 and mi['dist_unit'] == 'mi'
    assert km['dist_unit'] == 'km'
    assert km['dist_display'] == pytest.approx(20.0 * 1.609344, abs=0.1)


# --------------------------------------------------------------------------- #
# Altitude profile.
# --------------------------------------------------------------------------- #
def _profile_track():
    return [
        {'lat': 0, 'lng': 0, 'dist_m': 0.0, 'e_m': 100.0},
        {'lat': 0, 'lng': 0, 'dist_m': 1609.344, 'e_m': 130.0},   # +30m/mi gentle climb
        {'lat': 0, 'lng': 0, 'dist_m': 3218.688, 'e_m': 250.0},   # steep climb
        {'lat': 0, 'lng': 0, 'dist_m': 4828.032, 'e_m': 120.0},   # descent
    ]


def test_profile_uses_altitude_not_cumulative_climb():
    prof = lr.build_elevation_profile(_profile_track(), width=800, height=200)
    assert prof['available'] is True
    # y for the highest point (250 m) is the SMALLEST y (nearer the top).
    assert prof['min_ft'] == round(100.0 * lr.tlm.METERS_TO_FEET)
    assert prof['max_ft'] == round(250.0 * lr.tlm.METERS_TO_FEET)
    assert prof['total_mi'] == pytest.approx(4828.032 / 1609.344, abs=0.01)


def test_profile_gradient_buckets_color_by_grade():
    prof = lr.build_elevation_profile(_profile_track(), width=800, height=200)
    grades = [s['grade'] for s in prof['segments']]
    colors = [s['color'] for s in prof['segments']]
    assert len(prof['segments']) == 3
    assert grades[2] < 0                              # last leg descends
    assert colors[0] == '#22c55e'                     # ~1.9% → green (0–3%)
    assert colors[1] == '#f97316'                     # ~7.5% → orange (6–9%)
    assert colors[2] == '#3b82f6'                     # descent → blue


def test_profile_place_x_is_linear_along_route():
    prof = lr.build_elevation_profile(_profile_track(), width=800, height=200)
    assert lr.place_x(0, prof) == prof['plot']['x']
    end = prof['plot']['x'] + prof['plot']['w']
    assert lr.place_x(prof['total_mi'], prof) == pytest.approx(end, abs=0.1)
    mid = lr.place_x(prof['total_mi'] / 2, prof)
    assert prof['plot']['x'] < mid < end
    assert lr.place_x(None, prof) is None


def test_profile_unavailable_without_elevation():
    assert lr.build_elevation_profile([]) == {'available': False}
    assert lr.build_elevation_profile(
        [{'dist_m': 0, 'e_m': None}, {'dist_m': 10, 'e_m': None}]
    ) == {'available': False}


def test_profile_has_ticks_and_legend():
    prof = lr.build_elevation_profile(_profile_track(), width=800, height=200)
    assert prof['x_ticks'] and all('label' in t and 'x' in t for t in prof['x_ticks'])
    assert prof['y_ticks'] and all('label' in t and 'y' in t for t in prof['y_ticks'])
    assert prof['legend'] and prof['legend'][0]['label'] == 'descent'
    assert prof['area_path'].startswith('M') and prof['area_path'].endswith('Z')
