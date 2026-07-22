"""Live plan-selector round 2: the authorization allow-set, selected-plan
resolution (base / own / allowed-custom / rejected-private / malformed), the shared
upcoming-controls list, and the per-rider speed-to-finish block.

The single most important property here is the IDOR guard: a plan_id the viewer may
not see (another rider's private plan, an unknown/malformed id, any private id for a
guest) must fall back to the base plan and never leak the private plan. All external
HTTP is mocked and models are patched, so no DB is required.
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def _now():
    return datetime.now(timezone.utc)


def _login(client, rider_id=7):
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['rider_id'] = rider_id


# Base plan stops shared by the resolver tests (start + one control).
_BASE = [
    {'distance_miles': 0, 'cum_time_min': 0, 'arrival_time_min': 0,
     'location': 'Start', 'stop_type': 'start'},
    {'distance_miles': 1.1, 'cum_time_min': 30, 'arrival_time_min': 20,
     'location': 'Control 1', 'stop_type': 'control'},
]


# ── _available_plans: the authorization allow-set + selector options ────────

def test_available_plans_member_includes_public_and_own():
    """A logged-in rider may resolve base, every public custom plan, AND their own
    custom plan; the 'own' (each-rider's-own) lens is offered last."""
    from routes.live import _available_plans, PLAN_OWN, PLAN_BASE
    publics = [{'id': 11, 'name': 'Fast', 'first_name': 'Alice', 'is_public': True},
               {'id': 12, 'name': 'Chill', 'first_name': 'Bob', 'is_public': True}]
    own = {'id': 13, 'name': 'My push'}
    with patch('models.get_public_custom_plans', return_value=publics), \
         patch('models.get_custom_plan', return_value=own):
        options, allowed = _available_plans(base_plan_id=5, viewer_rider_id=99)
    ids = [o['id'] for o in options]
    assert ids[0] == PLAN_BASE
    assert allowed == {11, 12, 13}
    assert ids[-1] == PLAN_OWN            # 'own' sentinel offered last
    assert 11 in ids and 13 in ids


def test_available_plans_guest_gets_public_only_never_own():
    """A guest (rider_id None) sees base + public plans only — the private 'own'
    lookup is NEVER performed AND the 'own' per-rider lens is NOT offered (it would
    expose per-rider private-plan timing to a guest)."""
    from routes.live import _available_plans, PLAN_OWN, PLAN_BASE
    publics = [{'id': 11, 'name': 'Fast', 'first_name': 'Alice', 'is_public': True}]
    with patch('models.get_public_custom_plans', return_value=publics), \
         patch('models.get_custom_plan') as own_lookup:
        options, allowed = _available_plans(base_plan_id=5, viewer_rider_id=None)
    own_lookup.assert_not_called()
    assert allowed == {11}
    ids = [o['id'] for o in options]
    assert PLAN_OWN not in ids                       # 'own' withheld from guests
    assert ids == [PLAN_BASE, 11]                    # base + the public plan only


def test_available_plans_single_plan_ride_has_no_selector():
    """No custom plans → base only → the client renders no selector, just 'base plan'."""
    from routes.live import _available_plans, PLAN_BASE
    with patch('models.get_public_custom_plans', return_value=[]), \
         patch('models.get_custom_plan', return_value=None):
        options, allowed = _available_plans(base_plan_id=5, viewer_rider_id=99)
    assert allowed == set()
    assert len(options) == 1 and options[0]['id'] == PLAN_BASE


def test_available_plans_dedups_own_that_is_also_public():
    """A rider's own plan that is also public isn't listed twice."""
    from routes.live import _available_plans
    pub = {'id': 11, 'name': 'Fast', 'first_name': 'Alice', 'is_public': True}
    with patch('models.get_public_custom_plans', return_value=[pub]), \
         patch('models.get_custom_plan', return_value={'id': 11, 'name': 'Fast'}):
        options, allowed = _available_plans(base_plan_id=5, viewer_rider_id=99)
    assert allowed == {11}
    assert [o['id'] for o in options].count(11) == 1


# ── _selected_plan_stops: strict resolution against the allow-set ───────────

def test_selected_plan_stops_base_and_own(app):
    from routes.live import _selected_plan_stops, PLAN_BASE, PLAN_OWN
    ctx = {'plan_stops': _BASE}
    with app.app_context():
        assert _selected_plan_stops(None, ctx, set()) == (PLAN_BASE, _BASE)
        assert _selected_plan_stops('base', ctx, set()) == (PLAN_BASE, _BASE)
        # 'own' resolves only when it was OFFERED — a member WITH a visible custom
        # plan (allow-set non-empty). Then each rider keeps their own plan.
        applied, stops = _selected_plan_stops('own', ctx, {11}, is_member=True)
    assert applied == PLAN_OWN and stops is None


def test_selected_plan_stops_own_rejected_for_guest(app):
    """AUTHORIZATION: a guest (is_member False) requesting the 'own' lens falls back to
    the base plan — never per-rider (private-plan) grading — even when public plans
    exist (allow-set non-empty)."""
    from routes.live import _selected_plan_stops, PLAN_BASE
    ctx = {'plan_stops': _BASE}
    with app.app_context():
        applied, stops = _selected_plan_stops('own', ctx, {11}, is_member=False)
    assert applied == PLAN_BASE and stops is _BASE


def test_selected_plan_stops_own_rejected_when_not_offered(app):
    """COUNCIL FIX: a member for whom 'own' was WITHHELD (no visible custom plan →
    empty allow-set) cannot craft ?plan_id=own into per-rider private grading; it
    falls back to base. Offer and resolution share one predicate, so they can't drift."""
    from routes.live import _selected_plan_stops, PLAN_BASE
    ctx = {'plan_stops': _BASE}
    with app.app_context():
        applied, stops = _selected_plan_stops('own', ctx, set(), is_member=True)
    assert applied == PLAN_BASE and stops is _BASE


def test_selected_plan_stops_allowed_custom_overrides(app):
    from routes.live import _selected_plan_stops
    ctx = {'plan_stops': _BASE}
    custom = [{'distance_miles': 0, 'cum_time_min': 0},
              {'distance_miles': 10, 'cum_time_min': 50}]
    with app.app_context():
        with patch('routes.live._merge_custom_stops', return_value=custom):
            applied, stops = _selected_plan_stops('42', ctx, {42})
    assert applied == 42 and stops is custom


def test_selected_plan_stops_rejects_private_id_no_leak(app):
    """IDOR: an id NOT in the allow-set (another rider's private plan) → base fallback,
    and the plan is NEVER merged/read — no private-plan leak."""
    from routes.live import _selected_plan_stops, PLAN_BASE
    ctx = {'plan_stops': _BASE}
    with app.app_context():
        with patch('routes.live._merge_custom_stops') as merge:
            applied, stops = _selected_plan_stops('999', ctx, {42})   # 999 not allowed
    merge.assert_not_called()
    assert applied == PLAN_BASE and stops is _BASE


def test_selected_plan_stops_malformed_id_falls_back(app):
    from routes.live import _selected_plan_stops, PLAN_BASE
    ctx = {'plan_stops': _BASE}
    with app.app_context():
        applied, stops = _selected_plan_stops('not-an-int', ctx, {42})
    assert applied == PLAN_BASE and stops is _BASE


def test_selected_plan_stops_merge_failure_falls_back(app):
    """An allowed id whose merge yields <2 usable stops (None) → base fallback."""
    from routes.live import _selected_plan_stops, PLAN_BASE
    ctx = {'plan_stops': _BASE}
    with app.app_context():
        with patch('routes.live._merge_custom_stops', return_value=None):
            applied, stops = _selected_plan_stops('42', ctx, {42})
    assert applied == PLAN_BASE and stops is _BASE


# ── Endpoint wiring: /api/live/positions plan selection + non-leak ──────────

def _arrival_ctx(start_dt, **over):
    """A ctx whose next control (1.1 mi) has arrival_time_min 20 and cum 30, plus a
    base_plan_id so the plan selector engages."""
    ctx = {
        'has_route': True,
        'track': [{'lat': 37.0, 'lng': -122.00, 'dist_m': 0.0},
                  {'lat': 37.0, 'lng': -121.99, 'dist_m': 889.0},
                  {'lat': 37.0, 'lng': -121.98, 'dist_m': 1778.0}],
        'cum_ascent_ft': [0, 100, 200], 'total_dist_m': 1778.0, 'total_ascent_ft': 200,
        'wind_by_dist': [{'dist_m': 0, 'headwind_kmh': 12}, {'dist_m': 1778, 'headwind_kmh': -4}],
        'ride_start_iso': start_dt.isoformat(),
        'plan_total_mi': 100.0, 'plan_cutoff_hours': 10, 'base_plan_id': 5,
        'plan_stops': [
            {'distance_miles': 0, 'cum_time_min': 0, 'arrival_time_min': 0,
             'location': 'Start', 'stop_type': 'start'},
            {'distance_miles': 1.1, 'cum_time_min': 30, 'arrival_time_min': 20,
             'location': 'Control 1', 'stop_type': 'control'}],
    }
    ctx.update(over)
    return ctx


def _row():
    return {'rider_id': 7, 'name': 'Asha Rider', 'lat': 37.0, 'lng': -121.99,
            'recorded_at': _now() - timedelta(minutes=2), 'status': 'GOING',
            'speed': 6.0, 'heart_rate': None, 'power': None, 'cadence': None}


def _positions_json(client, ctx, query='', publics=None, own=None):
    """Run the endpoint with the heavy context patched out and the plan-lookup model
    functions defaulted to empty (base_plan_id=5 in ctx would otherwise hit the DB).
    History is empty on purpose: a rider projects off its single current fix (the
    stateless fallback), so telemetry appears on the very first position."""
    with patch('routes.live.get_latest_positions_for_ride', return_value=[_row()]), \
         patch('routes.live._ride_live_context', return_value=ctx), \
         patch('routes.live.get_positions_for_rider_since', return_value=[]), \
         patch('models.get_public_custom_plans', return_value=publics or []), \
         patch('models.get_custom_plan', return_value=own):
        return client.get('/api/live/positions?ride_id=5' + query).get_json()


_PUB = [{'id': 11, 'name': 'Pub', 'first_name': 'A', 'is_public': True}]


def test_positions_rejects_unauthorized_plan_id(client):
    """AUTHORIZATION non-leak (Verification #8): a plan_id outside the allow-set applies
    the base plan; the private plan is never resolved and never appears in `plans`."""
    _login(client, rider_id=7)
    ctx = _arrival_ctx(_now() - timedelta(minutes=5))
    with patch('routes.live._merge_custom_stops') as merge:
        body = _positions_json(client, ctx, '&plan_id=999', publics=_PUB)
    merge.assert_not_called()                       # private plan never read
    assert body['selected_plan_id'] == 'base'       # rejected → base fallback
    ids = [p['id'] for p in body['plans']]
    assert 999 not in ids and 'base' in ids and 11 in ids


def test_positions_applies_allowed_public_plan_regrades_everyone(client):
    """Selecting an allowed plan re-grades EVERY rider against it: the next-control
    arrival comes from the selected plan (18), not the base plan (20)."""
    _login(client, rider_id=7)
    ctx = _arrival_ctx(_now() - timedelta(minutes=5))
    custom = [{'distance_miles': 0, 'cum_time_min': 0, 'arrival_time_min': 0,
               'location': 'S', 'stop_type': 'start'},
              {'distance_miles': 1.1, 'cum_time_min': 25, 'arrival_time_min': 18,
               'location': 'C1', 'stop_type': 'control'}]
    with patch('routes.live._merge_custom_stops', return_value=custom):
        body = _positions_json(client, ctx, '&plan_id=11', publics=_PUB)
    assert body['selected_plan_id'] == 11
    nc = body['positions'][0]['telemetry']['next_control']
    assert nc['arrival_time_min'] == 18             # selected plan's timing, not base's 20


def test_positions_default_is_base_plan(client):
    """No plan_id → the base plan is applied (selected_plan_id == 'base')."""
    _login(client, rider_id=7)
    body = _positions_json(client, _arrival_ctx(_now() - timedelta(minutes=5)))
    assert body['selected_plan_id'] == 'base'


def _guest(client, code='ABCD-2K9P', ride_id=5):
    with client.session_transaction() as s:
        s['live_guest'] = {'code': code, 'ride_id': ride_id}


def test_positions_guest_own_lens_falls_back_to_base(client):
    """AUTHORIZATION (endpoint): a GUEST requesting plan_id=own gets the base plan —
    the 'own' per-rider lens is never applied (no private per-rider timing leaks) and
    is not even offered in `plans`."""
    _guest(client, ride_id=5)
    inv = {'code': 'ABCD-2K9P', 'ride_id': 5, 'expires_at': None}
    ctx = _arrival_ctx(_now() - timedelta(minutes=5))
    with patch('routes.live.get_valid_ride_invite', return_value=inv), \
         patch('routes.live.get_latest_positions_for_ride', return_value=[_row()]), \
         patch('routes.live._ride_live_context', return_value=ctx), \
         patch('routes.live.get_positions_for_rider_since', return_value=[]), \
         patch('models.get_public_custom_plans', return_value=_PUB), \
         patch('models.get_custom_plan') as own_lookup, \
         patch('routes.live._rider_plan_stops') as per_rider:
        body = client.get('/api/live/positions?ride_id=5&plan_id=own').get_json()
    own_lookup.assert_not_called()                  # no per-rider own-plan lookup for a guest
    per_rider.assert_not_called()                   # per-rider grading never engaged
    assert body['selected_plan_id'] == 'base'
    assert 'own' not in [p['id'] for p in body['plans']]   # 'own' withheld from guests


def test_positions_member_own_rejected_when_not_offered(client):
    """COUNCIL FIX (endpoint): a MEMBER on a ride with NO visible custom plans is not
    offered 'own', so a crafted ?plan_id=own falls back to base and never triggers
    the per-rider branch that would read other riders' PRIVATE custom plans."""
    _login(client, rider_id=7)
    ctx = _arrival_ctx(_now() - timedelta(minutes=5))
    with patch('routes.live.get_latest_positions_for_ride', return_value=[_row()]), \
         patch('routes.live._ride_live_context', return_value=ctx), \
         patch('routes.live.get_positions_for_rider_since', return_value=[]), \
         patch('models.get_public_custom_plans', return_value=[]), \
         patch('models.get_custom_plan', return_value=None), \
         patch('routes.live._rider_plan_stops') as per_rider:
        body = client.get('/api/live/positions?ride_id=5&plan_id=own').get_json()
    per_rider.assert_not_called()                   # per-rider private grading never engaged
    assert body['selected_plan_id'] == 'base'       # withheld 'own' → base fallback
    assert 'own' not in [p['id'] for p in body['plans']]


def test_positions_no_sharers_still_returns_plans_charts_and_controls(client):
    """Fix for the spectator gap: a ride with NO active sharers still builds the
    context, so the plan selector, route-ahead chart_data, and the shared
    upcoming-controls list are all present even before anyone broadcasts."""
    _login(client, rider_id=7)
    chart = {'labels': [0.0, 0.5, 1.1], 'elevation_ft': [30, 60, 90],
             'headwind_mph': [5.0, 4.0, 3.0], 'temperature_f': [60.0, 61.0, 62.0]}
    ctx = _arrival_ctx(_now() - timedelta(minutes=5), chart_data=chart)
    with patch('routes.live.get_latest_positions_for_ride', return_value=[]), \
         patch('routes.live._ride_live_context', return_value=ctx) as build_ctx, \
         patch('models.get_public_custom_plans', return_value=_PUB), \
         patch('models.get_custom_plan', return_value=None):
        body = client.get('/api/live/positions?ride_id=5').get_json()
    build_ctx.assert_called_once()                       # context built despite no rows
    assert body['positions'] == []                       # nobody sharing yet
    ids = [p['id'] for p in body['plans']]
    assert ids[0] == 'base' and 11 in ids and 'own' in ids   # >1 plan → selector shows
    assert body['chart_data'] is not None                # route-ahead charts available
    assert body['upcoming_controls']                     # shared upcoming-controls present


# ── Shared upcoming-controls list (item 2) ─────────────────────────────────

def test_upcoming_controls_shared_ride_level_with_eta(client):
    """One ride-level list of the applied plan's future controls, each with a
    club-local ETA — not repeated per rider."""
    _login(client, rider_id=7)
    body = _positions_json(client, _arrival_ctx(_now() - timedelta(minutes=5)))
    uc = body['upcoming_controls']
    assert isinstance(uc, list) and uc                      # present
    c1 = next(c for c in uc if abs((c['distance_mi'] or 0) - 1.1) < 0.2)
    assert c1['eta_label']                                  # club-local clock time
    assert c1['arrival_time_min'] == 20


# ── Per-rider speed-to-finish (item 3) ─────────────────────────────────────

def test_finish_required_speed_present(client):
    """Each rider card gets a finish block with a speed-to-finish and behind flag."""
    _login(client, rider_id=7)
    body = _positions_json(client, _arrival_ctx(_now() - timedelta(minutes=5)))
    fin = body['positions'][0]['telemetry']['finish']
    assert fin is not None
    assert fin['distance_mi'] is not None
    assert 'required_mph' in fin and 'behind' in fin


def test_finish_required_speed_behind_is_null(client):
    """Past the finish's plan arrival → required_mph null + behind True (em-dash)."""
    _login(client, rider_id=7)
    body = _positions_json(client, _arrival_ctx(_now() - timedelta(minutes=600)))
    fin = body['positions'][0]['telemetry']['finish']
    assert fin['required_mph'] is None and fin['behind'] is True


# ── finish_stop unit (services/live_telemetry) ─────────────────────────────

def test_finish_stop_picks_farthest_non_start():
    from services.live_telemetry import finish_stop
    stops = [{'distance_miles': 0, 'cum_time_min': 0, 'stop_type': 'start'},
             {'distance_miles': 1.1, 'cum_time_min': 30, 'arrival_time_min': 20,
              'location': 'C1', 'stop_type': 'control'},
             {'distance_miles': 5.0, 'cum_time_min': 90, 'arrival_time_min': 80,
              'location': 'Finish', 'stop_type': 'finish'}]
    fin = finish_stop(stops)
    assert fin['distance_miles'] == 5.0 and fin['arrival_time_min'] == 80
    assert finish_stop([]) is None
    assert finish_stop(None) is None
