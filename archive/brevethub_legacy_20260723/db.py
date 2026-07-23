"""BrevetHub database access — a per-request psycopg2 connection plus small
query helpers.

Isolated from Team Asha's `db.py`: BrevetHub opens its own connection (same
Supabase DATABASE_URL) and every query in `models.py` runs through the helpers
here. All queries target `rp_*` tables only.
"""
import psycopg2
import psycopg2.extras
from flask import g, current_app


def get_db():
    if 'brevethub_db' not in g:
        g.brevethub_db = psycopg2.connect(current_app.config['DATABASE_URL'])
        g.brevethub_db.autocommit = False
    return g.brevethub_db


def close_db(e=None):
    conn = g.pop('brevethub_db', None)
    if conn is not None and not conn.closed:
        conn.close()


def query(sql, params=None):
    """Run a SELECT and return all rows as a list of RealDict rows.

    Rolls back on error: the connection is per-request with autocommit off, so a
    failed SELECT leaves the transaction ABORTED and every later query on the
    same request fails with InFailedSqlTransaction. Rolling back here lets callers
    that catch the error (fail-soft reads) keep the request's connection usable."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    except Exception:
        conn.rollback()
        raise


def query_one(sql, params=None):
    """Run a SELECT and return the first row (or None). Rolls back on error so a
    failed read cannot poison the rest of the request's transaction (see query)."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return cur.fetchone()
    except Exception:
        conn.rollback()
        raise


def execute(sql, params=None, returning=False):
    """Run an INSERT/UPDATE/DELETE, commit, and optionally return the first row
    (for `RETURNING` clauses). Rolls back on error."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            row = cur.fetchone() if returning else None
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise


def init_app(app):
    app.teardown_appcontext(close_db)
