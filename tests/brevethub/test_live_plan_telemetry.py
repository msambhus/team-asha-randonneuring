"""Plan-aware live telemetry (Mission 2) — assembly, fallbacks, tenant scoping, HUD.

BrevetHub pattern: monkeypatch brevethub.models.*, use the `client` fixture, never a
real DB, mock every RWGPS HTTP call (fetch_route is patched at the route import
site). The shared telemetry math itself is proven pure in tests/test_live_telemetry_shim.py;
here we prove the BrevetHub route assembles the parent-app field shapes correctly,
degrades gracefully, and resolves a ride to a plan WITHIN ITS OWN TENANT.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

_RIDER = {'id': 7, 'email': 'r@example.com', 'google_id': 'g-1',
          'profile_completed': True, 'rusa_id': None, 'club_id': 3,
          'rusa_id_duplicate': False}

# A public ride owned by club 3, with an RWGPS route so the route-id plan path runs.
_RIDE = {'id': 1, 'rider_id': 99, 'is_public': True, 'name': 'Coastal 200',
         'distance_km': 200, 'start_at': None, 'club_id': 3,
         'rwgps_url': 'https://ridewithgps.com/routes/123'}

# A straight south-to-north track at constant longitude; the `d` field carries the
# along-route distance in meters (10-mile spacing), so a rider fix that lands on a
# track point projects to a known, clean mileage.
_MI = 1609.344
_TRACK = [
    {'x': -122.0, 'y': 37.00, 'e': 0,   'd': 0 * _MI},
    {'x': -122.0, 'y': 37.05, 'e': 100, 'd': 10 * _MI},
    {'x': -122.0, 'y': 37.10, 'e': 200, 'd': 20 * _MI},
    {'x': -122.0, 'y': 37.15, 'e': 300, 'd': 30 * _MI},
    {'x': -122.0, 'y': 37.20, 'e': 400, 'd': 40 * _MI},
]
_ROUTE_DATA = {'track_points': _TRACK}

# Plan: 15 mph schedule with a distinct next-control (mile 35) and finish (mile 40).
_PLAN = {'id': 55, 'club_id': 3, 'cutoff_hours': 4, 'total_distance_miles': 40,
         'rwgps_route_id': '123', 'name': 'Coastal 200 Plan'}
_PLAN_STOPS = [
    {'distance_miles': 0,  'cum_time_min': 0,   'location': 'Start',     'stop_type': 'start'},
    {'distance_miles': 20, 'cum_time_min': 80,  'location': 'Control A', 'stop_type': 'control'},
    {'distance_miles': 35, 'cum_time_min': 140, 'location': 'Control B', 'stop_type': 'control'},
    {'distance_miles': 40, 'cum_time_min': 160, 'location': 'Finish',    'stop_type': 'finish'},
]


def _login(client, rider_id=7):
    with client.session_transaction() as sess:
        sess['rider_id'] = rider_id


def _latest_row(rider_id=7, name='alice', **tel):
    row = {'rider_id': rider_id, 'name': name, 'lat': 37.15, 'lng': -122.0,
           'recorded_at': None, 'speed': 6.0, 'heart_rate': 150, 'power': 200,
           'cadence': 85, 'source': 'garmin'}
    row.update(tel)
    return row


def _history(base):
    """Four fixes on the route (miles 0, 10, 20, 30), anchored 100 min before now so
    elapsed rounds to 100 min regardless of test wall-clock jitter."""
    lats = [37.00, 37.05, 37.10, 37.15]
    mins_ago = [100, 70, 40, 10]
    out = []
    for lat, ago in zip(lats, mins_ago):
        out.append({'lat': lat, 'lng': -122.0, 'speed': 6.0,
                    'recorded_at': base - timedelta(minutes=ago)})
    return out


# --------------------------------------------------------------------------- #
# Rider WITH a real in-tenant plan — numeric correctness of the assembled block.
# --------------------------------------------------------------------------- #
def test_rider_with_plan_full_telemetry_numeric(client):
    _login(client)
    now = datetime.now(timezone.utc)
    row = _latest_row(recorded_at=now - timedelta(minutes=10))
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=[row]), \
         patch('brevethub.models.get_rider_position_history_rp', return_value=_history(now)), \
         patch('brevethub.models.get_brevet_route_plan_by_route_id_rp', return_value=_PLAN), \
         patch('brevethub.models.get_brevet_route_plan_stops', return_value=_PLAN_STOPS), \
         patch('brevethub.routes.live.fetch_route', return_value=_ROUTE_DATA):
        resp = client.get('/live/1/live-positions.json')
    assert resp.status_code == 200
    t = resp.get_json()['positions'][0]['telemetry']

    assert t['on_route'] is True
    # Distance done / remaining from the route projection (mile 30 of 40).
    assert t['now']['distance_mi'] == 30.0
    assert t['remaining']['distance_mi'] == 10.0
    # Ascent split (feet): 300 m climbed of 400 m total.
    assert t['now']['ascent_done_ft'] == round(300 * 3.28084)
    assert t['remaining']['ascent_left_ft'] == round(400 * 3.28084) - round(300 * 3.28084)
    # Elapsed anchored to the first fix ~100 min.
    assert t['now']['elapsed_min'] == 100
    # Banked vs plan: at mile 30 the plan expects 120 min, rider took 100 → +20.
    assert t['time_banked_plan_min'] == 20
    assert t['plan']['status'] == 'ahead'
    # Banked vs ACP cutoff (OTL margin): pro-rata cutoff at mile 30 is 180 min → +80.
    assert t['time_banked_cutoff_min'] == 80
    # Next control = mile 35, 5 mi to go, plan arrival 140 min → 5 / ((140-100)/60) = 7.5 mph.
    nc = t['next_control']
    assert nc['name'] == 'Control B' and nc['dist_to_go_mi'] == 5.0
    assert nc['required_mph'] == 7.5
    assert nc['eta_iso'] is not None
    # Finish = mile 40, 10 mi to go, arrival 160 min → 10 / ((160-100)/60) = 10 mph.
    fin = t['finish']
    assert fin['dist_to_go_mi'] == 10.0 and fin['required_mph'] == 10.0
    # M1 basics still present in the now block.
    assert t['now']['heart_rate'] == 150 and t['now']['cadence'] == 85


def test_rider_with_plan_dot_color_reflects_timing(client):
    _login(client)
    now = datetime.now(timezone.utc)
    row = _latest_row(recorded_at=now - timedelta(minutes=10))
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=[row]), \
         patch('brevethub.models.get_rider_position_history_rp', return_value=_history(now)), \
         patch('brevethub.models.get_brevet_route_plan_by_route_id_rp', return_value=_PLAN), \
         patch('brevethub.models.get_brevet_route_plan_stops', return_value=_PLAN_STOPS), \
         patch('brevethub.routes.live.fetch_route', return_value=_ROUTE_DATA):
        resp = client.get('/live/1/live-positions.json')
    p = resp.get_json()['positions'][0]
    assert p['plan_color'] == '#16a34a'   # ahead of plan → green


# --------------------------------------------------------------------------- #
# Graceful degradation — never a 500, plan-aware fields absent.
# --------------------------------------------------------------------------- #
def test_no_plan_ride_returns_base_no_500(client):
    """A ride whose route id and name resolve to NO in-tenant plan still renders the
    M1 basics (route-relative distance is fine; plan-aware fields are absent)."""
    _login(client)
    now = datetime.now(timezone.utc)
    row = _latest_row(recorded_at=now - timedelta(minutes=10))
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=[row]), \
         patch('brevethub.models.get_rider_position_history_rp', return_value=_history(now)), \
         patch('brevethub.models.get_brevet_route_plan_by_route_id_rp', return_value=None), \
         patch('brevethub.models.get_brevet_route_plan_candidates_rp', return_value=[]), \
         patch('brevethub.routes.live.fetch_route', return_value=_ROUTE_DATA):
        resp = client.get('/live/1/live-positions.json')
    assert resp.status_code == 200
    t = resp.get_json()['positions'][0]['telemetry']
    assert t['now']['speed_mph'] is not None       # M1 basics present
    assert t['plan'] is None                        # plan-aware fields absent
    assert t['next_control'] is None and t['finish'] is None
    assert t['time_banked_plan_min'] is None and t['time_banked_cutoff_min'] is None


def test_thin_history_returns_base_no_500(client):
    """A rider with fewer than MIN_HISTORY_FOR_PLAN fixes gets M1 basics only."""
    _login(client)
    now = datetime.now(timezone.utc)
    row = _latest_row(recorded_at=now - timedelta(minutes=2))
    one_point = [{'lat': 37.0, 'lng': -122.0, 'speed': 5.0,
                  'recorded_at': now - timedelta(minutes=2)}]
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=[row]), \
         patch('brevethub.models.get_rider_position_history_rp', return_value=one_point), \
         patch('brevethub.models.get_brevet_route_plan_by_route_id_rp', return_value=_PLAN), \
         patch('brevethub.models.get_brevet_route_plan_stops', return_value=_PLAN_STOPS), \
         patch('brevethub.routes.live.fetch_route', return_value=_ROUTE_DATA):
        resp = client.get('/live/1/live-positions.json')
    assert resp.status_code == 200
    t = resp.get_json()['positions'][0]['telemetry']
    assert t['on_route'] is None                    # not projected
    assert t['plan'] is None and t['next_control'] is None
    assert t['now']['speed_mph'] is not None


def test_route_fetch_error_degrades_no_500(client):
    """An RWGPS fetch error yields dots + M1 basics, never a 500."""
    _login(client)
    now = datetime.now(timezone.utc)
    row = _latest_row(recorded_at=now - timedelta(minutes=10))
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=[row]), \
         patch('brevethub.models.get_rider_position_history_rp', return_value=_history(now)), \
         patch('brevethub.models.get_brevet_route_plan_by_route_id_rp', return_value=_PLAN), \
         patch('brevethub.models.get_brevet_route_plan_stops', return_value=_PLAN_STOPS), \
         patch('brevethub.routes.live.fetch_route', side_effect=Exception('rwgps down')):
        resp = client.get('/live/1/live-positions.json')
    assert resp.status_code == 200
    t = resp.get_json()['positions'][0]['telemetry']
    assert t['on_route'] is None                    # no route geometry → no projection
    assert t['now']['speed_mph'] is not None


# --------------------------------------------------------------------------- #
# Tenant scoping — the ride is resolved to a plan within its OWN club only.
# --------------------------------------------------------------------------- #
def test_route_id_lookup_is_scoped_to_ride_club(client):
    """The primary plan lookup is called with the ride OWN club id, so the SQL scope
    (club_id = %s OR club_id IS NULL) can never return another club plan."""
    _login(client)
    now = datetime.now(timezone.utc)
    row = _latest_row(recorded_at=now - timedelta(minutes=10))
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=[row]), \
         patch('brevethub.models.get_rider_position_history_rp', return_value=_history(now)), \
         patch('brevethub.models.get_brevet_route_plan_by_route_id_rp', return_value=_PLAN) as by_id, \
         patch('brevethub.models.get_brevet_route_plan_stops', return_value=_PLAN_STOPS), \
         patch('brevethub.routes.live.fetch_route', return_value=_ROUTE_DATA):
        client.get('/live/1/live-positions.json')
    by_id.assert_called_once_with('123', 3)         # route id + the ride club (3)


def test_name_fallback_feeds_only_club_scoped_candidates(client):
    """When no route-id plan matches, the name matcher is fed ONLY the club-scoped
    candidate list — never every club plans."""
    _login(client)
    now = datetime.now(timezone.utc)
    row = _latest_row(recorded_at=now - timedelta(minutes=10))
    candidates = [dict(_PLAN, id=77, name='Coastal 200')]
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_RIDE), \
         patch('brevethub.models.get_live_positions_rp', return_value=[row]), \
         patch('brevethub.models.get_rider_position_history_rp', return_value=_history(now)), \
         patch('brevethub.models.get_brevet_route_plan_by_route_id_rp', return_value=None), \
         patch('brevethub.models.get_brevet_route_plan_candidates_rp', return_value=candidates) as cand, \
         patch('brevethub.models.get_brevet_route_plan_stops', return_value=_PLAN_STOPS), \
         patch('brevethub.routes.live.fetch_route', return_value=_ROUTE_DATA):
        resp = client.get('/live/1/live-positions.json')
    cand.assert_called_once_with(3)                 # candidate list scoped to club 3
    # The name match resolved to the in-tenant candidate → plan-aware fields present.
    t = resp.get_json()['positions'][0]['telemetry']
    assert t['plan'] is not None


# --------------------------------------------------------------------------- #
# Live-grading regression (the redteam gate) — meal rows never corrupt grading
# --------------------------------------------------------------------------- #
def test_live_grading_excludes_meals_and_recovers_meal_free_timing():
    """A conservative plan carrying meal-break rows grades BYTE-FOR-BYTE like the
    meal-free plan: _ride_live_context excludes stop_type='meal' rows and subtracts the
    accumulated preceding dwell from each control's cum_time_min, so plan_stops equals
    the pre-change single meal-free series and no meal row reaches the graded set."""
    from brevethub.routes import live

    # The meal-free control series a single plan produced before the variant split.
    meal_free = [
        {'distance_miles': 0,  'cum_time_min': 0,   'location': 'Start',     'stop_type': 'start',   'segment_time_min': 0},
        {'distance_miles': 20, 'cum_time_min': 92,  'location': 'Control A', 'stop_type': 'control', 'segment_time_min': 92},
        {'distance_miles': 40, 'cum_time_min': 184, 'location': 'Control B', 'stop_type': 'control', 'segment_time_min': 92},
        {'distance_miles': 60, 'cum_time_min': 276, 'location': 'Finish',    'stop_type': 'finish',  'segment_time_min': 92},
    ]
    # The stored conservative plan: same controls, plus a 30-min meal row after Control
    # A that shifts every later control's stored cum_time_min by +30.
    meal_laden = [
        {'distance_miles': 0,  'cum_time_min': 0,   'location': 'Start',     'stop_type': 'start',   'segment_time_min': 0},
        {'distance_miles': 20, 'cum_time_min': 92,  'location': 'Control A', 'stop_type': 'control', 'segment_time_min': 92},
        {'distance_miles': 20, 'cum_time_min': 122, 'location': 'Lunch',     'stop_type': 'meal',    'segment_time_min': 30},
        {'distance_miles': 40, 'cum_time_min': 214, 'location': 'Control B', 'stop_type': 'control', 'segment_time_min': 92},
        {'distance_miles': 60, 'cum_time_min': 306, 'location': 'Finish',    'stop_type': 'finish',  'segment_time_min': 92},
    ]
    plan = {'id': 55, 'club_id': 3, 'cutoff_hours': 4, 'total_distance_miles': 60,
            'rwgps_route_id': '123', 'name': 'X'}

    def _ctx(stops):
        with patch('brevethub.models.get_brevet_route_plan_by_route_id_rp',
                   return_value=plan) as by_id, \
             patch('brevethub.models.get_brevet_route_plan_stops', return_value=stops), \
             patch('brevethub.routes.live.fetch_route', return_value={'track_points': []}):
            return live._ride_live_context(dict(_RIDE)), by_id

    laden_ctx, by_id = _ctx(meal_laden)
    free_ctx, _ = _ctx(meal_free)

    # No meal row survives into the graded checkpoints.
    assert all(s['stop_type'] != 'meal' for s in laden_ctx['plan_stops'])
    # Byte-for-byte identical graded series (distance + de-dwelled cum time).
    assert laden_ctx['plan_stops'] == free_ctx['plan_stops']
    assert laden_ctx['plan_stops'] == [
        {'distance_miles': 0.0,  'cum_time_min': 0.0,   'location': 'Start',     'stop_type': 'start'},
        {'distance_miles': 20.0, 'cum_time_min': 92.0,  'location': 'Control A', 'stop_type': 'control'},
        {'distance_miles': 40.0, 'cum_time_min': 184.0, 'location': 'Control B', 'stop_type': 'control'},
        {'distance_miles': 60.0, 'cum_time_min': 276.0, 'location': 'Finish',    'stop_type': 'finish'},
    ]
    # The route-id reader is pinned to conservative (default kwarg → no aggressive plan).
    by_id.assert_called_once_with('123', 3)


def test_plan_by_route_id_query_is_club_scoped_and_pins_conservative():
    """The route-id plan SQL scopes to the ride club OR a club-less warm plan, PINS the
    conservative variant, and passes the route id + club id + variant as bound params."""
    import brevethub.models as models
    with patch('brevethub.db.query_one', return_value=None) as q:
        models.get_brevet_route_plan_by_route_id_rp('123', 5)
    sql, params = q.call_args[0]
    assert 'club_id = %s OR club_id IS NULL' in sql
    assert 'rwgps_route_id = %s' in sql
    assert 'variant = %s' in sql
    assert params == ('123', 5, 'conservative')


def test_plan_candidates_query_is_club_scoped_and_pins_conservative():
    import brevethub.models as models
    with patch('brevethub.db.query', return_value=[]) as q:
        models.get_brevet_route_plan_candidates_rp(9)
    sql, params = q.call_args[0]
    assert 'club_id = %s OR club_id IS NULL' in sql
    assert 'variant = %s' in sql
    assert params == (9, 'conservative')


def test_plan_by_route_id_none_route_returns_none_without_query():
    import brevethub.models as models
    with patch('brevethub.db.query_one') as q:
        assert models.get_brevet_route_plan_by_route_id_rp(None, 3) is None
    q.assert_not_called()


# --------------------------------------------------------------------------- #
# Surface-B HUD render — the plan-aware readout is wired into the member map page.
# --------------------------------------------------------------------------- #
def test_map_page_renders_plan_aware_hud(app, client):
    _login(client)
    app.config['MAPBOX_ACCESS_TOKEN'] = 'pk.test-token'
    with patch('brevethub.models.get_rider_by_id', return_value=_RIDER), \
         patch('brevethub.models.get_ride', return_value=_RIDE), \
         patch('brevethub.models.get_live_tracking_rp', return_value=None), \
         patch('brevethub.routes.live.fetch_route', return_value=_ROUTE_DATA):
        resp = client.get('/live/1/map')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Plan-aware HUD labels (mirrored from the parent web app live HUD).
    for label in ('vs plan', 'vs cutoff', 'Time banked', 'req speed',
                  'Off route', 'to go', 'ahead of plan'):
        assert label in body, f'missing HUD label: {label}'
    assert 'live-positions.json' in body            # the member poll URL is wired
