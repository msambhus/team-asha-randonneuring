"""BrevetHub live-tracking model layer (rp_live_tracking + rp_live_position).

No real DB (repo convention): monkeypatch brevethub.db.query / query_one /
execute, and run purge against a fake connection so we can assert statement shape.
Contracts (both redteam blockers are first-class here):
  - set/clear/enable are SELF-scoped — the SQL is keyed on the SUBJECT rider_id
    passed in, never a ride-owner id, so a rider can only write their own row,
  - insert coerces bad telemetry to NULL and rejects out-of-range coordinates
    BEFORE any DB write,
  - the enabled-riders filter only returns riders opted in with a Garmin session
    pointed at a ride,
  - the latest-positions query gates on the current per-ride attach
    (t.active_ride_id = p.ride_id) AND enabled tracking, and exposes a display
    name (never the raw email column).
"""
from unittest.mock import patch

from brevethub import models


# --------------------------------------------------------------------------- #
# get_enabled_live_tracking_rp — the poll cron's rider set
# --------------------------------------------------------------------------- #
def test_enabled_live_tracking_filters_opted_in_riders_with_a_ride():
    rows = [{'rider_id': 7, 'garmin_session_url': 'u', 'garmin_session_token': 't',
             'active_ride_id': 3}]
    with patch('brevethub.db.query', return_value=rows) as q:
        out = models.get_enabled_live_tracking_rp()
    assert out == rows
    sql = q.call_args.args[0]
    assert 'rp_live_tracking' in sql
    assert 'enabled = TRUE' in sql
    assert 'garmin_session_token IS NOT NULL' in sql
    assert 'active_ride_id IS NOT NULL' in sql


# --------------------------------------------------------------------------- #
# insert_live_position_rp — telemetry persistence + coord validation
# --------------------------------------------------------------------------- #
def test_insert_position_persists_full_telemetry():
    with patch('brevethub.db.execute') as ex:
        ok = models.insert_live_position_rp(
            rider_id=7, lat=37.77, lng=-122.41,
            recorded_at='2026-07-20T06:05:00+00:00', source='garmin',
            accuracy=5.0, speed=8.3, heart_rate=142, power=210, cadence=88,
            ride_id=3)
    assert ok is True
    sql, params = ex.call_args.args[0], ex.call_args.args[1]
    assert 'INSERT INTO rp_live_position' in sql
    # rider_id, ride_id, lat, lng, accuracy, recorded_at, source, speed, hr, power, cadence
    assert params[0] == 7 and params[1] == 3
    assert params[2] == 37.77 and params[3] == -122.41
    assert params[6] == 'garmin'
    assert params[7] == 8.3 and params[8] == 142 and params[9] == 210 and params[10] == 88


def test_insert_position_coerces_bad_telemetry_to_null():
    with patch('brevethub.db.execute') as ex:
        ok = models.insert_live_position_rp(
            rider_id=7, lat=1.0, lng=2.0, recorded_at=None, source='garmin',
            speed='fast', heart_rate='n/a', power=None, cadence='', ride_id=3)
    assert ok is True
    params = ex.call_args.args[1]
    assert params[7] is None and params[8] is None and params[9] is None and params[10] is None


def test_insert_position_rejects_out_of_range_coords_without_db():
    with patch('brevethub.db.execute') as ex:
        ok = models.insert_live_position_rp(
            rider_id=7, lat=200.0, lng=-122.41, recorded_at=None, source='garmin',
            ride_id=3)
    assert ok is False
    ex.assert_not_called()   # never touches the DB on invalid coordinates


def test_insert_position_rejects_non_numeric_coords():
    with patch('brevethub.db.execute') as ex:
        ok = models.insert_live_position_rp(
            rider_id=7, lat='nope', lng=2.0, recorded_at=None, source='garmin')
    assert ok is False
    ex.assert_not_called()


# --------------------------------------------------------------------------- #
# Self-scoped writes — the subject rider_id is always the one passed in
# --------------------------------------------------------------------------- #
def test_upsert_enable_is_self_scoped():
    with patch('brevethub.db.execute') as ex:
        ok = models.upsert_rider_live_tracking_rp(rider_id=7, enabled=True)
    assert ok is True
    sql, params = ex.call_args.args[0], ex.call_args.args[1]
    assert 'INSERT INTO rp_live_tracking' in sql and 'ON CONFLICT (rider_id)' in sql
    assert params[0] == 7 and params[1] is True


def test_set_ride_garmin_writes_subject_rider_row():
    with patch('brevethub.db.execute') as ex:
        ok = models.set_ride_garmin_rp(
            rider_id=7, ride_id=3, session_url='https://livetrack.garmin.com/session/A/token/B',
            session_token='B')
    assert ok is True
    sql, params = ex.call_args.args[0], ex.call_args.args[1]
    assert 'INSERT INTO rp_live_tracking' in sql and 'ON CONFLICT (rider_id)' in sql
    # subject rider first, active_ride_id last — never a ride-owner id
    assert params[0] == 7 and params[3] == 3
    assert params[1] == 'https://livetrack.garmin.com/session/A/token/B'
    assert params[2] == 'B'


def test_clear_ride_garmin_is_self_scoped_to_this_ride():
    with patch('brevethub.db.execute') as ex:
        ok = models.clear_ride_garmin_rp(rider_id=7, ride_id=3)
    assert ok is True
    sql, params = ex.call_args.args[0], ex.call_args.args[1]
    assert 'UPDATE rp_live_tracking' in sql
    assert 'WHERE rider_id = %s AND active_ride_id = %s' in sql
    assert params == (7, 3)   # only the session rider's own row, only for this ride


def test_set_active_ride_clears_stale_garmin_on_ride_change():
    # Council fix: a beacon retargeting active_ride_id must null a Garmin session
    # registered for a different ride, or the cron would poll it and mis-tag its
    # points to the new ride. The clear is conditional on the ride actually moving.
    with patch('brevethub.db.execute') as ex:
        ok = models.set_active_ride_rp(rider_id=7, ride_id=3)
    assert ok is True
    sql, params = ex.call_args.args[0], ex.call_args.args[1]
    assert 'INSERT INTO rp_live_tracking' in sql and 'ON CONFLICT (rider_id)' in sql
    # both Garmin fields are conditionally nulled when the active ride changes
    assert 'garmin_session_token = CASE' in sql
    assert 'garmin_session_url = CASE' in sql
    assert 'COALESCE(rp_live_tracking.active_ride_id, -1) <> EXCLUDED.active_ride_id' in sql
    assert 'THEN NULL' in sql
    assert params == (7, 3)


def test_get_live_tracking_reads_own_row():
    with patch('brevethub.db.query_one', return_value={'rider_id': 7, 'enabled': True}) as q:
        out = models.get_live_tracking_rp(7)
    assert out['rider_id'] == 7
    assert q.call_args.args[1] == (7,)


# --------------------------------------------------------------------------- #
# get_live_positions_rp — named + telemetry, gated by the per-ride attach
# --------------------------------------------------------------------------- #
def test_live_positions_query_is_named_and_ride_gated():
    rows = [{'rider_id': 7, 'name': 'alice', 'lat': 1.0, 'lng': 2.0,
             'recorded_at': None, 'speed': None, 'heart_rate': None,
             'power': None, 'cadence': None, 'source': 'garmin'}]
    with patch('brevethub.db.query', return_value=rows) as q:
        out = models.get_live_positions_rp(3, 'since-cutoff')
    assert out == rows
    sql, params = q.call_args.args[0], q.call_args.args[1]
    assert 'JOIN rp_live_tracking t' in sql
    assert 't.enabled = TRUE' in sql
    assert 'p.ride_id = %s' in sql          # points are tagged to this ride
    assert 't.active_ride_id = p.ride_id' in sql  # rider is still attached here
    assert 'split_part(r.email' in sql      # display name derived, raw email not exposed
    assert 'AS name' in sql
    assert 'email' not in [k for k in rows[0].keys()]  # model never returns the email column
    assert params == (3, 'since-cutoff')


def test_last_position_recorded_at_is_per_ride():
    with patch('brevethub.db.query_one', return_value={'last_at': 'ts'}) as q:
        out = models.get_last_position_recorded_at_rp(7, 3)
    assert out == 'ts'
    assert q.call_args.args[1] == (7, 3)


# --------------------------------------------------------------------------- #
# purge_old_positions_rp — cursor rowcount
# --------------------------------------------------------------------------- #
class _FakeCursor:
    rowcount = 4
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def execute(self, sql, params=None):
        self.sql = sql


class _FakeConn:
    def __init__(self):
        self.cur = _FakeCursor()
        self.committed = 0
    def cursor(self, cursor_factory=None):
        return self.cur
    def commit(self):
        self.committed += 1
    def rollback(self):
        pass


def test_purge_returns_deleted_count():
    conn = _FakeConn()
    with patch('brevethub.db.get_db', return_value=conn):
        deleted = models.purge_old_positions_rp(7)
    assert deleted == 4
    assert conn.committed == 1
    assert 'DELETE FROM rp_live_position' in conn.cur.sql
