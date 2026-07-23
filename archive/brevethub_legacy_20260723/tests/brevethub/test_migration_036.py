"""Migration 036 — adds rp_ride.rider_id (the live-ride owner) additively.

Like the 033/035 migration tests, this is a STATIC SQL contract check (no DB): it
proves the mission-required properties the Python/model scanners can't see, because
they read model SQL, not migration files. It asserts migration 036:
  - parses as valid SQL,
  - adds rp_ride.rider_id via ALTER TABLE … ADD COLUMN IF NOT EXISTS (additive),
  - guards BOTH the column add and the index create with IF NOT EXISTS (idempotent)
    and contains no DROP (strictly additive),
  - touches ONLY rp_* tables/references (the isolation invariant, on the SQL itself).
"""
import os
import re

import sqlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATION_PATH = os.path.join(REPO_ROOT, 'migrations', '036_brevethub_live_ride_owner.sql')


def _sql():
    with open(MIGRATION_PATH, 'r', encoding='utf-8') as fh:
        return fh.read()


def test_migration_exists():
    assert os.path.exists(MIGRATION_PATH), "migration 036 is missing"


def test_migration_parses():
    statements = [s for s in sqlparse.parse(_sql()) if s.token_first(skip_cm=True)]
    assert statements, "migration 036 parsed to zero statements"


def test_migration_adds_rider_id_column():
    """The ownership column the position-POST + create paths gate on is created,
    and it's additive (ALTER … ADD COLUMN, never a destructive change)."""
    sql = _sql()
    assert re.search(
        r'ALTER\s+TABLE\s+rp_ride\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+rider_id',
        sql, re.IGNORECASE), "migration 036 must ADD COLUMN IF NOT EXISTS rider_id on rp_ride"


def test_migration_is_idempotent():
    """Re-applying must be safe: IF NOT EXISTS guards BOTH the column and the index,
    with no bare ADD COLUMN / CREATE INDEX, and no DROP anywhere."""
    sql = _sql()
    upper = sql.upper()
    assert 'DROP' not in upper, "migration 036 must be strictly additive (no DROP)"

    # Every ADD COLUMN is guarded.
    for m in re.finditer(r'ADD\s+COLUMN\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "an ADD COLUMN in migration 036 lacks IF NOT EXISTS"
    # Every CREATE INDEX is guarded.
    for m in re.finditer(r'CREATE\s+INDEX\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "a CREATE INDEX in migration 036 lacks IF NOT EXISTS"


def test_migration_touches_only_rp_tables():
    """Every table/reference the migration names is rp_-prefixed, so applying it can
    never alter a Team Asha table (reuses the 033/035 scanner shape)."""
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
    assert seen_any, "no table references found in migration 036 — scan is broken"
    assert not offenders, f"migration 036 touches non-rp_ tables: {sorted(offenders)}"
