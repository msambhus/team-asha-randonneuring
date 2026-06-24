"""Integration tests for live telemetry: Garmin field capture, history-append
cron, beacon speed, and the /api/live/positions telemetry block + caching.
All external HTTP mocked; models patched so no DB is needed.
"""
import json
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
                'garmin_session_token': 't1'}]
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
        resp = client.post('/api/live/beacon', json={'lat': 37.8, 'lng': -122.2, 'speed': 6.1})
    assert resp.status_code == 200
    assert captured['speed'] == 6.1
    assert captured['source'] == 'beacon'


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
    assert t['now']['heart_rate'] == 140
    assert t['remaining']['toughness'] is not None
    assert t['detailed_after_ride'] is True
    assert 'status' in (t['plan'] or {})
    # On-route breadcrumb trail of where the rider has ridden.
    assert pos['trail'] and len(pos['trail']) >= 1
    assert pos['trail'][0] == [-122.0, 37.0]   # [lng,lat], on-route history point


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
    """A momentary off-route fix doesn't hide a rider who has on-route history."""
    _login(client)
    now = _now()
    row = {'rider_id': 7, 'name': 'Bounce', 'lat': 37.2, 'lng': -121.99,   # current fix off-route
           'recorded_at': now, 'status': 'GOING',
           'speed': 5.0, 'heart_rate': None, 'power': None, 'cadence': None}
    # Recent history is ON the route (near the _FAKE_CTX track at lat 37.0).
    history = [
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': now - timedelta(minutes=10), 'speed': 5.0},
        {'lat': 37.0, 'lng': -121.99, 'recorded_at': now - timedelta(minutes=5), 'speed': 5.0},
    ]
    with patch('routes.live.get_latest_positions_for_ride', return_value=[row]), \
         patch('routes.live._ride_live_context', return_value=_FAKE_CTX), \
         patch('routes.live.get_positions_for_rider_since', return_value=history):
        resp = client.get('/api/live/positions?ride_id=5')
    assert resp.status_code == 200
    positions = resp.get_json()['positions']
    assert len(positions) == 1                       # not hidden — has on-route breadcrumb
    assert positions[0]['telemetry']['on_route'] is False
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
    with app.app_context():
        with patch('routes.live.get_ride_by_id', return_value=ride), \
             patch('routes.live.fetch_route', return_value=route), \
             patch('routes.live.fetch_route_weather', return_value=[forecast, forecast]):
            ctx = live_module._ride_live_context(8888)
    cache.clear()
    assert ctx['has_route'] is True
    assert ctx['total_ascent_ft'] and ctx['total_ascent_ft'] > 0   # ~50m → ~164 ft
    assert ctx['wind_by_dist'] is not None
    assert ctx['wind_by_dist'][0]['headwind_kmh'] > 0   # headwind, not tailwind


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
    row = {'rider_id': 7, 'name': 'R', 'lat': 37.0, 'lng': -121.99,
           'recorded_at': _now() - timedelta(minutes=1), 'status': 'GOING',
           'speed': None, 'heart_rate': None, 'power': None, 'cadence': None}
    with patch('routes.live.get_latest_positions_for_ride', return_value=[row]), \
         patch('routes.live.get_ride_by_id', return_value=ride), \
         patch('routes.live.fetch_route', return_value=route) as mock_fetch, \
         patch('routes.live.get_ride_plan_stops', return_value=[]), \
         patch('routes.live.fetch_route_weather', return_value=[]), \
         patch('routes.live.get_positions_for_rider_since', return_value=[]):
        r1 = client.get('/api/live/positions?ride_id=%d' % RIDE_ID)
        r2 = client.get('/api/live/positions?ride_id=%d' % RIDE_ID)
    assert r1.status_code == 200 and r2.status_code == 200
    assert mock_fetch.call_count == 1   # context cached — RWGPS fetched once across 2 polls
    cache.clear()
