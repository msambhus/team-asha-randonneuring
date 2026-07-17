"""BrevetHub real-plan model layer (rp_brevet_route_plan[_stop] + club ownership).

No real DB (repo convention): the read helpers monkeypatch brevethub.db.query /
query_one, and the transactional upsert runs against a fake connection so we can
assert the statement shape. Contracts:
  - upsert persists the plan + one row per stop in NATIVE units (verbatim), inside a
    single transaction (delete-old-stops then re-insert), and is idempotent on re-run,
  - the read helper returns the plan bundled with its ordered stops (or None),
  - club-ownership checks are scoped to owner_rider_id.
"""
from brevethub import models
from brevethub import db


_PLAN = {
    'name': 'Fixture 200', 'slug': 'fixture-200',
    'total_distance_miles': 124.3, 'total_elevation_ft': 3280,
    'rwgps_url': 'https://ridewithgps.com/routes/1', 'rwgps_route_id': '1',
    'distance_km': 200, 'cutoff_hours': 13.5, 'start_time': '07:00',
    'avg_moving_speed': 12.0, 'avg_elapsed_speed': 11.5,
    'total_moving_time_min': 620, 'total_elapsed_time_min': 640,
    'total_break_time_min': 0, 'overall_ft_per_mile': 26.4,
}
_STOPS = [
    {'stop_order': 1, 'location': 'Start', 'stop_type': 'start',
     'distance_miles': 0.0, 'elevation_gain': 0, 'segment_time_min': 0,
     'notes': '', 'seg_dist': 0.0, 'ft_per_mi': None, 'avg_speed': None,
     'cum_time_min': 0, 'bookend_time_min': None, 'time_bank_min': None,
     'difficulty_score': 0.0},
    {'stop_order': 2, 'location': 'Midway Control', 'stop_type': 'control',
     'distance_miles': 62.1, 'elevation_gain': 1600, 'segment_time_min': 310,
     'notes': '', 'seg_dist': 62.1, 'ft_per_mi': 26, 'avg_speed': 12.0,
     'cum_time_min': 310, 'bookend_time_min': 400, 'time_bank_min': 90,
     'difficulty_score': 2.6},
    {'stop_order': 3, 'location': 'Finish', 'stop_type': 'finish',
     'distance_miles': 124.3, 'elevation_gain': 1680, 'segment_time_min': 315,
     'notes': '', 'seg_dist': 62.2, 'ft_per_mi': 27, 'avg_speed': 11.9,
     'cum_time_min': 625, 'bookend_time_min': 750, 'time_bank_min': 125,
     'difficulty_score': 2.7},
]


class _FakeCursor:
    def __init__(self):
        self.calls = []
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def execute(self, sql, params=None):
        self.calls.append((sql, params))
    def fetchone(self):
        return {'id': 42}


class _FakeConn:
    def __init__(self):
        self.cur = _FakeCursor()
        self.committed = 0
        self.rolled_back = 0
    def cursor(self, cursor_factory=None):
        return self.cur
    def commit(self):
        self.committed += 1
    def rollback(self):
        self.rolled_back += 1


# --------------------------------------------------------------------------- #
# Upsert — native units, transaction shape, idempotency
# --------------------------------------------------------------------------- #
def test_upsert_persists_plan_and_stops_native(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(db, 'get_db', lambda: conn)

    plan_id = models.upsert_brevet_route_plan(11, _PLAN, _STOPS, club_id=3)
    assert plan_id == 42
    assert conn.committed == 1

    calls = conn.cur.calls
    insert_plan = calls[0]
    assert 'INSERT INTO rp_brevet_route_plan ' in insert_plan[0]
    # event_id + club_id are bound, native miles stored VERBATIM (no conversion).
    assert insert_plan[1][0] == 11         # event_id
    assert insert_plan[1][1] == 3          # club_id
    assert 124.3 in insert_plan[1]         # total_distance_miles, native

    # Old stops deleted, then one INSERT per stop.
    assert any('DELETE FROM rp_brevet_route_plan_stop' in c[0] for c in calls)
    stop_inserts = [c for c in calls if 'INSERT INTO rp_brevet_route_plan_stop' in c[0]]
    assert len(stop_inserts) == len(_STOPS)
    # A stop's native distance_miles is bound verbatim (62.1, not km).
    assert any(62.1 in c[1] for c in stop_inserts)


def test_upsert_is_idempotent_on_rerun(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(db, 'get_db', lambda: conn)
    first = models.upsert_brevet_route_plan(11, _PLAN, _STOPS)
    second = models.upsert_brevet_route_plan(11, _PLAN, _STOPS)
    assert first == second == 42
    assert conn.committed == 2  # both runs completed cleanly (ON CONFLICT upsert)


def test_upsert_rolls_back_on_error(monkeypatch):
    class _BoomCursor(_FakeCursor):
        def execute(self, sql, params=None):
            raise RuntimeError('db down')
    conn = _FakeConn()
    conn.cur = _BoomCursor()
    monkeypatch.setattr(db, 'get_db', lambda: conn)
    import pytest
    with pytest.raises(RuntimeError):
        models.upsert_brevet_route_plan(11, _PLAN, _STOPS)
    assert conn.rolled_back == 1 and conn.committed == 0


def test_upsert_blocked_when_another_club_owns(monkeypatch):
    """Ownership guard: when the conflict update is blocked (RETURNING -> no row),
    the upsert returns None and NEVER touches the existing club's stops."""
    class _BlockedCursor(_FakeCursor):
        def fetchone(self):
            return None                     # WHERE clause blocked the update
    conn = _FakeConn()
    conn.cur = _BlockedCursor()
    monkeypatch.setattr(db, 'get_db', lambda: conn)

    result = models.upsert_brevet_route_plan(11, _PLAN, _STOPS, club_id=99)
    assert result is None
    assert conn.rolled_back == 1 and conn.committed == 0
    # No stop DELETE / INSERT ran — the other club's plan is left intact.
    assert not any('rp_brevet_route_plan_stop' in c[0] for c in conn.cur.calls)
    assert not any('DELETE' in c[0] for c in conn.cur.calls)


def test_upsert_sql_carries_ownership_guard(monkeypatch):
    """The conflict update is guarded so a club can only adopt an unowned plan or
    refresh its own — the SQL must carry that WHERE clause."""
    conn = _FakeConn()
    monkeypatch.setattr(db, 'get_db', lambda: conn)
    models.upsert_brevet_route_plan(11, _PLAN, _STOPS, club_id=3)
    insert_sql = conn.cur.calls[0][0]
    assert 'rp_brevet_route_plan.club_id IS NULL' in insert_sql
    assert 'rp_brevet_route_plan.club_id = EXCLUDED.club_id' in insert_sql


# --------------------------------------------------------------------------- #
# Read — plan bundled with ordered stops
# --------------------------------------------------------------------------- #
def test_get_plan_with_stops(monkeypatch):
    monkeypatch.setattr(db, 'query_one', lambda sql, params=None: {'id': 5, 'name': 'Fixture 200'})
    monkeypatch.setattr(db, 'query', lambda sql, params=None: _STOPS)
    bundle = models.get_brevet_route_plan_with_stops(11)
    assert bundle['plan']['id'] == 5
    assert [s['stop_order'] for s in bundle['stops']] == [1, 2, 3]


def test_get_plan_with_stops_none_when_absent(monkeypatch):
    monkeypatch.setattr(db, 'query_one', lambda sql, params=None: None)
    monkeypatch.setattr(db, 'query', lambda sql, params=None: [])
    assert models.get_brevet_route_plan_with_stops(11) is None


# --------------------------------------------------------------------------- #
# Club ownership — scoped to owner_rider_id
# --------------------------------------------------------------------------- #
def test_is_club_owner_true_false(monkeypatch):
    monkeypatch.setattr(db, 'query_one', lambda sql, params=None: {'ok': 1})
    assert models.is_club_owner(3, 7) is True
    monkeypatch.setattr(db, 'query_one', lambda sql, params=None: None)
    assert models.is_club_owner(3, 7) is False
    # Missing ids short-circuit without a query.
    assert models.is_club_owner(None, 7) is False
    assert models.is_club_owner(3, None) is False


def test_get_club_owned_by_rider(monkeypatch):
    club = {'id': 3, 'name': 'SFR', 'owner_rider_id': 7}
    monkeypatch.setattr(db, 'query_one', lambda sql, params=None: club)
    assert models.get_club_owned_by_rider(7)['id'] == 3
    monkeypatch.setattr(db, 'query_one', lambda sql, params=None: None)
    assert models.get_club_owned_by_rider(7) is None
    assert models.get_club_owned_by_rider(None) is None
