"""Integration tests for live telemetry: Garmin field capture, history-append
cron, beacon speed, and the /api/live/positions telemetry block + caching.
All external HTTP mocked; models patched so no DB is needed.
"""
import json

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import requests

from services import garmin_livetrack
from cache import cache
import routes.live as live_module


def _now():
    return datetime.now(timezone.utc)


def _login(client, rider_id=7):
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = rider_id


# ── Garmin field capture ──────────────────────────────────────────────────

def test_extract_point_captures_fitness_fields():
    raw = {
        'position': {'lat': 37.8, 'lon': -122.2},
        'dateTime': '2026-06-23T14:00:00Z',
        'fitnessPointData': {'heartRate': 142, 'power': 210, 'cadence': 88},
        'speed': 7.5,
    }
    p = garmin_livetrack._extract_point(raw)
    assert p['heart_rate'] == 142
    assert p['power'] == 210
    assert p['cadence'] == 88
    assert p['speed'] == 7.5


def test_extract_point_missing_fitness_is_none():
    p = garmin_livetrack._extract_point({'lat': 37.8, 'lng': -122.2, 'dateTime': '2026-06-23T14:00:00Z'})
    assert p['heart_rate'] is None and p['power'] is None and p['cadence'] is None


def _share_html(points):
    """Embed points the way Garmin server-renders them (escaped RSC payload)."""
    esc = json.dumps(points).replace('\\', '\\\\').replace('"', '\\"')
    return ('<html><body><script>self.__next_f.push([1,"a:{\\"trackPoints\\":'
            + esc + '}"])</script></body></html>')


def test_fetch_positions_carries_fields(app):
    points = [
        {'position': {'lat': 37.8, 'lon': -122.2}, 'dateTime': '2026-06-23T14:00:00Z',
         'fitnessPointData': {'heartRate': 150}, 'speed': 6.0},
    ]
    resp = MagicMock(status_code=200, ok=True, text=_share_html(points))
    with app.app_context():
        with patch.object(requests, 'get', return_value=resp):
            pts = garmin_livetrack.fetch_positions('tok', 'sess')
    assert pts[0]['heart_rate'] == 150 and pts[0]['speed'] == 6.0


# ── Cron: append downsampled history ──────────────────────────────────────

def test_cron_appends_downsampled_history(client, app):
    app.config['CRON_SECRET'] = 'testsecret'
    tracked = [{
        'rider_id': 7,
        'garmin_session_url': 'https://livetrack.garmin.com/session/s1/token/t1',
        'garmin_session_token': 't1',
        'active_ride_id': 5,
    }]
    base = _now() - timedelta(minutes=10)
    points = [
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': base, 'speed': 5},
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': base + timedelta(seconds=10)},  # dropped (<30s)
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': base + timedelta(seconds=40)},  # kept
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': base + timedelta(seconds=80)},  # kept
    ]
    inserts = []
    with patch('models.get_enabled_live_tracking', return_value=tracked), \
         patch('models.get_last_position_recorded_at', return_value=None), \
         patch('services.garmin_livetrack.fetch_positions', return_value=points), \
         patch('models.insert_live_position', side_effect=lambda **kw: inserts.append(kw) or True), \
         patch('models.purge_old_positions', return_value=0):
        resp = client.post('/api/cron/poll-garmin-livetrack',
                           headers={'Authorization': 'Bearer testsecret'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['inserted'] == 3            # 4 points, one dropped by downsample
    assert len(inserts) == 3
    assert inserts[0]['source'] == 'garmin'
    assert inserts[0]['speed'] == 5         # fields forwarded


def test_cron_skips_already_stored_points(client, app):
    app.config['CRON_SECRET'] = 'testsecret'
    tracked = [{'rider_id': 7,
                'garmin_session_url': 'https://livetrack.garmin.com/session/s1/token/t1',
                'garmin_session_token': 't1', 'active_ride_id': 5}]
    base = _now() - timedelta(minutes=10)
    points = [
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': base},
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': base + timedelta(seconds=60)},
    ]
    # last stored == base+60 → only points strictly newer are inserted (none here)
    with patch('models.get_enabled_live_tracking', return_value=tracked), \
         patch('models.get_last_position_recorded_at', return_value=base + timedelta(seconds=60)), \
         patch('services.garmin_livetrack.fetch_positions', return_value=points), \
         patch('models.insert_live_position', return_value=True) as ins, \
         patch('models.purge_old_positions', return_value=0):
        resp = client.post('/api/cron/poll-garmin-livetrack',
                           headers={'Authorization': 'Bearer testsecret'})
    assert resp.status_code == 200
    assert resp.get_json()['inserted'] == 0
    ins.assert_not_called()


# ── Beacon captures speed ──────────────────────────────────────────────────

def test_beacon_captures_speed(client):
    captured = {}
    _login(client, rider_id=7)
    with patch('routes.live.get_live_tracking', return_value={'enabled': True}), \
         patch('routes.live.insert_live_position', side_effect=lambda **kw: captured.update(kw) or True):
        resp = client.post('/api/live/beacon',
                           json={'ride_id': 5, 'lat': 37.8, 'lng': -122.2, 'speed': 6.1})
    assert resp.status_code == 200
    assert captured['speed'] == 6.1
    assert captured['source'] == 'beacon'
    assert captured['ride_id'] == 5


# ── /api/live/positions telemetry block ────────────────────────────────────

_FAKE_CTX = {
    'has_route': True,
    'track': [
        {'lat': 37.0, 'lng': -122.00, 'dist_m': 0.0},
        {'lat': 37.0, 'lng': -121.99, 'dist_m': 889.0},
        {'lat': 37.0, 'lng': -121.98, 'dist_m': 1778.0},
    ],
    'cum_ascent_ft': [0, 100, 200],
    'total_dist_m': 1778.0,
    'total_ascent_ft': 200,
    'plan_stops': [{'distance_miles': 0, 'cum_time_min': 0},
                   {'distance_miles': 1.1, 'cum_time_min': 30}],
    'wind_by_dist': [{'dist_m': 0, 'headwind_kmh': 12}, {'dist_m': 1778, 'headwind_kmh': -4}],
    'ride_start_iso': (datetime(2026, 6, 23, 7, 0, tzinfo=timezone.utc)).isoformat(),
}


def test_positions_includes_telemetry(client):
    _login(client)
    now = _now()
    row = {'rider_id': 7, 'name': 'Asha Rider', 'lat': 37.0, 'lng': -121.99,
           'recorded_at': now - timedelta(minutes=2), 'status': 'GOING',
           'speed': 6.0, 'heart_rate': 140, 'power': None, 'cadence': None}
    history = [
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': now - timedelta(minutes=30), 'speed': 5.0},
        {'lat': 37.0, 'lng': -121.99, 'recorded_at': now - timedelta(minutes=2), 'speed': 6.0},
    ]
    with patch('routes.live.get_latest_positions_for_ride', return_value=[row]), \
         patch('routes.live._ride_live_context', return_value=_FAKE_CTX), \
         patch('routes.live.get_positions_for_rider_since', return_value=history):
        resp = client.get('/api/live/positions?ride_id=5')
    assert resp.status_code == 200
    pos = resp.get_json()['positions'][0]
    t = pos['telemetry']
    assert t is not None
    assert t['now']['distance_mi'] is not None
    # Average speeds (elapsed + moving) accompany the instantaneous speed.
    assert 'avg_elapsed_speed_mph' in t['now'] and 'avg_moving_speed_mph' in t['now']
    if t['now']['moving_min']:
        assert t['now']['avg_moving_speed_mph'] == round(
            t['now']['distance_mi'] / (t['now']['moving_min'] / 60.0), 1)
    assert t['now']['heart_rate'] == 140
    assert t['remaining']['toughness'] is not None
    assert t['detailed_after_ride'] is True
    assert 'status' in (t['plan'] or {})
    # On-route breadcrumb trail of where the rider has ridden.
    assert pos['trail'] and len(pos['trail']) >= 1
    assert pos['trail'][0] == [-122.0, 37.0]   # [lng,lat], on-route history point
    # Plan-timing dot color is present and agrees with the telemetry plan status.
    assert pos['plan_color'].startswith('#')
    from routes.live import PLAN_AHEAD_COLOR, PLAN_BEHIND_COLOR
    if (t['plan'] or {}).get('status') == 'behind':
        assert pos['plan_color'] == PLAN_BEHIND_COLOR
    elif (t['plan'] or {}).get('status') in ('ahead', 'on'):
        assert pos['plan_color'] == PLAN_AHEAD_COLOR


# ── plan-timing dot color (ahead/behind) ───────────────────────────────────

def test_plan_dot_color_precedence():
    from routes.live import (_plan_dot_color, STATUS_COLORS,
                             PLAN_AHEAD_COLOR, PLAN_BEHIND_COLOR, PLAN_UNKNOWN_COLOR)
    ahead = {'on_route': True, 'plan': {'status': 'ahead', 'delta_min': 10}}
    on = {'on_route': True, 'plan': {'status': 'on', 'delta_min': 0}}
    behind = {'on_route': True, 'plan': {'status': 'behind', 'delta_min': -10}}
    offroute = {'on_route': False}
    noplan = {'on_route': True, 'plan': None}

    assert _plan_dot_color('GOING', ahead) == PLAN_AHEAD_COLOR
    assert _plan_dot_color('GOING', on) == PLAN_AHEAD_COLOR       # on-plan = green
    assert _plan_dot_color('GOING', behind) == PLAN_BEHIND_COLOR
    assert _plan_dot_color('GOING', offroute) == PLAN_UNKNOWN_COLOR   # off-route → grey
    assert _plan_dot_color('FINISHED', ahead) == PLAN_UNKNOWN_COLOR   # finished → grey
    # No plan matched → fall back to the signup-status color (no regression).
    assert _plan_dot_color('INTERESTED', noplan) == STATUS_COLORS['INTERESTED']
    assert _plan_dot_color('GOING', None) == STATUS_COLORS['GOING']


def test_plan_dot_color_keeps_pace_color_when_stale():
    """Staleness is shown by dimming, NOT greying — a stale-but-behind rider keeps
    a red dot so the map dot agrees with the card's 'behind' badge (which is not
    stale-gated). Only finished/off-route/no-plan change the color."""
    from routes.live import _plan_dot_color, PLAN_BEHIND_COLOR, PLAN_AHEAD_COLOR
    behind = {'on_route': True, 'plan': {'status': 'behind', 'delta_min': -10}}
    ahead = {'on_route': True, 'plan': {'status': 'ahead', 'delta_min': 10}}
    assert _plan_dot_color('GOING', behind) == PLAN_BEHIND_COLOR
    assert _plan_dot_color('GOING', ahead) == PLAN_AHEAD_COLOR


def test_ride_live_context_resolves_plan_and_times_it():
    """The live context resolves the base plan via _resolve_base_plan (FK-slug OR
    route-name match) and computes its timing — so a FK-null, name-matched ride
    (e.g. SCR 600k) still gets plan stops + base_plan_id for the ahead/behind delta."""
    from routes.live import _ride_live_context
    ride = {'id': 5, 'name': 'Surf City 600k', 'ride_plan_id': None, 'plan_slug': None,
            'time_limit_hours': 40, 'distance_km': 600,
            'rwgps_url': None, 'rwgps_url_team': None, 'date': None, 'start_time': None}
    plan = {'id': 9, 'slug': 'scr-600', 'total_distance_miles': 375, 'cutoff_hours': None}
    stops = [{'distance_miles': 0, 'segment_time_min': 0, 'stop_duration_min': 0, 'elevation_gain': 0},
             {'distance_miles': 100, 'segment_time_min': 360, 'stop_duration_min': 15, 'elevation_gain': 3000}]
    with patch('routes.live.get_ride_by_id', return_value=ride), \
         patch('routes.live._resolve_base_plan', return_value=plan), \
         patch('routes.live.get_ride_plan_stops', return_value=stops), \
         patch('routes.live._ride_start_utc', return_value=None):
        ctx = _ride_live_context.uncached(5)   # bypass the per-ride memoize
    assert ctx['base_plan_id'] == 9
    assert ctx['plan_cutoff_hours'] == 40.0     # event time_limit_hours wins
    assert ctx['plan_total_mi'] == 375.0
    assert len(ctx['plan_stops']) == 2 and ctx['has_plan'] is True
    assert ctx['plan_stops'][1]['cum_time_min'] == 375   # 360 seg + 15 stop


# ── per-rider custom plan resolution for the live delta ────────────────────

_BASE_STOPS = [{'distance_miles': 0, 'cum_time_min': 0},
               {'distance_miles': 10, 'cum_time_min': 60}]


def test_rider_plan_stops_prefers_custom_plan():
    from routes.live import _rider_plan_stops
    ctx = {'plan_stops': _BASE_STOPS, 'base_plan_id': 7,
           'plan_cutoff_hours': 10, 'plan_total_mi': 100}
    custom_raw = [{'distance_miles': 0, 'cum_time_min': 0},
                  {'distance_miles': 10, 'cum_time_min': 50}]   # 50, not base's 60
    with patch('models.get_custom_plan', return_value={'id': 99}), \
         patch('services.custom_plan_service.get_merged_plan_stops', return_value=(['m'], {'id': 99})), \
         patch('services.custom_plan_service.recalculate_cumulative_values', return_value=custom_raw):
        stops = _rider_plan_stops(ctx, rider_id=7)
    assert stops[1]['cum_time_min'] == 50.0            # graded against the rider's custom plan


def test_rider_plan_stops_falls_back_to_base():
    from routes.live import _rider_plan_stops
    ctx = {'plan_stops': _BASE_STOPS, 'base_plan_id': 7,
           'plan_cutoff_hours': 10, 'plan_total_mi': 100}
    with patch('models.get_custom_plan', return_value=None):
        assert _rider_plan_stops(ctx, 7) is _BASE_STOPS       # no custom → base
    # No base plan id → base, without even a custom lookup.
    assert _rider_plan_stops({'plan_stops': _BASE_STOPS, 'base_plan_id': None}, 7) is _BASE_STOPS


def test_rider_plan_stops_custom_under_two_stops_falls_back():
    """A custom plan that yields fewer than 2 usable stops can't grade pace, so we
    fall back to the base plan rather than return an unusable single point."""
    from routes.live import _rider_plan_stops
    ctx = {'plan_stops': _BASE_STOPS, 'base_plan_id': 7,
           'plan_cutoff_hours': 10, 'plan_total_mi': 100}
    one_stop = [{'distance_miles': 0, 'cum_time_min': 0}]
    with patch('models.get_custom_plan', return_value={'id': 99}), \
         patch('services.custom_plan_service.get_merged_plan_stops', return_value=(['m'], {'id': 99})), \
         patch('services.custom_plan_service.recalculate_cumulative_values', return_value=one_stop):
        assert _rider_plan_stops(ctx, 7) is _BASE_STOPS


# ── Item 1-3: arrival ETA, required speed, time banked ─────────────────────

def _arrival_ctx(start_dt, **over):
    """A ctx whose next control (1.1 mi) has arrival_time_min (20) distinct from
    cum_time_min (30), plus plan total/cutoff for the banked-vs-cutoff metric."""
    ctx = dict(_FAKE_CTX,
               ride_start_iso=start_dt.isoformat(),
               plan_total_mi=100.0, plan_cutoff_hours=10,
               plan_stops=[
                   {'distance_miles': 0, 'cum_time_min': 0, 'arrival_time_min': 0,
                    'location': 'Start', 'stop_type': 'start'},
                   {'distance_miles': 1.1, 'cum_time_min': 30, 'arrival_time_min': 20,
                    'location': 'Control 1', 'stop_type': 'control'}])
    ctx.update(over)
    return ctx


def _run_positions(client, ctx, row_over=None):
    now = _now()
    row = {'rider_id': 7, 'name': 'Asha Rider', 'lat': 37.0, 'lng': -121.99,
           'recorded_at': now - timedelta(minutes=2), 'status': 'GOING',
           'speed': 6.0, 'heart_rate': None, 'power': None, 'cadence': None}
    if row_over:
        row.update(row_over)
    with patch('routes.live.get_latest_positions_for_ride', return_value=[row]), \
         patch('routes.live._ride_live_context', return_value=ctx), \
         patch('routes.live.get_positions_for_rider_since', return_value=[]):
        resp = client.get('/api/live/positions?ride_id=5')
    return resp


def test_next_control_eta_uses_arrival_not_departure(client):
    """The next-control ETA is the plan's ARRIVAL time (arrival_time_min=20),
    earlier than the departure (cum_time_min=30) for a control with a break."""
    _login(client)
    start = _now() - timedelta(minutes=5)
    resp = _run_positions(client, _arrival_ctx(start))
    nc = resp.get_json()['positions'][0]['telemetry']['next_control']
    assert nc['arrival_time_min'] == 20
    # eta_iso is start + arrival(20), NOT start + cum(30).
    eta = datetime.fromisoformat(nc['eta_iso'])
    assert round((eta - start).total_seconds() / 60) == 20


def test_next_control_required_speed_on_time(client):
    """A rider on pace gets a positive required speed and behind=False."""
    _login(client)
    resp = _run_positions(client, _arrival_ctx(_now() - timedelta(minutes=5)))
    nc = resp.get_json()['positions'][0]['telemetry']['next_control']
    assert nc['behind'] is False
    assert nc['required_mph'] is not None and nc['required_mph'] > 0


def test_next_control_required_speed_behind_is_null(client):
    """Past the plan's arrival time → required_mph null + behind flag (em-dash),
    never a negative number or a crash."""
    _login(client)
    resp = _run_positions(client, _arrival_ctx(_now() - timedelta(minutes=60)))
    nc = resp.get_json()['positions'][0]['telemetry']['next_control']
    assert nc['required_mph'] is None
    assert nc['behind'] is True


def test_time_banked_cutoff_and_plan_present(client):
    """Both banked metrics are surfaced: vs cutoff (top-level) and vs plan
    (top-level + plan.banked_min), alongside the ahead/behind badge."""
    _login(client)
    resp = _run_positions(client, _arrival_ctx(_now() - timedelta(minutes=5)))
    t = resp.get_json()['positions'][0]['telemetry']
    assert t['time_banked_cutoff_min'] is not None
    assert t['time_banked_plan_min'] == t['plan']['delta_min']
    assert t['plan']['banked_min'] == t['plan']['delta_min']


def test_time_banked_cutoff_null_without_cutoff(client):
    """No cutoff on the ride → banked-vs-cutoff is null (plan banked still present)."""
    _login(client)
    ctx = _arrival_ctx(_now() - timedelta(minutes=5), plan_cutoff_hours=None)
    t = _run_positions(client, ctx).get_json()['positions'][0]['telemetry']
    assert t['time_banked_cutoff_min'] is None
    assert t['time_banked_plan_min'] is not None


# ── Item 4: route-ahead chart data ─────────────────────────────────────────

def test_positions_includes_chart_data(client):
    """The positions response carries a top-level chart_data block with aligned
    (equal-length) series, and marks are drawn from telemetry.now.distance_mi."""
    _login(client)
    chart = {'labels': [0.0, 0.5, 1.1], 'elevation_ft': [30, 60, 90],
             'headwind_mph': [5.0, 4.0, 3.0], 'temperature_f': [60.0, 61.0, 62.0]}
    ctx = _arrival_ctx(_now() - timedelta(minutes=5), chart_data=chart)
    body = _run_positions(client, ctx).get_json()
    cd = body['chart_data']
    assert cd is not None
    n = len(cd['labels'])
    assert n == len(cd['elevation_ft']) == len(cd['headwind_mph']) == len(cd['temperature_f'])
    # The rider is on-route, so their current mileage is available to mark.
    assert body['positions'][0]['telemetry']['now']['distance_mi'] is not None


def test_positions_chart_data_null_without_route(client):
    """A ride with no route yields chart_data == null and no telemetry error."""
    _login(client)
    ctx = dict(_arrival_ctx(_now() - timedelta(minutes=5)), has_route=False, chart_data=None)
    body = _run_positions(client, ctx).get_json()
    assert body['chart_data'] is None
    assert body['positions'][0]['telemetry']['on_route'] is None


def test_build_live_chart_data_from_weather_pipeline():
    """chart_data is built from the STORED weather (no live fetch — TA-237) through the
    SAME time-aware pipeline as the weather page — arrival-hour selection — NOT a
    current-conditions sampler. Returns aligned labels/elevation/headwind/temperature."""
    from routes.live import _build_live_chart_data
    # 60km RWGPS-format route (x=lng, y=lat, d=dist_m, e=elev_m) for elevation.
    track = [{'x': -122.0 + i * 0.05, 'y': 37.0, 'd': i * 15000, 'e': 100 + i * 10}
             for i in range(5)]
    # Stored sample points the cron persisted (aligned to the forecasts below).
    samples = [{'lat': 37.0, 'lng': -122.0 + i * 0.05, 'distance_m': i * 15000}
               for i in range(5)]
    start = datetime(2026, 6, 27, 7, 0)
    times = [(start + timedelta(hours=h)).strftime('%Y-%m-%dT%H:00') for h in range(4)]
    forecast = {'utc_offset_seconds': 0, 'hourly': {
        'time': times, 'temperature_2m': [15.0, 16.0, 17.0, 18.0],
        'wind_speed_10m': [10, 10, 10, 10], 'wind_gusts_10m': [20, 21, 22, 23],
        'wind_direction_10m': [90, 90, 90, 90]}}
    plan_stops = [{'distance_miles': 0, 'cum_time_min': 0},
                  {'distance_miles': 40, 'cum_time_min': 120}]
    cd = _build_live_chart_data(samples, [forecast] * 5, track, plan_stops, start)
    n = len(cd['labels'])
    assert n >= 2
    assert n == len(cd['elevation_ft']) == len(cd['headwind_mph']) == len(cd['temperature_f'])
    assert n == len(cd['wind_gust_mph'])
    assert cd['wind_gust_mph'][0] > cd['headwind_mph'][0]
    # Arrival-hour selection picks the first forecast hour at the start point: 15C → 59F.
    assert cd['temperature_f'][0] == pytest.approx(59.0, abs=0.2)
    assert cd['headwind_mph'][0] > 0    # east wind on an eastbound leg → headwind


def test_build_live_chart_data_none_without_samples():
    from routes.live import _build_live_chart_data
    assert _build_live_chart_data([], [], [], None, None) is None


def test_build_live_chart_data_none_without_forecast():
    """No stored forecast → None so the caller hides the charts (graceful)."""
    from routes.live import _build_live_chart_data
    track = [{'x': -122.0 + i * 0.05, 'y': 37.0, 'd': i * 15000, 'e': 100} for i in range(5)]
    samples = [{'lat': 37.0, 'lng': -122.0 + i * 0.05, 'distance_m': i * 15000} for i in range(5)]
    assert _build_live_chart_data(samples, [], track, [], None) is None


def test_positions_wind_shows_mph_type_and_arrow(client):
    """Wind metric reads '<arrow> <mph> mph <head|cross|tail>', not a bare label."""
    _login(client)
    now = _now()
    ctx = dict(_FAKE_CTX, wind_by_dist=[
        {'dist_m': 0, 'headwind_kmh': 16, 'crosswind_kmh': 0},        # done: headwind
        {'dist_m': 1778, 'headwind_kmh': 2, 'crosswind_kmh': 15},     # ahead: crosswind
    ])
    row = {'rider_id': 7, 'name': 'Asha Rider', 'lat': 37.0, 'lng': -121.99,
           'recorded_at': now - timedelta(minutes=2), 'status': 'GOING',
           'speed': 6.0, 'heart_rate': None, 'power': None, 'cadence': None}
    history = [
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': now - timedelta(minutes=30), 'speed': 5.0},
        {'lat': 37.0, 'lng': -121.99, 'recorded_at': now - timedelta(minutes=2), 'speed': 6.0},
    ]
    with patch('routes.live.get_latest_positions_for_ride', return_value=[row]), \
         patch('routes.live._ride_live_context', return_value=ctx), \
         patch('routes.live.get_positions_for_rider_since', return_value=history):
        resp = client.get('/api/live/positions?ride_id=5')
    t = resp.get_json()['positions'][0]['telemetry']
    done = t['now']['headwind_done_label']
    assert done.startswith('↓') and 'mph' in done and done.endswith('head')
    assert t['now']['headwind_done_mph'] == pytest.approx(9.9, abs=0.2)
    ahead = t['remaining']['headwind_ahead_label']
    assert ahead[0] in '↑↗→↘↓↙←↖' and ahead.endswith('cross')


def test_positions_moving_time_not_more_than_elapsed(client):
    """Pre-start Garmin recording must not push moving time above elapsed."""
    _login(client)
    now = _now()
    ctx = dict(_FAKE_CTX,
               ride_start_iso=(now - timedelta(minutes=60)).isoformat())   # 60 min in
    # Continuous riding from 90 min ago (30 min BEFORE the official start).
    history = [{'lat': 37.0, 'lng': -122.0 + i * 0.001,
                'recorded_at': now - timedelta(minutes=90 - 5 * i), 'speed': 5.0}
               for i in range(19)]                                          # 90→0 min
    row = {'rider_id': 7, 'name': 'Sreeram', 'lat': 37.0, 'lng': -121.91,
           'recorded_at': now, 'status': 'GOING',
           'speed': 5.0, 'heart_rate': None, 'power': None, 'cadence': None}
    with patch('routes.live.get_latest_positions_for_ride', return_value=[row]), \
         patch('routes.live._ride_live_context', return_value=ctx), \
         patch('routes.live.get_positions_for_rider_since', return_value=history):
        resp = client.get('/api/live/positions?ride_id=5')
    n = resp.get_json()['positions'][0]['telemetry']['now']
    assert n['moving_min'] <= n['elapsed_min']          # the bug: was ~90 > 60
    assert n['moving_min'] < 90                          # pre-start time excluded


def test_positions_moving_plus_stopped_equals_elapsed(client):
    """moving + stopped must reconcile to elapsed, even across a data gap that
    moving_stopped() would otherwise drop from both totals."""
    _login(client)
    now = _now()
    ctx = dict(_FAKE_CTX,
               ride_start_iso=(now - timedelta(minutes=60)).isoformat())   # 60 min in
    # 10 min of riding, a 25-min data gap (dropped by moving_stopped), then 25 more.
    mins = [60, 55, 50, 25, 20, 15, 10, 5, 0]
    history = [{'lat': 37.0, 'lng': -122.0 + i * 0.001,
                'recorded_at': now - timedelta(minutes=m), 'speed': 5.0}
               for i, m in enumerate(mins)]
    row = {'rider_id': 7, 'name': 'Asha Rider', 'lat': 37.0, 'lng': -121.91,
           'recorded_at': now, 'status': 'GOING',
           'speed': 5.0, 'heart_rate': None, 'power': None, 'cadence': None}
    with patch('routes.live.get_latest_positions_for_ride', return_value=[row]), \
         patch('routes.live._ride_live_context', return_value=ctx), \
         patch('routes.live.get_positions_for_rider_since', return_value=history):
        resp = client.get('/api/live/positions?ride_id=5')
    n = resp.get_json()['positions'][0]['telemetry']['now']
    assert n['moving_min'] + n['stopped_min'] == pytest.approx(n['elapsed_min'], abs=1)
    assert n['stopped_min'] > 0          # the dropped gap is absorbed into stopped


def test_positions_time_left_is_limit_minus_elapsed(client):
    """Time left = overall brevet time limit − elapsed (e.g. 40h for a 600)."""
    _login(client)
    now = _now()
    ctx = dict(_FAKE_CTX,
               time_limit_min=2400,                                  # 40h (a 600)
               ride_start_iso=(now - timedelta(minutes=120)).isoformat())  # 2h in
    row = {'rider_id': 7, 'name': 'Asha Rider', 'lat': 37.0, 'lng': -121.99,
           'recorded_at': now - timedelta(minutes=2), 'status': 'GOING',
           'speed': 6.0, 'heart_rate': None, 'power': None, 'cadence': None}
    with patch('routes.live.get_latest_positions_for_ride', return_value=[row]), \
         patch('routes.live._ride_live_context', return_value=ctx), \
         patch('routes.live.get_positions_for_rider_since', return_value=[]):
        resp = client.get('/api/live/positions?ride_id=5')
    t = resp.get_json()['positions'][0]['telemetry']
    assert t['remaining']['time_left_min'] == pytest.approx(2280, abs=2)   # 2400 − 120


def test_ride_context_time_limit_from_event_and_distance(app):
    """time_limit_min prefers ride.time_limit_hours; falls back to the ACP
    standard for the distance (600 km → 40h)."""
    cache.clear()
    explicit = {'id': 6601, 'name': 'Has limit', 'date': '2026-06-27',
                'start_time': '06:00', 'time_limit_hours': 40, 'distance_km': 600,
                'ride_plan_id': None, 'rwgps_url': None, 'rwgps_url_team': None}
    fallback = {'id': 6602, 'name': 'No limit', 'date': '2026-06-27',
                'start_time': '06:00', 'time_limit_hours': None, 'distance_km': 600,
                'ride_plan_id': None, 'rwgps_url': None, 'rwgps_url_team': None}
    with app.app_context():
        with patch('routes.live.get_ride_by_id', return_value=explicit):
            assert live_module._ride_live_context(6601)['time_limit_min'] == 2400
        cache.clear()
        with patch('routes.live.get_ride_by_id', return_value=fallback):
            assert live_module._ride_live_context(6602)['time_limit_min'] == 2400
    cache.clear()


def test_positions_without_route_still_shows_source_metrics(client):
    _login(client)
    row = {'rider_id': 7, 'name': 'R', 'lat': 37.0, 'lng': -122.0,
           'recorded_at': _now(), 'status': 'GOING',
           'speed': 5.0, 'heart_rate': None, 'power': None, 'cadence': None}
    no_route_ctx = dict(_FAKE_CTX, has_route=False)
    with patch('routes.live.get_latest_positions_for_ride', return_value=[row]), \
         patch('routes.live._ride_live_context', return_value=no_route_ctx), \
         patch('routes.live.get_positions_for_rider_since', return_value=[]):
        resp = client.get('/api/live/positions?ride_id=5')
    assert resp.status_code == 200
    t = resp.get_json()['positions'][0]['telemetry']
    assert t['on_route'] is None          # no route to compare against
    assert t['remaining'] is None          # route-relative metrics omitted
    assert t['now']['speed_mph'] is not None
    assert t['now']['activity'] == 'cycling'   # 5 m/s


def test_positions_off_route_rider_shown_without_route_metrics(client):
    """A rider far from the route is still shown (marker), but with no course telemetry."""
    _login(client)
    # _FAKE_CTX track is around lat 37.0; put the rider ~22 km north.
    row = {'rider_id': 7, 'name': 'Off Route', 'lat': 37.2, 'lng': -121.99,
           'recorded_at': _now(), 'status': 'GOING',
           'speed': 5.0, 'heart_rate': None, 'power': None, 'cadence': None}
    with patch('routes.live.get_latest_positions_for_ride', return_value=[row]), \
         patch('routes.live._ride_live_context', return_value=_FAKE_CTX), \
         patch('routes.live.get_positions_for_rider_since', return_value=[]):
        resp = client.get('/api/live/positions?ride_id=5')
    assert resp.status_code == 200
    positions = resp.get_json()['positions']
    assert len(positions) == 1                       # shown, not hidden
    t = positions[0]['telemetry']
    assert t['on_route'] is False
    assert t['remaining'] is None                    # course telemetry suppressed
    assert 'distance_mi' not in t['now']
    assert t['now']['activity'] == 'cycling'         # speed/activity still shown


def test_positions_off_route_bounce_with_history_stays_shown(client):
    """Off-course latest fix freezes metrics at the last valid route point."""
    _login(client)
    now = _now()
    row = {'rider_id': 7, 'name': 'Bounce', 'lat': 37.2, 'lng': -121.99,   # current fix off-route
           'recorded_at': now, 'status': 'GOING',
           'speed': 5.0, 'heart_rate': None, 'power': None, 'cadence': None}
    # Recent history is ON the route (near the _FAKE_CTX track at lat 37.0); the
    # newest fix (matching `row`) is the off-route bounce at lat 37.2.
    history = [
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': now - timedelta(minutes=10), 'speed': 5.0},
        {'lat': 37.0, 'lng': -121.99, 'recorded_at': now - timedelta(minutes=5), 'speed': 5.0},
        {'lat': 37.2, 'lng': -121.99, 'recorded_at': now, 'speed': 5.0},
    ]
    with patch('routes.live.get_latest_positions_for_ride', return_value=[row]), \
         patch('routes.live._ride_live_context', return_value=_FAKE_CTX), \
         patch('routes.live.get_positions_for_rider_since', return_value=history):
        resp = client.get('/api/live/positions?ride_id=5')
    assert resp.status_code == 200
    positions = resp.get_json()['positions']
    assert len(positions) == 1                       # not hidden — has on-route breadcrumb
    telemetry = positions[0]['telemetry']
    assert telemetry['on_route'] is False
    assert telemetry['now']['distance_mi'] == 0.6
    assert telemetry['off_course_since_mi'] == 0.6
    assert telemetry['remaining']['distance_mi'] == 0.6
    assert positions[0]['trail']                     # on-route trail present


def test_positions_future_ride_elapsed_is_none(client):
    """For a ride that hasn't started, elapsed (and plan) are not computed."""
    _login(client)
    future = dict(_FAKE_CTX,
                  ride_start_iso=(_now() + timedelta(days=3)).isoformat())
    row = {'rider_id': 7, 'name': 'R', 'lat': 37.0, 'lng': -121.99,
           'recorded_at': _now(), 'status': 'GOING',
           'speed': None, 'heart_rate': None, 'power': None, 'cadence': None}
    with patch('routes.live.get_latest_positions_for_ride', return_value=[row]), \
         patch('routes.live._ride_live_context', return_value=future), \
         patch('routes.live.get_positions_for_rider_since', return_value=[]):
        resp = client.get('/api/live/positions?ride_id=5')
    assert resp.status_code == 200
    t = resp.get_json()['positions'][0]['telemetry']
    assert t['now']['elapsed_min'] is None
    assert t['plan'] is None        # plan delta needs elapsed


def _telemetry_with_start(client, ride_start_dt):
    """Helper: run the telemetry endpoint with a controlled ride start time."""
    ctx = dict(_FAKE_CTX, ride_start_iso=ride_start_dt.isoformat())
    now = _now()
    row = {'rider_id': 7, 'name': 'R', 'lat': 37.0, 'lng': -121.99,
           'recorded_at': now - timedelta(minutes=1), 'status': 'GOING',
           'speed': None, 'heart_rate': None, 'power': None, 'cadence': None}
    with patch('routes.live.get_latest_positions_for_ride', return_value=[row]), \
         patch('routes.live._ride_live_context', return_value=ctx), \
         patch('routes.live.get_positions_for_rider_since', return_value=[]):
        resp = client.get('/api/live/positions?ride_id=5')
    return resp.get_json()['positions'][0]['telemetry']['plan']


def test_plan_status_ahead(client):
    _login(client)
    # ~5 min elapsed; plan expects ~15 min at the rider's distance → ahead.
    plan = _telemetry_with_start(client, _now() - timedelta(minutes=5))
    assert plan['status'] == 'ahead'
    assert plan['delta_min'] > 2


def test_plan_status_behind(client):
    _login(client)
    # ~90 min elapsed; plan expects ~15 min → well behind.
    plan = _telemetry_with_start(client, _now() - timedelta(minutes=90))
    assert plan['status'] == 'behind'
    assert plan['delta_min'] < -2


# ── _ride_live_context builds ascent + wind (the tricky uncached path) ──────

def test_ride_context_builds_ascent_and_wind(app):
    cache.clear()
    ride = {'id': 8888, 'name': 'Ctx 200k', 'date': '2026-06-27',
            'plan_start_time': '07:00', 'ride_plan_id': None,
            'rwgps_url': 'https://ridewithgps.com/routes/55780124', 'rwgps_url_team': None}
    # Distances span 60 km so sample_track_points (50 km interval) yields ≥2 samples.
    route = {'track_points': [
        {'x': -122.0, 'y': 37.0, 'd': 0, 'e': 10},
        {'x': -121.7, 'y': 37.0, 'd': 30000, 'e': 60},     # +50m climb
        {'x': -121.4, 'y': 37.0, 'd': 60000, 'e': 40},     # descent (no added climb)
    ]}
    now = _now()
    hours = [(now - timedelta(hours=1)), now, (now + timedelta(hours=1))]
    times = [h.replace(tzinfo=None).strftime('%Y-%m-%dT%H:00') for h in hours]
    # East wind (from 90°) on an eastbound route → headwind.
    forecast = {'utc_offset_seconds': 0,
                'hourly': {'time': times,
                           'wind_speed_10m': [10, 10, 10],
                           'wind_direction_10m': [90, 90, 90]}}
    # Stored sample points (eastbound) the fetch-route-weather cron persisted (TA-237).
    samples = [{'lat': 37.0, 'lng': -122.0, 'distance_m': 0},
               {'lat': 37.0, 'lng': -121.7, 'distance_m': 30000}]
    with app.app_context():
        with patch('routes.live.get_ride_by_id', return_value=ride), \
             patch('routes.live.fetch_route', return_value=route), \
             patch('routes.live.load_stored_route_weather',
                   return_value=([forecast, forecast], samples)):
            ctx = live_module._ride_live_context(8888)
    cache.clear()
    assert ctx['has_route'] is True
    assert ctx['total_ascent_ft'] and ctx['total_ascent_ft'] > 0   # ~50m → ~164 ft
    assert ctx['wind_by_dist'] is not None
    assert ctx['wind_by_dist'][0]['headwind_kmh'] > 0   # headwind, not tailwind
    # Route-ahead charts are sourced from the shared weather pipeline (item 5): the
    # same fetch_route_weather mock feeds them, so chart_data is built here too.
    assert ctx['chart_data'] is not None
    assert len(ctx['chart_data']['labels']) >= 2


# ── Ride start is Pacific wall-clock, converted to UTC (not treated as UTC) ──

def test_ride_context_start_time_is_pacific_summer(app):
    """A 6 AM start on a summer (PDT, UTC-7) ride resolves to 13:00 UTC."""
    cache.clear()
    ride = {'id': 5551, 'name': 'PDT 200k', 'date': '2026-06-27',
            'start_time': '06:00', 'plan_start_time': None, 'ride_plan_id': None,
            'rwgps_url': None, 'rwgps_url_team': None}
    with app.app_context():
        with patch('routes.live.get_ride_by_id', return_value=ride):
            ctx = live_module._ride_live_context(5551)
    cache.clear()
    assert ctx['ride_start_iso'] == '2026-06-27T13:00:00+00:00'


def test_ride_context_start_time_is_pacific_winter(app):
    """A 6 AM start on a winter (PST, UTC-8) ride resolves to 14:00 UTC."""
    cache.clear()
    ride = {'id': 5552, 'name': 'PST 200k', 'date': '2026-01-10',
            'start_time': '06:00', 'plan_start_time': None, 'ride_plan_id': None,
            'rwgps_url': None, 'rwgps_url_team': None}
    with app.app_context():
        with patch('routes.live.get_ride_by_id', return_value=ride):
            ctx = live_module._ride_live_context(5552)
    cache.clear()
    assert ctx['ride_start_iso'] == '2026-01-10T14:00:00+00:00'


def test_scheduled_ride_start_overrides_plan_default(app):
    """A reusable plan's default start must not add phantom stopped time."""
    cache.clear()
    ride = {'id': 5553, 'name': 'Evening 200k', 'date': '2026-07-30',
            'start_time': '21:57', 'plan_start_time': '21:00',
            'ride_plan_id': 64, 'rwgps_url': None, 'rwgps_url_team': None}
    with app.app_context():
        with patch('routes.live.get_ride_by_id', return_value=ride):
            ctx = live_module._ride_live_context(5553)
    cache.clear()
    # July is PDT (UTC-7): 9:57 PM local is 04:57 UTC the following day.
    assert ctx['ride_start_iso'] == '2026-07-31T04:57:00+00:00'
    assert live_module._ride_start_local(ride) == datetime(2026, 7, 30, 21, 57)


# ── Per-ride context is cached (no RWGPS re-fetch per poll) ─────────────────

def test_ride_context_cached_across_polls(client):
    cache.clear()
    _login(client)
    RIDE_ID = 7777
    ride = {'id': RIDE_ID, 'name': 'Cached 200k', 'date': '2026-06-27',
            'plan_start_time': '07:00', 'ride_plan_id': 99,
            'rwgps_url': 'https://ridewithgps.com/routes/55780124', 'rwgps_url_team': None}
    route = {'track_points': [
        {'x': -122.00, 'y': 37.0, 'd': 0, 'e': 10},
        {'x': -121.99, 'y': 37.0, 'd': 889, 'e': 20},
        {'x': -121.98, 'y': 37.0, 'd': 1778, 'e': 25},
    ]}
    overview = [
        {'lng': point['x'], 'lat': point['y'], 'dist_m': point['d'], 'e_m': point['e']}
        for point in route['track_points']
    ]
    row = {'rider_id': 7, 'name': 'R', 'lat': 37.0, 'lng': -121.99,
           'recorded_at': _now() - timedelta(minutes=1), 'status': 'GOING',
           'speed': None, 'heart_rate': None, 'power': None, 'cadence': None}
    with patch('routes.live.get_latest_positions_for_ride', return_value=[row]), \
         patch('routes.live.get_ride_by_id', return_value=ride), \
         patch('routes.live._radial_overview_track', return_value=overview) as mock_overview, \
         patch('routes.live.get_ride_plan_stops', return_value=[]), \
         patch('routes.live.load_stored_route_weather', return_value=(None, None)), \
         patch('routes.live.get_positions_for_rider_since', return_value=[]):
        r1 = client.get('/api/live/positions?ride_id=%d' % RIDE_ID)
        r2 = client.get('/api/live/positions?ride_id=%d' % RIDE_ID)
    assert r1.status_code == 200 and r2.status_code == 200
    assert mock_overview.call_count == 1  # full-course context built once across 2 polls
    cache.clear()
