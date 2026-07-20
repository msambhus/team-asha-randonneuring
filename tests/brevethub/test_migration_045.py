"""Migration 045 — the sign-up lifecycle result column + status CHECK constraint.

Like the 033/037/042 migration tests, this is a STATIC SQL contract check (no DB):
it proves the mission-required properties the Python/model scanners can't see,
because they read model SQL, not migration files. It asserts migration 045:
  - parses as valid SQL,
  - adds finish_time via ALTER TABLE ... ADD COLUMN IF NOT EXISTS (additive +
    idempotent),
  - adds a CHECK constraint naming ALL EIGHT lowercase lifecycle status values,
    guarded by a re-runnable pg_constraint existence check (idempotent — Postgres
    has no ADD CONSTRAINT IF NOT EXISTS),
  - contains no DROP and no destructive ALTER (strictly additive),
  - touches ONLY rp_* tables/references (the isolation invariant, on the SQL itself).
"""
import os
import re

import sqlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATION_PATH = os.path.join(REPO_ROOT, 'migrations', '045_brevethub_signup_lifecycle.sql')

# The eight lowercase lifecycle status values the CHECK constraint must pin.
_STATUS_VALUES = ('interested', 'maybe', 'going', 'withdraw',
                  'finished', 'dnf', 'dns', 'otl')


def _sql():
    with open(MIGRATION_PATH, 'r', encoding='utf-8') as fh:
        return fh.read()


def test_migration_exists():
    assert os.path.exists(MIGRATION_PATH), "migration 045 is missing"


def test_migration_parses():
    statements = [s for s in sqlparse.parse(_sql()) if s.token_first(skip_cm=True)]
    assert statements, "migration 045 parsed to zero statements"


def test_migration_adds_finish_time_column_additively():
    """finish_time is added with ADD COLUMN IF NOT EXISTS on rp_event_signup."""
    sql = _sql()
    assert re.search(
        r'ALTER\s+TABLE\s+rp_event_signup\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+finish_time\s+TEXT',
        sql, re.IGNORECASE), "migration 045 must ADD COLUMN IF NOT EXISTS finish_time TEXT"


def test_migration_check_constraint_covers_all_eight_statuses():
    """The status CHECK constraint pins every lowercase lifecycle value."""
    sql = _sql()
    assert re.search(r'ADD\s+CONSTRAINT\s+rp_event_signup_status_check', sql, re.IGNORECASE), \
        "migration 045 must add the named status CHECK constraint"
    assert re.search(r'CHECK\s*\(\s*status\s+IN', sql, re.IGNORECASE), \
        "migration 045 status constraint must be CHECK (status IN (...))"
    for value in _STATUS_VALUES:
        assert re.search(r"'%s'" % re.escape(value), sql), \
            "migration 045 CHECK constraint is missing status value '%s'" % value


def test_migration_constraint_is_idempotent():
    """The ADD CONSTRAINT is guarded by a pg_constraint existence check so a
    re-apply is a no-op (Postgres has no ADD CONSTRAINT IF NOT EXISTS)."""
    sql = _sql()
    assert re.search(r'DO\s+\$\$', sql, re.IGNORECASE), \
        "migration 045 must wrap the constraint in a re-runnable DO block"
    assert re.search(
        r'IF\s+NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+pg_constraint',
        sql, re.IGNORECASE), "migration 045 must guard the constraint on pg_constraint"


def test_migration_is_additive():
    """Re-applying must be safe: no DROP, no destructive ALTER; the ADD COLUMN is
    guarded with IF NOT EXISTS and the constraint is guarded by the DO block."""
    sql = _sql()
    upper = sql.upper()
    assert 'DROP' not in upper, "migration 045 must be strictly additive (no DROP)"
    assert 'ALTER COLUMN' not in upper, "migration 045 must not ALTER COLUMN (destructive)"
    # Every ADD COLUMN is guarded.
    for m in re.finditer(r'ADD\s+COLUMN\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "an ADD COLUMN in migration 045 lacks IF NOT EXISTS"


def test_migration_touches_only_rp_tables():
    """Every table the migration names is rp_-prefixed, so applying it can never
    alter a parent-app table (reuses the 033/037 scanner shape)."""
    sql = _sql()
    patterns = [
        r'ALTER\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)',
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)',
        r'INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)',
        r'UPDATE\s+([A-Za-z_][A-Za-z0-9_]*)',
        r'REFERENCES\s+([A-Za-z_][A-Za-z0-9_]*)',
        r'CREATE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?[A-Za-z_][A-Za-z0-9_]*\s+ON\s+([A-Za-z_][A-Za-z0-9_]*)',
    ]
    offenders = set()
    seen_any = False
    for pat in patterns:
        for name in re.findall(pat, sql, re.IGNORECASE):
            seen_any = True
            if not name.lower().startswith('rp_'):
                offenders.add(name.lower())
    assert seen_any, "no table references found in migration 045 — scan is broken"
    assert not offenders, f"migration 045 touches non-rp_ tables: {sorted(offenders)}"
