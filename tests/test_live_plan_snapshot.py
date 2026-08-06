"""Live-page plan snapshot + cache-first route geometry (routes/live.py).

- `_build_plan_snapshot` summarizes the ride's resolved plan for the panel beside the
  live climb profile (name/slug/distance/climb/controls/cutoff/start), fail-soft.
- `_radial_track` prefers the cron-warmed route_geometry_cache over a live RWGPS
  fetch, so the live route line + profile render for routes the authed API can't serve.
"""
from unittest.mock import patch

import routes.live as live


_RIDE = {'id': 193, 'name': 'Heart of the Valley 200k', 'ride_plan_id': 64,
         'plan_slug': '00975-heart-of-the-valley', 'time_limit_hours': 13.5,
         'start_time': '02:00', 'rwgps_url': 'https://ridewithgps.com/routes/34227438'}

_PLAN = {'id': 64, 'name': 'Heart of the Valley 200k', 'slug': '00975-heart-of-the-valley',
         'total_distance_miles': 127.5, 'total_elevation_ft': 1004, 'cutoff_hours': 13.5}

_STOPS = [{'stop_type': 'start'}, {'stop_type': 'control'}, {'stop_type': 'control'},
          {'stop_type': 'rest'}, {'stop_type': 'control'}, {'stop_type': 'finish'}]

_TRACK = [{'lat': 37.72, 'lng': -121.43, 'dist_m': 0.0, 'e_m': 23.3},
          {'lat': 37.73, 'lng': -121.42, 'dist_m': 5000.0, 'e_m': 40.0}]


# --------------------------------------------------------------------------- #
# _build_plan_snapshot
# --------------------------------------------------------------------------- #
def test_plan_snapshot_summarizes_resolved_plan(app):
    with app.app_context(), \
         patch('routes.live._resolve_base_plan', return_value=_PLAN), \
         patch('routes.live.get_ride_plan_stops', return_value=_STOPS):
        snap = live._build_plan_snapshot(_RIDE)
    assert snap['name'] == 'Heart of the Valley 200k'
    assert snap['slug'] == '00975-heart-of-the-valley'
    assert snap['distance_mi'] == 128            # 127.5 rounded
    assert snap['elevation_ft'] == 1004
    assert snap['controls'] == 3                 # only stop_type == 'control'
    assert snap['cutoff_hours'] == 13.5
    assert snap['start_time'] == '02:00'


def test_plan_snapshot_includes_only_active_day_controls(app):
    stops = [
        {'location': 'Day 1: Start', 'stop_type': 'start',
         'distance_miles': 0, 'segment_time_min': 0, 'stop_duration_min': 0},
        {'location': 'Day 1: Control A', 'stop_type': 'control',
         'distance_miles': 50, 'segment_time_min': 240, 'stop_duration_min': 15},
        {'location': 'Day 2: Control B', 'stop_type': 'control',
         'distance_miles': 160, 'segment_time_min': 600, 'stop_duration_min': 20},
    ]
    future_ride = dict(_RIDE, date='2099-08-06')
    with app.app_context(), \
         patch('routes.live._resolve_base_plan', return_value=_PLAN), \
         patch('routes.live.get_ride_plan_stops', return_value=stops):
        snap = live._build_plan_snapshot(future_ride)
    assert snap['active_day'] == 1
    assert [row['name'] for row in snap['day_stops']] == ['Start', 'Control A']
    assert snap['day_stops'][1]['distance_mi'] == 50.0
    assert snap['day_stops'][1]['eta'] == '06:00'


def test_plan_snapshot_none_when_no_plan(app):
    with app.app_context(), patch('routes.live._resolve_base_plan', return_value=None):
        assert live._build_plan_snapshot(_RIDE) is None


def test_plan_snapshot_fail_soft_on_error(app):
    with app.app_context(), \
         patch('routes.live._resolve_base_plan', side_effect=RuntimeError('db down')):
        assert live._build_plan_snapshot(_RIDE) is None


def test_plan_snapshot_bad_cutoff_drops_only_that_stat(app):
    with app.app_context(), \
         patch('routes.live._resolve_base_plan',
               return_value=dict(_PLAN, cutoff_hours='not-a-number')), \
         patch('routes.live.get_ride_plan_stops', return_value=_STOPS):
        snap = live._build_plan_snapshot(dict(_RIDE, time_limit_hours=None))
    assert snap is not None                      # panel still built
    assert snap['cutoff_hours'] is None          # only the cutoff stat is dropped
    assert snap['distance_mi'] == 128


# --------------------------------------------------------------------------- #
# _radial_track cache-first
# --------------------------------------------------------------------------- #
def test_radial_track_prefers_warmed_cache(app):
    """A warmed cache is returned without any live RWGPS fetch."""
    with app.app_context(), \
         patch('routes.live.get_route_elevation_track', return_value=_TRACK) as cache, \
         patch('routes.live.fetch_route') as fetch:
        track = live._radial_track(_RIDE)
    assert track == _TRACK
    cache.assert_called_once_with('34227438')
    fetch.assert_not_called()                    # never hits the live API when warmed


def test_radial_track_falls_back_on_cold_cache(app):
    """A cold (None) cache falls back to the live fetch path."""
    route_json = {'track_points': [
        {'x': -121.43, 'y': 37.72, 'd': 0.0, 'e': 23.3},
        {'x': -121.42, 'y': 37.73, 'd': 5000.0, 'e': 40.0}]}
    with app.app_context(), \
         patch('routes.live.get_route_elevation_track', return_value=None), \
         patch('routes.live.fetch_route', return_value=route_json) as fetch:
        track = live._radial_track(_RIDE)
    fetch.assert_called_once()                   # fell back to the live fetch
    assert track and track[0]['lat'] == 37.72
