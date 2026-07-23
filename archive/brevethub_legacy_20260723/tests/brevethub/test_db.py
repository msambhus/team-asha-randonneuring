"""Unit tests for brevethub.db query helpers — specifically that a failed SELECT
rolls back so it cannot poison the rest of a per-request transaction."""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from brevethub import db


def _fake_conn(execute_side_effect=None, fetchall=None, fetchone=None):
    """Build a mock psycopg2 connection whose cursor() is a context manager."""
    conn = MagicMock(name='conn')
    cur = MagicMock(name='cursor')
    if execute_side_effect is not None:
        cur.execute.side_effect = execute_side_effect
    cur.fetchall.return_value = fetchall
    cur.fetchone.return_value = fetchone

    @contextmanager
    def _cursor(*args, **kwargs):
        yield cur

    conn.cursor.side_effect = _cursor
    return conn, cur


def test_query_rolls_back_and_reraises_on_error():
    conn, _cur = _fake_conn(execute_side_effect=RuntimeError('relation does not exist'))
    with patch('brevethub.db.get_db', return_value=conn):
        with pytest.raises(RuntimeError):
            db.query('SELECT 1')
    conn.rollback.assert_called_once()


def test_query_one_rolls_back_and_reraises_on_error():
    conn, _cur = _fake_conn(execute_side_effect=RuntimeError('boom'))
    with patch('brevethub.db.get_db', return_value=conn):
        with pytest.raises(RuntimeError):
            db.query_one('SELECT 1')
    conn.rollback.assert_called_once()


def test_query_success_does_not_roll_back():
    rows = [{'id': 1}, {'id': 2}]
    conn, _cur = _fake_conn(fetchall=rows)
    with patch('brevethub.db.get_db', return_value=conn):
        assert db.query('SELECT id FROM t') == rows
    conn.rollback.assert_not_called()


def test_query_one_success_does_not_roll_back():
    conn, _cur = _fake_conn(fetchone={'id': 7})
    with patch('brevethub.db.get_db', return_value=conn):
        assert db.query_one('SELECT id FROM t WHERE id=%s', (7,)) == {'id': 7}
    conn.rollback.assert_not_called()
