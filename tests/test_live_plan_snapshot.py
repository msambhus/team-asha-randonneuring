"""Live-page plan snapshot + cache-first route geometry (routes/live.py).

- `_build_plan_snapshot` summarizes the ride's resolved plan for the panel beside the
  live climb profile (name/slug/distance/climb/controls/cutoff/start), fail-soft.
- `_radial_track` prefers the cron-warmed route_geometry_cache over a live RWGPS
  fetch, so the live route line + profile render for routes the authed API can't serve.
"""
from unittest.mock import patch
from datetime import timedelta
from zoneinfo import ZoneInfo

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


def test_plan_snapshot_treats_unprefixed_itinerary_as_day_one(app):
    stops = [
        {'location': 'Start', 'stop_type': 'start', 'distance_miles': 0,
         'segment_time_min': 0, 'stop_duration_min': 0, 'elevation_gain': 0},
        {'location': 'Finish', 'stop_type': 'finish', 'distance_miles': 126,
         'segment_time_min': 720, 'stop_duration_min': 10, 'elevation_gain': 5100},
    ]
    with app.app_context(), \
         patch('routes.live._resolve_base_plan', return_value=_PLAN), \
         patch('routes.live.get_ride_plan_stops', return_value=stops):
        snap = live._build_plan_snapshot(_RIDE)
    assert snap['active_day'] == 1
    assert snap['day_distance_mi'] == 126.0
    assert snap['day_elevation_ft'] == 5100
    assert snap['day_moving_min'] == 720
    assert [row['name'] for row in snap['day_stops']] == ['Start', 'Finish']


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
    assert snap['day_distance_mi'] == 50.0
    assert snap['day_controls'] == 1
    assert snap['day_moving_min'] == 240
    assert snap['day_stopped_min'] == 15
    assert snap['day_elapsed_min'] == 255
    assert snap['day_stops'][1]['time_bank_min'] is not None


def test_plan_snapshot_uses_event_local_calendar_day(app):
    event_today = live.datetime.now(ZoneInfo('America/Chicago')).date()
    stops = [
        {'location': 'Day 1: Start', 'stop_type': 'start',
         'distance_miles': 0, 'segment_time_min': 0, 'stop_duration_min': 0},
        {'location': 'Day 2: Control B', 'stop_type': 'control',
         'distance_miles': 160, 'segment_time_min': 600, 'stop_duration_min': 20},
    ]
    ride = dict(_RIDE, date=str(event_today - timedelta(days=1)), region='Minnesota')
    with app.app_context(), \
         patch('routes.live._resolve_base_plan', return_value=_PLAN), \
         patch('routes.live.get_ride_plan_stops', return_value=stops):
        snap = live._build_plan_snapshot(ride)
    assert snap['active_day'] == 2
    assert [row['name'] for row in snap['day_stops']] == ['Control B']


def test_plan_snapshot_shows_event_time_with_pacific_secondary(app):
    stops = [
        {'location': 'Day 1: Start', 'stop_type': 'start',
         'distance_miles': 0, 'segment_time_min': 0, 'stop_duration_min': 0},
        {'location': 'Day 1: Control A', 'stop_type': 'control',
         'distance_miles': 31.3, 'segment_time_min': 130, 'stop_duration_min': 0},
    ]
    ride = dict(_RIDE, date='2026-08-06', region='Minnesota', start_time='05:00')
    with app.app_context(), \
         patch('routes.live._resolve_base_plan', return_value=_PLAN), \
         patch('routes.live.get_ride_plan_stops', return_value=stops):
        snap = live._build_plan_snapshot(ride, selected_day=1)

    assert snap['start_time_event'] == '05:00'
    assert snap['start_time_event_zone'] == 'CT'
    assert snap['start_time_pacific'] == '03:00'
    assert snap['show_pacific_time'] is True
    assert snap['day_stops'][1]['eta'] == '07:10'
    assert snap['day_stops'][1]['eta_event_zone'] == 'CT'
    assert snap['day_stops'][1]['eta_pacific'] == '05:10'


def test_plan_snapshot_follows_explicit_map_day(app):
    stops = [
        {'location': 'Day 1: Start', 'stop_type': 'start',
         'distance_miles': 0, 'segment_time_min': 0, 'stop_duration_min': 0},
        {'location': 'Day 2: Control B', 'stop_type': 'control',
         'distance_miles': 160, 'segment_time_min': 600, 'stop_duration_min': 20},
    ]
    future_ride = dict(_RIDE, date='2099-08-06')
    with app.app_context(), \
         patch('routes.live._resolve_base_plan', return_value=_PLAN), \
         patch('routes.live.get_ride_plan_stops', return_value=stops):
        snap = live._build_plan_snapshot(future_ride, selected_day=2)
    assert snap['active_day'] == 2
    assert snap['is_current_day'] is False
    assert [row['name'] for row in snap['day_stops']] == ['Control B']


def test_active_plan_leg_switches_route_and_forecast_date(app):
    ride = dict(_RIDE, date='2026-08-06', region='Minnesota')
    legs = [
        {'rwgps_url': 'https://ridewithgps.com/routes/111', 'day_number': 1},
        {'rwgps_url': 'https://ridewithgps.com/routes/222', 'day_number': 2},
    ]
    stops = [
        {'location': 'Day 1: Start', 'distance_miles': 0},
        {'location': 'Day 2: Start', 'distance_miles': 100},
    ]
    now = live.datetime(2026, 8, 7, 12, tzinfo=live.timezone.utc)
    with app.app_context(), \
         patch('routes.live._resolve_base_plan', return_value=_PLAN), \
         patch('models.get_ride_plan_legs', return_value=legs), \
         patch('routes.live.get_ride_plan_stops', return_value=stops):
        leg = live._active_plan_leg(ride, now=now)
    assert leg['day_number'] == 2
    assert leg['rwgps_url'].endswith('/222')
    assert leg['forecast_date'].isoformat() == '2026-08-07'
    assert leg['distance_offset_mi'] == 100


def test_active_plan_leg_waits_for_planned_overnight_departure(app):
    ride = dict(_RIDE, date='2026-08-06', start_time='06:00', region='Minnesota')
    legs = [
        {'rwgps_url': 'https://ridewithgps.com/routes/111', 'day_number': 1},
        {'rwgps_url': 'https://ridewithgps.com/routes/222', 'day_number': 2},
    ]
    stops = [
        {'location': 'Day 1: Start', 'distance_miles': 0,
         'segment_time_min': 0, 'stop_duration_min': 0},
        {'location': 'Day 1: Overnight', 'distance_miles': 235,
         'segment_time_min': 1200, 'stop_duration_min': 240},
        {'location': 'Day 2: First control', 'distance_miles': 270,
         'segment_time_min': 180, 'stop_duration_min': 20},
    ]
    # Midnight Central is only 18 hours after a 6 AM start: still Day 1.
    midnight_central = live.datetime(2026, 8, 7, 5, 1, tzinfo=live.timezone.utc)
    # The Day 1 riding + sleep plan totals 24h, so 6:01 AM starts Day 2.
    departure_central = live.datetime(2026, 8, 7, 11, 1, tzinfo=live.timezone.utc)
    with app.app_context(), \
         patch('routes.live._resolve_base_plan', return_value=_PLAN), \
         patch('models.get_ride_plan_legs', return_value=legs), \
         patch('routes.live.get_ride_plan_stops', return_value=stops):
        before = live._active_plan_leg(ride, now=midnight_central)
        after = live._active_plan_leg(ride, now=departure_central)
    assert before['day_number'] == 1
    assert after['day_number'] == 2


def test_progress_day_uses_next_day_start_distance(app):
    ride = dict(_RIDE, id=194)
    legs = [
        {'rwgps_url': 'https://ridewithgps.com/routes/111', 'day_number': 1},
        {'rwgps_url': 'https://ridewithgps.com/routes/222', 'day_number': 2},
    ]
    stops = [
        {'location': 'Day 1: Start', 'distance_miles': 0},
        {'location': 'Day 2: Start', 'distance_miles': 235},
    ]
    full_course_track = [
        {'lat': 44.1, 'lng': -92.1, 'dist_m': 0},
        {'lat': 44.0, 'lng': -92.0, 'dist_m': 235 * 1609.344},
        {'lat': 43.9, 'lng': -91.9, 'dist_m': 236 * 1609.344},
    ]
    with app.test_request_context(), \
         patch('routes.live.get_latest_positions_for_ride', return_value=[{
             'lat': 44.0, 'lng': -92.0,
         }]), \
         patch('routes.live._radial_overview_track',
               return_value=full_course_track):
        active = live._progress_day_number(ride, legs, stops)

    assert active == 2


def test_all_day_weather_summarizes_each_stored_leg(app):
    ride = dict(_RIDE, date='2026-08-06', region='Minnesota', start_time='06:00')
    legs = [
        {'rwgps_url': 'https://ridewithgps.com/routes/111', 'day_number': 1},
        {'rwgps_url': 'https://ridewithgps.com/routes/222', 'day_number': 2},
        {'rwgps_url': 'https://ridewithgps.com/routes/333', 'day_number': 3},
        {'rwgps_url': 'https://ridewithgps.com/routes/444', 'day_number': 4},
    ]
    samples = [{'distance_m': 0}, {'distance_m': 160934.4}]
    chart = {'labels': [0, 100], 'headwind_mph': [-4, 12],
             'temperature_f': [56, 82], 'elevation_ft': [100, 200]}
    plan_stops = [
        {'location': 'Day 1: Start', 'distance_miles': 0},
        {'location': 'Day 2: Start', 'distance_miles': 235},
        {'location': 'Day 3: Start', 'distance_miles': 417},
        {'location': 'Day 4: Start', 'distance_miles': 611},
    ]
    with app.test_request_context(), \
         patch('routes.live._resolve_base_plan',
               return_value=dict(_PLAN, slug='coulee-challenge')), \
         patch('models.get_ride_plan_legs', return_value=legs), \
         patch('routes.live._active_plan_leg', return_value={'day_number': 2}), \
         patch('routes.live.get_ride_plan_stops', return_value=plan_stops), \
         patch('routes.live.load_stored_route_weather',
               return_value=([{'forecast': True}], samples)), \
         patch('routes.live.get_route_elevation_track', return_value=[]), \
         patch('routes.live._build_live_chart_data', return_value=chart), \
         patch('routes.live._build_plan_snapshot',
               side_effect=lambda _ride, selected_day=None: {'active_day': selected_day}):
        summary = live._build_all_day_weather(ride)

    assert len(summary['days']) == 4
    assert summary['days'][1]['is_current'] is True
    assert summary['days'][0]['distance_mi'] == 100
    assert summary['days'][0]['chart_data'] == chart
    assert summary['days'][0]['available'] is True
    assert summary['days'][0]['temperature_min_f'] == 56
    assert summary['days'][0]['temperature_max_f'] == 82
    assert summary['days'][0]['peak_headwind_mph'] == 12
    assert summary['days'][0]['peak_tailwind_mph'] == 4
    assert summary['days'][1]['start_distance_mi'] == 235.0
    assert summary['days'][2]['plan']['active_day'] == 3
    assert 'plan_slug=coulee-challenge' in summary['url']


def test_all_day_weather_is_hidden_for_single_leg(app):
    with app.app_context(), \
         patch('routes.live._resolve_base_plan', return_value=_PLAN), \
         patch('models.get_ride_plan_legs', return_value=[{
             'rwgps_url': _RIDE['rwgps_url'], 'day_number': 1,
         }]):
        assert live._build_all_day_weather(_RIDE) is None


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


def test_all_weather_points_include_every_route_day(app):
    ride = dict(_RIDE, date='2026-08-06')
    legs = [
        {'rwgps_url': 'https://ridewithgps.com/routes/111', 'day_number': 1},
        {'rwgps_url': 'https://ridewithgps.com/routes/222', 'day_number': 2},
    ]
    stops = [
        {'location': 'Day 1: Start', 'distance_miles': 0},
        {'location': 'Day 2: Start', 'distance_miles': 235},
    ]
    with app.app_context(), \
         patch('routes.live._resolve_base_plan', return_value=_PLAN), \
         patch('models.get_ride_plan_legs', return_value=legs), \
         patch('routes.live.get_ride_plan_stops', return_value=stops), \
         patch('routes.live._build_weather_points',
               side_effect=lambda _ride, leg=None: [{
                   'day': leg['day_number'],
                   'date': leg['forecast_date'].isoformat(),
               }]):
        points = live._build_all_weather_points(ride)

    assert points == [
        {'day': 1, 'date': '2026-08-06'},
        {'day': 2, 'date': '2026-08-07'},
    ]


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
         patch('routes.live.get_route_weather_elevation_track', return_value=None), \
         patch('routes.live.fetch_route', return_value=route_json) as fetch:
        track = live._radial_track(_RIDE)
    fetch.assert_called_once()                   # fell back to the live fetch
    assert track and track[0]['lat'] == 37.72


def test_radial_track_uses_stored_weather_track_before_live_fetch(app):
    """A private multi-day leg uses road-shaped cached forecast geometry."""
    weather_track = [
        {'lat': 44.9, 'lng': -93.1, 'dist_m': 0, 'e_m': 300},
        {'lat': 44.8, 'lng': -92.9, 'dist_m': 16093.44, 'e_m': 320},
    ]
    leg = {'rwgps_url': 'https://ridewithgps.com/routes/55704679',
           'forecast_date': '2026-08-07', 'distance_offset_mi': 235}
    with app.app_context(), \
         patch('routes.live.get_route_elevation_track', return_value=None), \
         patch('routes.live.get_route_weather_elevation_track',
               return_value=weather_track), \
         patch('routes.live.fetch_route') as fetch:
        track = live._radial_track(_RIDE, leg)
    assert len(track) == 2
    assert track[0]['dist_m'] == 235 / live.M_TO_MI
    assert track[1]['lat'] == 44.8
    fetch.assert_not_called()


def test_radial_polyline_keeps_warmed_long_route_detail():
    """A 4,020-point brevet cache must not be reduced to sparse road chords."""
    track = [
        {'lat': 44.0 + i / 100000, 'lng': -92.0 + i / 100000,
         'dist_m': i * 300, 'e_m': 250}
        for i in range(4020)
    ]

    polyline = live._radial_polyline(track)

    assert len(polyline) == 4020
    assert polyline[0] == [-92.0, 44.0]
    assert polyline[-1] == [track[-1]['lng'], track[-1]['lat']]


def test_radial_overview_track_uses_primary_plan_route_only(app):
    ride = dict(_RIDE, date='2026-08-06')
    plan = dict(_PLAN, rwgps_url='https://ridewithgps.com/routes/999')
    with app.app_context(), \
         patch('routes.live._resolve_base_plan', return_value=plan), \
         patch('routes.live._radial_track', return_value=_TRACK) as radial_track:
        track = live._radial_overview_track(ride)

    assert track == _TRACK
    radial_track.assert_called_once()
    assert radial_track.call_args.args[1] == {
        'rwgps_url': 'https://ridewithgps.com/routes/999',
        'distance_offset_mi': 0.0,
    }
