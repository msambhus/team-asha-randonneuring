"""Tests for the mobile plan endpoint's new elevation-profile + pace-strategy
fields (PR #535 — mobile parity with the web rpv2 plan page, PR #534).

GET /api/ride/<id>/plan must additively serve, alongside the existing `stops`:
  - `elevation_profile` — built cache-only from get_route_elevation_track (migration
    052) then shared.live_radial.build_elevation_profile; {available:false} on a cache
    miss; NEVER a live RWGPS fetch on the request path (the TA-237 invariant);
  - `pace_stops_map` — comfort/standard/push per-pace stop lists from
    shared.strategies.compute_pace_strategies, so the client can swap the itinerary
    and reposition the overlay on pick without a refetch.

All model/service lookups are patched (no DB, no network), mirroring
tests/test_api_auth.py's plan tests. `fetch_route` is asserted NEVER called.
"""
from unittest.mock import patch

import auth as auth_mod


# ── fixtures shared with the api_auth plan tests (kept local to avoid coupling) ──

from datetime import date as _pl_date

_PLAN = {
    'id': 7, 'name': 'SFR 100', 'slug': 'sfr-100',
    'total_distance_miles': 100, 'total_elevation_ft': 5000, 'distance_km': 160,
    'cutoff_hours': 10, 'start_time': '06:00', 'overall_ft_per_mile': 50,
    'rwgps_url': 'https://ridewithgps.com/routes/123', 'rwgps_url_team': None,
}
_PLAN_STOPS = [
    {'stop_order': 1, 'location': 'Start', 'stop_type': 'start', 'distance_miles': 0,
     'segment_time_min': 0, 'stop_duration_min': 0, 'elevation_gain': 0,
     'stop_name': None, 'notes': None},
    {'stop_order': 2, 'location': 'Control 1', 'stop_type': 'control', 'distance_miles': 50,
     'segment_time_min': 180, 'stop_duration_min': 15, 'elevation_gain': 2000,
     'stop_name': 'Lunch', 'notes': 'Cafe'},
    {'stop_order': 3, 'location': 'Finish', 'stop_type': 'finish', 'distance_miles': 100,
     'segment_time_min': 180, 'stop_duration_min': 0, 'elevation_gain': 1000,
     'stop_name': None, 'notes': None},
]

# A synthetic warmed elevation track (route_weather_cache.elevation_track shape):
# ascending dist_m with varying e_m so build_elevation_profile returns available=True.
_ELEV_TRACK = [
    {'lat': 37.0, 'lng': -122.0, 'dist_m': 0.0, 'e_m': 10.0},
    {'lat': 37.1, 'lng': -122.1, 'dist_m': 40000.0, 'e_m': 300.0},
    {'lat': 37.2, 'lng': -122.2, 'dist_m': 80000.0, 'e_m': 120.0},
    {'lat': 37.3, 'lng': -122.3, 'dist_m': 160934.0, 'e_m': 60.0},
]


def _ride_pl(**over):
    r = {'id': 5, 'date': _pl_date(2026, 7, 4), 'plan_slug': 'sfr-100',
         'rwgps_url': 'https://ridewithgps.com/routes/123', 'rwgps_url_team': None,
         'plan_start_time': '06:00'}
    r.update(over)
    return r


def _bearer(app, user_id=1, rider_id=7):
    with app.app_context():
        return {'Authorization': 'Bearer ' + auth_mod.mint_mobile_token(user_id, rider_id)}


def _get_plan(client, app, *, track=_ELEV_TRACK):
    """Drive GET /api/ride/5/plan with the base plan + a (possibly None) warmed track.

    fetch_route is patched to blow up if EVER called — the plan path is cache-only, so
    reaching a live RWGPS fetch is a TA-237 regression, not a passing test.
    """
    with patch('routes.live.get_ride_by_id', return_value=_ride_pl()), \
         patch('models.get_ride_plan_by_slug', return_value=dict(_PLAN)), \
         patch('models.get_custom_plan', return_value=None), \
         patch('models.get_ride_plan_stops', return_value=[dict(s) for s in _PLAN_STOPS]), \
         patch('routes.live.get_route_elevation_track', return_value=track) as elev, \
         patch('services.weather.fetch_stop_wind', return_value=[]), \
         patch('routes.live.fetch_route',
               side_effect=AssertionError('TA-237: live RWGPS fetch on the plan path')) as fr:
        resp = client.get('/api/ride/5/plan', headers=_bearer(app))
    return resp, elev, fr


# ── (a) elevation profile present + cache-only on the happy path ────────────

def test_elevation_profile_present_and_available(client, app):
    resp, elev, fetch_route = _get_plan(client, app)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['available'] is True
    ep = data['elevation_profile']
    assert ep['available'] is True
    assert isinstance(ep['segments'], list) and ep['segments']
    # Markers come from the standard pace stops (they carry cumul_mi) — one per stop.
    assert isinstance(ep['markers'], list) and ep['markers']
    # Built from the warmed cache, exactly once, from the resolved route id.
    elev.assert_called_once_with('123')


def test_elevation_profile_is_cache_only_no_live_fetch(client, app):
    """TA-237: the plan path reads the warmed track from cache and NEVER fetches RWGPS."""
    resp, _elev, fetch_route = _get_plan(client, app)
    assert resp.status_code == 200
    fetch_route.assert_not_called()


# ── (b) fail-soft: cache miss → {'available': False}, still HTTP 200 ─────────

def test_elevation_profile_unavailable_on_cache_miss(client, app):
    resp, _elev, fetch_route = _get_plan(client, app, track=None)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['available'] is True                 # the plan itself still resolves
    assert data['elevation_profile'] == {'available': False}
    # Existing contract untouched — old clients keep working.
    assert len(data['stops']) == 3
    fetch_route.assert_not_called()


# ── (c) pace_stops_map has comfort/standard/push, each a non-empty stop list ──

def test_pace_stops_map_has_three_variants(client, app):
    resp, _elev, _fetch_route = _get_plan(client, app)
    data = resp.get_json()
    pace_map = data['pace_stops_map']
    assert sorted(pace_map.keys()) == ['comfort', 'push', 'standard']
    for pace_id, stops in pace_map.items():
        assert stops, f'{pace_id} has no stops'
        first = stops[0]
        assert 'cumul_mi' in first and 'eta' in first and 'bank' in first


def test_pace_cards_meta_carries_headers_without_stops(client, app):
    resp, _elev, _fetch_route = _get_plan(client, app)
    data = resp.get_json()
    meta = data['pace_cards_meta']
    assert len(meta) == 3
    ids = [c['id'] for c in meta]
    assert sorted(ids) == ['comfort', 'push', 'standard']
    for card in meta:
        assert 'name' in card and 'total' in card
        assert 'stops' not in card          # header labels only — stops live in the map


# ── (d) backward compatibility: the existing stops/plan contract is unchanged ──

def test_existing_stops_contract_unchanged(client, app):
    """A client that ignores the new keys sees the pre-#535 response verbatim."""
    resp, _elev, _fetch_route = _get_plan(client, app)
    data = resp.get_json()
    assert data['available'] is True
    assert data['plan']['name'] == 'SFR 100'
    s2 = data['stops'][1]
    assert s2['cum_time_min'] == 195 and s2['arrival_time_min'] == 180
    assert s2['eta'] == '9:00 AM'


def test_no_plan_returns_empty_pace_map(client, app):
    """A ride with no plan never carries pace/elevation payloads to swap."""
    with patch('routes.live.get_ride_by_id',
               return_value=_ride_pl(plan_slug=None, name='Mystery Ride')), \
         patch('models.get_all_ride_plans', return_value=[]):
        resp = client.get('/api/ride/5/plan', headers=_bearer(app))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['reason'] == 'no_plan'
    assert 'pace_stops_map' not in data          # short-circuits before the pace block
