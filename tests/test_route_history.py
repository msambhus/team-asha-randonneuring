"""Unit tests for services/route_history.py.

Pure unit tests: models.* and (mostly) build_comparison are mocked so no DB or
Strava access is needed.  They run green normally AND under a bogus DATABASE_URL
(e.g. postgresql://x:x@127.0.0.1:1/nodb) because nothing here touches the DB.

One test (test_end_to_end_real_decode) does NOT mock build_comparison and feeds
a tiny real zlib-compressed stream to prove the decode path works end-to-end.
"""
import json
import zlib
from unittest.mock import patch

import pytest

from services import route_history


# A valid tiny zlib-compressed stream so _segment_actuals_for_ride gets past
# the decompress + streams.get('distance') guard when build_comparison is mocked.
_VALID_STREAM = zlib.compress(json.dumps({'distance': [0, 100], 'time': [0, 10]}).encode())


# ── helpers ──────────────────────────────────────────────────────────────
def _row(ride_id, ride_name='SFR 200K Marshall', date='2025-01-01',
         ride_plan_id=None, match_id=None, streams=_VALID_STREAM, **extra):
    row = {
        'ride_id': ride_id,
        'ride_name': ride_name,
        'date': date,
        'ride_plan_id': ride_plan_id,
        'match_id': match_id if match_id is not None else ride_id,
        'elapsed_time': 36000,
        'moving_time': 30000,
        'average_speed': 6.7,
        'average_heartrate': 140,
        'average_watts': 180,
        'weighted_average_watts': 190,
        'total_elevation_gain': 1500,
        'strava_distance_m': 200000,
        'has_heartrate': True,
        'device_watts': True,
        'activity_streams': streams,
    }
    row.update(extra)
    return row


def _comparison(rows):
    return {'rows': rows, 'summary': {}, 'hr_power': {}}


def _seg(location, segment_min, speed=None, watts=None, cadence=None, is_extra=False):
    return {
        'location': location,
        'is_extra': is_extra,
        'actual_segment_min': segment_min,
        'actual_speed_mph': speed,
        'actual_avg_watts': watts,
        'actual_avg_cadence': cadence,
    }


# ── falsy / no-data cases ────────────────────────────────────────────────
def test_falsy_base_plan_returns_empty():
    assert route_history.compute_same_route_segment_baseline(1, None) == {}
    assert route_history.compute_same_route_segment_baseline(1, 0) == {}


def test_no_prior_rides_returns_empty():
    with patch.object(route_history.models, 'get_rider_rides_with_cached_streams',
                      return_value=[]), \
         patch.object(route_history.models, 'get_all_ride_plans', return_value=[]):
        assert route_history.compute_same_route_segment_baseline(1, 42) == {}


def test_no_matching_route_returns_empty():
    # Ride exists but neither FK nor name matches base plan 42.
    rows = [_row(1, ride_name='Totally Different Ride', ride_plan_id=99)]
    with patch.object(route_history.models, 'get_rider_rides_with_cached_streams',
                      return_value=rows), \
         patch.object(route_history.models, 'get_all_ride_plans',
                      return_value=[{'id': 99, 'name': 'Totally Different Ride'}]):
        assert route_history.compute_same_route_segment_baseline(1, 42) == {}


def test_filters_metadata_before_loading_selected_stream_blobs():
    metadata = [
        _row(1, ride_plan_id=42, streams=None),
        _row(2, ride_plan_id=99, streams=None),
    ]
    selected = [_row(1, ride_plan_id=42)]
    with patch.object(
            route_history.models, 'get_rider_rides_metadata_for_comparison',
            return_value=metadata), \
         patch.object(route_history.models, 'get_all_ride_plans',
                      return_value=[]), \
         patch.object(
             route_history.models, 'get_rider_rides_with_cached_streams_by_ids',
             return_value=selected) as get_selected, \
         patch.object(route_history.models, 'get_ride_plan_stops',
                      return_value=[{'location': 'Start'}]), \
         patch.object(route_history.models, 'get_strava_ride_analysis',
                      return_value={'detected_stops': []}), \
         patch.object(route_history.strava_analysis, 'build_comparison',
                      return_value=_comparison([_seg('Start', 60)])):
        result = route_history.compute_same_route_segment_baseline(7, 42)

    get_selected.assert_called_once_with(7, [1])
    assert result['Start']['n_rides'] == 1


# ── FK vs name-match filtering ───────────────────────────────────────────
def test_fk_match_kept():
    rows = [_row(1, ride_plan_id=42)]
    with patch.object(route_history.models, 'get_rider_rides_with_cached_streams',
                      return_value=rows), \
         patch.object(route_history.models, 'get_all_ride_plans', return_value=[]), \
         patch.object(route_history.models, 'get_ride_plan_stops',
                      return_value=[{'location': 'Start'}]), \
         patch.object(route_history.models, 'get_strava_ride_analysis',
                      return_value={'detected_stops': []}), \
         patch.object(route_history.strava_analysis, 'build_comparison',
                      return_value=_comparison([_seg('Start', 60, speed=12.0)])):
        result = route_history.compute_same_route_segment_baseline(1, 42)
    assert 'Start' in result
    assert result['Start']['n_rides'] == 1


def test_name_match_kept_when_fk_absent():
    # No FK, but the ride name matches plan 42's route name.
    rows = [_row(1, ride_name='SFR 200K Marshall', ride_plan_id=None)]
    plans = [{'id': 42, 'name': 'Marshall Wall 200K'},
             {'id': 7, 'name': 'Davis Double'}]
    with patch.object(route_history.models, 'get_rider_rides_with_cached_streams',
                      return_value=rows), \
         patch.object(route_history.models, 'get_all_ride_plans', return_value=plans), \
         patch.object(route_history.models, 'get_ride_plan_stops',
                      return_value=[{'location': 'Start'}]), \
         patch.object(route_history.models, 'get_strava_ride_analysis',
                      return_value={'detected_stops': []}), \
         patch.object(route_history.strava_analysis, 'build_comparison',
                      return_value=_comparison([_seg('Start', 50, speed=11.0)])):
        result = route_history.compute_same_route_segment_baseline(1, 42)
    assert result['Start']['n_rides'] == 1


def test_name_match_to_wrong_plan_excluded():
    # Name matches plan 7, not the requested base plan 42 → excluded.
    rows = [_row(1, ride_name='Davis Double Century', ride_plan_id=None)]
    plans = [{'id': 42, 'name': 'Marshall Wall 200K'},
             {'id': 7, 'name': 'Davis Double Century'}]
    with patch.object(route_history.models, 'get_rider_rides_with_cached_streams',
                      return_value=rows), \
         patch.object(route_history.models, 'get_all_ride_plans', return_value=plans), \
         patch.object(route_history.models, 'get_ride_plan_stops',
                      return_value=[{'location': 'Start'}]), \
         patch.object(route_history.models, 'get_strava_ride_analysis',
                      return_value={'detected_stops': []}), \
         patch.object(route_history.strava_analysis, 'build_comparison',
                      return_value=_comparison([_seg('Start', 50)])):
        assert route_history.compute_same_route_segment_baseline(1, 42) == {}


def test_fk_match_kept_despite_name_mismatch():
    """Regression (#4): a prior ride FK-linked to the base plan is counted even
    when its NAME wouldn't fuzzy-match — the real-world case (rider 6 / plan 6)
    that returned an empty baseline until get_rider_rides_with_cached_streams
    started selecting ride_plan_id, restoring the FK-first path."""
    rows = [_row(1, ride_name='Tuesday Shop Ride', ride_plan_id=42)]
    plans = [{'id': 42, 'name': 'Marshall Wall 200K'}]  # name does NOT match
    with patch.object(route_history.models, 'get_rider_rides_with_cached_streams',
                      return_value=rows), \
         patch.object(route_history.models, 'get_all_ride_plans', return_value=plans), \
         patch.object(route_history.models, 'get_ride_plan_stops',
                      return_value=[{'location': 'Start'}]), \
         patch.object(route_history.models, 'get_strava_ride_analysis',
                      return_value={'detected_stops': []}), \
         patch.object(route_history.strava_analysis, 'build_comparison',
                      return_value=_comparison([_seg('Start', 72, speed=13.0)])):
        result = route_history.compute_same_route_segment_baseline(1, 42)
    assert result and result['Start']['n_rides'] == 1  # FK path saved it


def test_cached_streams_query_selects_ride_plan_id():
    """Regression (#4): the candidate query MUST select ride_plan_id, or the
    FK-first match in _resolve_base_plan_id is dead and same-route history breaks."""
    import inspect
    from models import (get_rider_rides_with_cached_streams,
                        get_rider_rides_with_cached_streams_by_ids)
    assert 'ride_plan_id' in inspect.getsource(get_rider_rides_with_cached_streams)
    assert 'ride_plan_id' in inspect.getsource(get_rider_rides_with_cached_streams_by_ids)


# ── averaging across rides ───────────────────────────────────────────────
def test_averaging_across_rides():
    rows = [_row(1, ride_plan_id=42, match_id=101),
            _row(2, ride_plan_id=42, match_id=102)]

    def _bc(**kwargs):
        # Distinguish rides by the detected_stops we return per match.
        return _bc.map[_bc.calls.pop(0)]
    _bc.calls = []

    comps = {
        101: _comparison([_seg('Start', 60, speed=12.0, watts=200, cadence=80),
                          _seg('Control A', 40, speed=10.0, watts=180, cadence=70)]),
        102: _comparison([_seg('Start', 80, speed=10.0, watts=160, cadence=90),
                          _seg('Control A', 60, speed=8.0, watts=140, cadence=60)]),
    }

    def build_comparison(plan_stops, detected_stops, activity, streams=None, **kw):
        # detected_stops carries the match id we stashed in the analysis mock.
        mid = detected_stops[0]['mid'] if detected_stops else None
        return comps[mid]

    def analysis(match_id):
        return {'detected_stops': [{'mid': match_id}]}

    with patch.object(route_history.models, 'get_rider_rides_with_cached_streams',
                      return_value=rows), \
         patch.object(route_history.models, 'get_all_ride_plans', return_value=[]), \
         patch.object(route_history.models, 'get_ride_plan_stops',
                      return_value=[{'location': 'Start'}, {'location': 'Control A'}]), \
         patch.object(route_history.models, 'get_strava_ride_analysis',
                      side_effect=analysis), \
         patch.object(route_history.strava_analysis, 'build_comparison',
                      side_effect=build_comparison):
        result = route_history.compute_same_route_segment_baseline(1, 42)

    assert result['Start'] == {
        'avg_segment_min': 70.0,   # (60+80)/2
        'avg_speed_mph': 11.0,     # (12+10)/2
        'avg_watts': 180,          # (200+160)/2
        'avg_cadence': 85,         # (80+90)/2
        'n_rides': 2,
    }
    assert result['Control A'] == {
        'avg_segment_min': 50.0,   # (40+60)/2
        'avg_speed_mph': 9.0,      # (10+8)/2
        'avg_watts': 160,          # (180+140)/2
        'avg_cadence': 65,         # (70+60)/2
        'n_rides': 2,
    }


def test_location_omitted_when_no_segment_min():
    # Control B never gets a segment_min → omitted; None watts skipped in mean.
    rows = [_row(1, ride_plan_id=42)]
    comp = _comparison([
        _seg('Start', 60, speed=12.0, watts=None, cadence=80),
        _seg('Control B', None, speed=None),
    ])
    with patch.object(route_history.models, 'get_rider_rides_with_cached_streams',
                      return_value=rows), \
         patch.object(route_history.models, 'get_all_ride_plans', return_value=[]), \
         patch.object(route_history.models, 'get_ride_plan_stops',
                      return_value=[{'location': 'Start'}, {'location': 'Control B'}]), \
         patch.object(route_history.models, 'get_strava_ride_analysis',
                      return_value={'detected_stops': []}), \
         patch.object(route_history.strava_analysis, 'build_comparison',
                      return_value=comp):
        result = route_history.compute_same_route_segment_baseline(1, 42)
    assert 'Control B' not in result
    assert result['Start']['avg_watts'] is None      # no watts contributed
    assert result['Start']['avg_cadence'] == 80
    assert result['Start']['n_rides'] == 1


def test_extra_rows_ignored():
    rows = [_row(1, ride_plan_id=42)]
    comp = _comparison([
        _seg('Start', 60, speed=12.0),
        _seg('Unplanned Gas Station', 15, speed=5.0, is_extra=True),
    ])
    with patch.object(route_history.models, 'get_rider_rides_with_cached_streams',
                      return_value=rows), \
         patch.object(route_history.models, 'get_all_ride_plans', return_value=[]), \
         patch.object(route_history.models, 'get_ride_plan_stops',
                      return_value=[{'location': 'Start'}]), \
         patch.object(route_history.models, 'get_strava_ride_analysis',
                      return_value={'detected_stops': []}), \
         patch.object(route_history.strava_analysis, 'build_comparison',
                      return_value=comp):
        result = route_history.compute_same_route_segment_baseline(1, 42)
    assert list(result.keys()) == ['Start']


# ── exclude_ride_id and max_rides ────────────────────────────────────────
def test_exclude_ride_id():
    rows = [_row(1, ride_plan_id=42, date='2025-02-01'),
            _row(2, ride_plan_id=42, date='2025-01-01')]
    with patch.object(route_history.models, 'get_rider_rides_with_cached_streams',
                      return_value=rows), \
         patch.object(route_history.models, 'get_all_ride_plans', return_value=[]), \
         patch.object(route_history.models, 'get_ride_plan_stops',
                      return_value=[{'location': 'Start'}]), \
         patch.object(route_history.models, 'get_strava_ride_analysis',
                      return_value={'detected_stops': []}), \
         patch.object(route_history.strava_analysis, 'build_comparison',
                      return_value=_comparison([_seg('Start', 60)])):
        result = route_history.compute_same_route_segment_baseline(
            1, 42, exclude_ride_id=1)
    # Only ride 2 counted.
    assert result['Start']['n_rides'] == 1


def test_max_rides_cap():
    # 5 same-route rides, cap to 3 → n_rides == 3. Newest dates kept.
    rows = [_row(i, ride_plan_id=42, date='2025-01-%02d' % (10 + i))
            for i in range(1, 6)]
    with patch.object(route_history.models, 'get_rider_rides_with_cached_streams',
                      return_value=rows), \
         patch.object(route_history.models, 'get_all_ride_plans', return_value=[]), \
         patch.object(route_history.models, 'get_ride_plan_stops',
                      return_value=[{'location': 'Start'}]), \
         patch.object(route_history.models, 'get_strava_ride_analysis',
                      return_value={'detected_stops': []}), \
         patch.object(route_history.strava_analysis, 'build_comparison',
                      return_value=_comparison([_seg('Start', 60)])):
        result = route_history.compute_same_route_segment_baseline(1, 42, max_rides=3)
    assert result['Start']['n_rides'] == 3


# ── robustness: never raise ──────────────────────────────────────────────
def test_bad_blob_skipped():
    rows = [_row(1, ride_plan_id=42, streams=b'not-zlib')]
    with patch.object(route_history.models, 'get_rider_rides_with_cached_streams',
                      return_value=rows), \
         patch.object(route_history.models, 'get_all_ride_plans', return_value=[]), \
         patch.object(route_history.models, 'get_ride_plan_stops',
                      return_value=[{'location': 'Start'}]), \
         patch.object(route_history.models, 'get_strava_ride_analysis',
                      return_value={'detected_stops': []}):
        # build_comparison NOT mocked, but we never reach it (bad blob).
        result = route_history.compute_same_route_segment_baseline(1, 42)
    assert result == {}


def test_build_comparison_raising_is_swallowed():
    rows = [_row(1, ride_plan_id=42)]
    real_stream = zlib.compress(json.dumps({'distance': [0, 100]}).encode())
    rows[0]['activity_streams'] = real_stream
    with patch.object(route_history.models, 'get_rider_rides_with_cached_streams',
                      return_value=rows), \
         patch.object(route_history.models, 'get_all_ride_plans', return_value=[]), \
         patch.object(route_history.models, 'get_ride_plan_stops',
                      return_value=[{'location': 'Start'}]), \
         patch.object(route_history.models, 'get_strava_ride_analysis',
                      return_value={'detected_stops': []}), \
         patch.object(route_history.strava_analysis, 'build_comparison',
                      side_effect=RuntimeError('boom')):
        result = route_history.compute_same_route_segment_baseline(1, 42)
    assert result == {}


def test_models_call_raising_returns_empty():
    with patch.object(route_history.models, 'get_rider_rides_with_cached_streams',
                      side_effect=Exception('db down')):
        assert route_history.compute_same_route_segment_baseline(1, 42) == {}


# ── end-to-end real decode (build_comparison NOT mocked) ─────────────────
def test_end_to_end_real_decode():
    """Feed a tiny real zlib-compressed stream and run the real build_comparison
    to prove the decompress→build_comparison path works end-to-end.
    """
    # A minimal but internally consistent stream: distance in meters, time in
    # seconds, monotonic. Two waypoints: Start (0 mi) and Finish (~6.2 mi).
    streams = {
        'distance': [0, 2500, 5000, 7500, 10000],   # meters (~6.2 mi total)
        'time': [0, 600, 1200, 1800, 2400],          # seconds
        'heartrate': [130, 135, 140, 145, 150],
        'watts': [150, 160, 170, 180, 190],
        'cadence': [80, 82, 84, 86, 88],
    }
    blob = zlib.compress(json.dumps(streams).encode())

    row = _row(1, ride_plan_id=42, streams=blob,
               strava_distance_m=10000, moving_time=2400, elapsed_time=2600,
               average_speed=4.17)

    # Plan stops: distance in miles cumulative. build_comparison reads several
    # optional keys with .get(); the ones it needs for segment timing are
    # 'distance_mi' (cumulative) and 'location'. Provide a simple 2-stop plan.
    stops = [
        {'location': 'Start', 'distance_miles': 0, 'stop_type': 'start',
         'stop_order': 0},
        {'location': 'Midpoint', 'distance_miles': 3.1, 'stop_type': 'control',
         'stop_order': 1},
        {'location': 'Finish', 'distance_miles': 6.21, 'stop_type': 'finish',
         'stop_order': 2},
    ]

    with patch.object(route_history.models, 'get_rider_rides_with_cached_streams',
                      return_value=[row]), \
         patch.object(route_history.models, 'get_all_ride_plans', return_value=[]), \
         patch.object(route_history.models, 'get_ride_plan_stops', return_value=stops), \
         patch.object(route_history.models, 'get_strava_ride_analysis',
                      return_value={'detected_stops': []}):
        result = route_history.compute_same_route_segment_baseline(1, 42)

    # We don't assert exact numbers (build_comparison's segment math is what it
    # is) — we prove the pipeline decoded the blob, ran the real comparison, and
    # produced at least one keyed segment baseline with the expected shape.
    assert isinstance(result, dict)
    assert result, 'expected at least one location baseline from real decode'
    for loc, agg in result.items():
        assert set(agg.keys()) == {
            'avg_segment_min', 'avg_speed_mph', 'avg_watts', 'avg_cadence', 'n_rides'}
        assert agg['n_rides'] >= 1
        assert isinstance(agg['avg_segment_min'], float)


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-q']))
