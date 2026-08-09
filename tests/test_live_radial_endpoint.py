"""Team Asha public Radial roster poll (GET /ride/<id>/live/roster.json).

Mirrors the live-metrics test pattern: `client` fixture, patch the route's model +
context helpers, never a real DB. Proves the public roster is guest-reachable when
the ride is opted public, is privacy-shaped (display_name + position + stats + key,
no rider_id / email / google_id), and that the existing member /api/live/positions
STILL carries rider_id (the two-tier contract).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from cache import cache
from routes.live import _telemetry_history_since


@pytest.fixture(autouse=True)
def _clear_live_roster_cache(request):
    # Pure helper tests in this module do not construct the Flask app.
    if 'client' not in request.fixturenames:
        yield
        return
    client = request.getfixturevalue('client')
    with client.application.app_context():
        cache.clear()
    yield
    with client.application.app_context():
        cache.clear()


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def _now():
    return datetime.now(timezone.utc)


_FAKE_CTX = {
    'has_route': True,
    'track': [
        {'lat': 37.0, 'lng': -122.00, 'dist_m': 0.0, 'e_m': 0.0},
        {'lat': 37.0, 'lng': -121.99, 'dist_m': 889.0, 'e_m': 30.0},
        {'lat': 37.0, 'lng': -121.98, 'dist_m': 1778.0, 'e_m': 60.0},
    ],
    'cum_ascent_ft': [0, 100, 200],
    'total_dist_m': 1778.0,
    'total_ascent_ft': 200,
    'plan_stops': [{'distance_miles': 0, 'cum_time_min': 0},
                   {'distance_miles': 1.1, 'cum_time_min': 30}],
    'plan_total_mi': 1.1,
    'plan_cutoff_hours': 1.0,
    'wind_by_dist': None,
    'chart_data': {
        'labels': [0.0, 1.1],
        'elevation_ft': [0, 200],
        'headwind_mph': [4.0, -2.0],
        'temperature_f': [58.0, 61.0],
    },
    'ride_start_iso': datetime(2026, 6, 23, 7, 0, tzinfo=timezone.utc).isoformat(),
    'time_limit_min': 60,
}

_PUBLIC_LIVE_RIDE = {'id': 5, 'name': 'Public Loop', 'is_public_live': True,
                     'distance_km': 200, 'date': '2026-06-23'}
_PRIVATE_RIDE = {'id': 5, 'name': 'Private', 'is_public_live': False,
                 'distance_km': 200, 'date': '2026-06-23'}


def _row(rider_id=7, name='Asha Rider'):
    return {'rider_id': rider_id, 'name': name, 'lat': 37.0, 'lng': -121.99,
            'recorded_at': _now() - timedelta(minutes=2), 'status': 'REGISTERED',
            'speed': 6.0, 'heart_rate': 140, 'power': None, 'cadence': None,
            'source': 'garmin'}


def _history():
    now = _now()
    return [
        {'lat': 37.0, 'lng': -122.0, 'recorded_at': now - timedelta(minutes=30), 'speed': 5.0},
        {'lat': 37.0, 'lng': -121.99, 'recorded_at': now - timedelta(minutes=2), 'speed': 6.0},
    ]


def test_multiday_telemetry_history_starts_at_ride_start():
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    ride_start = now - timedelta(hours=53)

    since = _telemetry_history_since(
        {'ride_start_iso': ride_start.isoformat()}, now)

    assert since == ride_start


def test_telemetry_history_falls_back_to_24_hours_without_ride_start():
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    assert _telemetry_history_since({}, now) == now - timedelta(hours=24)


def test_telemetry_history_is_bounded_by_position_retention():
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    old_start = now - timedelta(days=12)

    since = _telemetry_history_since(
        {'ride_start_iso': old_start.isoformat()}, now)

    assert since == now - timedelta(days=7)


def test_guest_reads_public_live_roster_200(client):
    with patch('routes.live.get_ride_by_id', return_value=_PUBLIC_LIVE_RIDE), \
         patch('routes.live._ride_live_context', return_value=_FAKE_CTX), \
         patch('routes.live.get_latest_positions_for_ride', return_value=[_row()]), \
         patch('routes.live.get_positions_for_rider_since', return_value=_history()):
        resp = client.get('/ride/5/live/roster.json')   # no login → guest
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ride_id'] == 5 and 'server_time' in data
    assert data['chart_data']['headwind_mph'] == [4.0, -2.0]
    assert len(data['roster']) == 1
    # Privacy-reduced name: first name + last initial, never a full surname.
    assert data['roster'][0]['display_name'] == 'Asha R.'
    # Public web and native maps share the same bounded, road-following trace.
    assert data['roster'][0]['trail'][0] == [-122.0, 37.0]
    assert len(data['roster'][0]['trail']) >= 2
    assert resp.headers['Cache-Control'] == (
        'public, s-maxage=15, stale-while-revalidate=30')


def test_public_base_roster_reads_persistent_snapshot_without_history_replay(client):
    computed = _now() - timedelta(seconds=8)
    snapshot = {
        'computed_at': computed,
        'source_recorded_at': computed - timedelta(seconds=2),
        'payload': {'public': {
            'ride_id': 5, 'roster': [{'key': 'opaque', 'display_name': 'Asha R.'}],
            'server_time': computed.isoformat(), 'stale_after_minutes': 10,
            'poll_seconds': 30, 'chart_data': None,
        }},
    }
    with patch('routes.live.get_ride_by_id', return_value=_PUBLIC_LIVE_RIDE), \
         patch('routes.live._shared_live_snapshot', return_value=snapshot), \
         patch('routes.live._ride_live_context', return_value=_FAKE_CTX), \
         patch('routes.live.get_positions_for_rider_since') as history:
        resp = client.get('/ride/5/live/roster.json?plan_id=base')

    assert resp.status_code == 200
    history.assert_not_called()
    body = resp.get_json()
    assert body['roster'][0]['display_name'] == 'Asha R.'
    assert body['snapshot_computed_at'] == computed.isoformat()
    assert 'rider_id' not in resp.get_data(as_text=True)


def test_native_base_positions_reads_same_persistent_snapshot(client):
    _login(client)
    computed = _now() - timedelta(seconds=8)
    snapshot = {
        'computed_at': computed,
        'source_recorded_at': computed - timedelta(seconds=2),
        'payload': {'mobile': {
            'ride_id': 5,
            'positions': [{'rider_id': 7, 'name': 'Asha Rider'}],
            'server_time': computed.isoformat(), 'stale_after_minutes': 10,
            'chart_data': None, 'elevation_profile': None,
            'upcoming_controls': [], 'plan_snapshot': None,
        }},
    }
    with patch('routes.live._shared_live_snapshot', return_value=snapshot), \
         patch('routes.live._ride_live_context', return_value=_FAKE_CTX), \
         patch('routes.live.get_positions_for_rider_since') as history:
        resp = client.get('/api/live/positions?ride_id=5')

    assert resp.status_code == 200
    history.assert_not_called()
    body = resp.get_json()
    assert body['positions'][0]['rider_id'] == 7
    assert body['snapshot_computed_at'] == computed.isoformat()
    assert resp.headers['Cache-Control'] == 'private, no-store'


def test_public_roster_reuses_composed_response_within_fresh_window(client):
    with patch('routes.live.get_ride_by_id', return_value=_PUBLIC_LIVE_RIDE), \
         patch('routes.live._ride_live_context', return_value=_FAKE_CTX) as context, \
         patch('routes.live.get_latest_positions_for_ride', return_value=[_row()]) as positions, \
         patch('routes.live.get_positions_for_rider_since', return_value=_history()):
        first = client.get('/ride/5/live/roster.json?plan_id=base')
        second = client.get('/ride/5/live/roster.json?plan_id=base')

    assert first.status_code == 200 and second.status_code == 200
    assert second.get_json() == first.get_json()
    assert context.call_count == 1
    assert positions.call_count == 1


def test_public_roster_cache_is_partitioned_by_plan(client):
    with patch('routes.live.get_ride_by_id', return_value=_PUBLIC_LIVE_RIDE), \
         patch('routes.live._ride_live_context', return_value=_FAKE_CTX) as context, \
         patch('routes.live.get_latest_positions_for_ride', return_value=[_row()]), \
         patch('routes.live.get_positions_for_rider_since', return_value=_history()):
        client.get('/ride/5/live/roster.json?plan_id=base')
        client.get('/ride/5/live/roster.json?plan_id=42')

    assert context.call_count == 2


def test_public_roster_is_pii_safe(client):
    with patch('routes.live.get_ride_by_id', return_value=_PUBLIC_LIVE_RIDE), \
         patch('routes.live._ride_live_context', return_value=_FAKE_CTX), \
         patch('routes.live.get_latest_positions_for_ride', return_value=[_row()]), \
         patch('routes.live.get_positions_for_rider_since', return_value=_history()):
        resp = client.get('/ride/5/live/roster.json')
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    for leaked in ('rider_id', 'email', 'google_id'):
        assert leaked not in body
    row = resp.get_json()['roster'][0]
    assert 'key' in row and len(row['key']) == 12
    assert row['route_position_mi'] is not None   # on-route position computed
    # Rich telemetry is restored without weakening the public privacy contract.
    assert row['elapsed_min'] is not None
    assert row['moving_min'] is not None
    assert row['stopped_min'] is not None
    assert row['grade_pct'] is not None
    assert row['distance_left_mi'] is not None
    assert row['time_left_min'] is not None
    assert row['finish'] is not None


def test_public_roster_restores_headwind_context(client):
    ctx = dict(_FAKE_CTX)
    ctx['wind_by_dist'] = [
        {'dist_m': 0, 'headwind_kmh': 12, 'crosswind_kmh': 0},
        {'dist_m': 1778, 'headwind_kmh': 8, 'crosswind_kmh': 3},
    ]
    with patch('routes.live.get_ride_by_id', return_value=_PUBLIC_LIVE_RIDE), \
         patch('routes.live._ride_live_context', return_value=ctx), \
         patch('routes.live.get_latest_positions_for_ride',
               return_value=[_row()]), \
         patch('routes.live.get_positions_for_rider_since',
               return_value=_history()):
        resp = client.get('/ride/5/live/roster.json')
    row = resp.get_json()['roster'][0]
    assert 'head' in row['headwind_done_label']
    assert row['headwind_ahead_label'] is not None


def test_public_roster_includes_going_rider_without_location(client):
    registered = [
        {'rider_id': 7, 'name': 'Asha Rider', 'status': 'REGISTERED'},
        {'rider_id': 8, 'name': 'Bharadwaj Rao', 'status': 'REGISTERED'},
    ]
    with patch('routes.live.get_ride_by_id', return_value=_PUBLIC_LIVE_RIDE), \
         patch('routes.live._ride_live_context', return_value=_FAKE_CTX), \
         patch('routes.live.get_latest_positions_for_ride', return_value=[_row()]), \
         patch('routes.live.get_registered_riders_for_ride', return_value=registered), \
         patch('routes.live.get_positions_for_rider_since',
               side_effect=lambda rider_id, *_args, **_kwargs: _history() if rider_id == 7 else []):
        resp = client.get('/ride/5/live/roster.json')

    assert resp.status_code == 200
    roster = resp.get_json()['roster']
    assert len(roster) == 2
    bharadwaj = next(row for row in roster if row['display_name'] == 'Bharadwaj R.')
    assert bharadwaj['lat'] is None
    assert bharadwaj['lng'] is None
    assert bharadwaj['recorded_at'] is None
    assert 'rider_id' not in bharadwaj


def test_private_ride_guest_404s(client):
    with patch('routes.live.get_ride_by_id', return_value=_PRIVATE_RIDE):
        resp = client.get('/ride/5/live/roster.json')   # guest, not public-live, no invite
    assert resp.status_code == 404


def test_member_can_read_private_roster(client):
    _login(client, rider_id=7)
    with patch('routes.live.get_ride_by_id', return_value=_PRIVATE_RIDE), \
         patch('routes.live._ride_live_context', return_value=_FAKE_CTX), \
         patch('routes.live.get_latest_positions_for_ride', return_value=[_row()]), \
         patch('routes.live.get_positions_for_rider_since', return_value=_history()):
        resp = client.get('/ride/5/live/roster.json')
    assert resp.status_code == 200
    assert resp.headers['Cache-Control'] == 'private, no-store'


def test_unknown_ride_404s(client):
    with patch('routes.live.get_ride_by_id', return_value=None):
        resp = client.get('/ride/5/live/roster.json')
    assert resp.status_code == 404


def test_member_positions_still_carry_rider_id(client):
    """Two-tier contract: the authenticated /api/live/positions keeps rider_id while
    the public roster.json drops it."""
    _login(client, rider_id=7)
    with patch('routes.live.get_ride_by_id', return_value=_PUBLIC_LIVE_RIDE), \
         patch('routes.live._ride_live_context', return_value=_FAKE_CTX), \
         patch('routes.live.get_latest_positions_for_ride', return_value=[_row()]), \
         patch('routes.live.get_positions_for_rider_since', return_value=_history()):
        member = client.get('/api/live/positions?ride_id=5')
        public = client.get('/ride/5/live/roster.json')
    assert member.status_code == 200 and public.status_code == 200
    assert member.get_json()['positions'][0]['rider_id'] == 7
    assert 'rider_id' not in public.get_data(as_text=True)


def test_member_positions_include_plan_snapshot_and_going_non_sharer(client):
    _login(client, rider_id=7)
    plan_snapshot = {
        'name': 'Coulee Challenge', 'slug': 'coulee-challenge',
        'active_day': 2, 'day_distance_mi': 182, 'day_stops': [],
    }
    registered = [
        {'rider_id': 7, 'name': 'Asha Rider', 'status': 'REGISTERED'},
        {'rider_id': 8, 'name': 'Bharadwaj Rao', 'status': 'REGISTERED'},
    ]
    with patch('routes.live.get_ride_by_id', return_value=_PUBLIC_LIVE_RIDE), \
         patch('routes.live._ride_live_context', return_value=_FAKE_CTX), \
         patch('routes.live._mobile_live_plan_snapshot', return_value=plan_snapshot), \
         patch('routes.live.get_latest_positions_for_ride', return_value=[_row()]), \
         patch('routes.live.get_registered_riders_for_ride', return_value=registered), \
         patch('routes.live.get_positions_for_rider_since', return_value=_history()):
        resp = client.get('/api/live/positions?ride_id=5')

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['plan_snapshot']['active_day'] == 2
    assert len(data['positions']) == 2
    non_sharer = next(p for p in data['positions'] if p['rider_id'] == 8)
    assert non_sharer['not_sharing'] is True
    assert non_sharer['lat'] is None and non_sharer['lng'] is None
