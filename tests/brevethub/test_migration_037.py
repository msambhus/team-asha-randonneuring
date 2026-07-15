"""Migration 037 — the brevet-calendar cache + rider-participation tables.

Like the 033/035/036 migration tests, this is a STATIC SQL contract check (no DB):
it proves the mission-required properties the Python/model scanners can't see,
because they read model SQL, not migration files. It asserts migration 037:
  - parses as valid SQL,
  - creates rp_brevet_event and rp_event_signup via CREATE TABLE IF NOT EXISTS
    (additive + idempotent),
  - guards every CREATE INDEX with IF NOT EXISTS and contains no DROP and no
    destructive ALTER (strictly additive),
  - touches ONLY rp_* tables/references (the isolation invariant, on the SQL itself).
"""
import os
import re

import sqlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATION_PATH = os.path.join(REPO_ROOT, 'migrations', '037_brevethub_brevet_calendar.sql')


def _sql():
    with open(MIGRATION_PATH, 'r', encoding='utf-8') as fh:
        return fh.read()


def test_migration_exists():
    assert os.path.exists(MIGRATION_PATH), "migration 037 is missing"


def test_migration_parses():
    statements = [s for s in sqlparse.parse(_sql()) if s.token_first(skip_cm=True)]
    assert statements, "migration 037 parsed to zero statements"


def test_migration_creates_calendar_tables():
    """Both the event cache and the participation table are created additively."""
    sql = _sql()
    assert re.search(
        r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+rp_brevet_event',
        sql, re.IGNORECASE), "migration 037 must CREATE TABLE IF NOT EXISTS rp_brevet_event"
    assert re.search(
        r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+rp_event_signup',
        sql, re.IGNORECASE), "migration 037 must CREATE TABLE IF NOT EXISTS rp_event_signup"


def test_migration_is_idempotent():
    """Re-applying must be safe: IF NOT EXISTS guards every CREATE TABLE / CREATE
    INDEX / ADD COLUMN, with no DROP and no destructive ALTER anywhere."""
    sql = _sql()
    upper = sql.upper()
    assert 'DROP' not in upper, "migration 037 must be strictly additive (no DROP)"

    # Every CREATE TABLE is guarded.
    for m in re.finditer(r'CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "a CREATE TABLE in migration 037 lacks IF NOT EXISTS"
    # Every CREATE INDEX is guarded.
    for m in re.finditer(r'CREATE\s+INDEX\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "a CREATE INDEX in migration 037 lacks IF NOT EXISTS"
    # Every ADD COLUMN (if any) is guarded.
    for m in re.finditer(r'ADD\s+COLUMN\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "an ADD COLUMN in migration 037 lacks IF NOT EXISTS"


def test_migration_touches_only_rp_tables():
    """Every table/reference the migration names is rp_-prefixed, so applying it can
    never alter a Team Asha table (reuses the 033/035/036 scanner shape)."""
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
    assert seen_any, "no table references found in migration 037 — scan is broken"
    assert not offenders, f"migration 037 touches non-rp_ tables: {sorted(offenders)}"
