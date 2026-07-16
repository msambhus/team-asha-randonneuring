"""Migration 038 — the BrevetHub brevet-weather point-forecast cache table.

Like the 033/035/036/037 migration tests, this is a STATIC SQL contract check (no
DB): it proves the mission-required properties the Python/model scanners can't see,
because they read model SQL, not migration files. It asserts migration 038:
  - parses as valid SQL,
  - creates rp_brevet_weather via CREATE TABLE IF NOT EXISTS (additive + idempotent),
  - guards every CREATE INDEX with IF NOT EXISTS and contains no DROP and no
    destructive ALTER (strictly additive),
  - touches ONLY rp_* tables/references (the isolation invariant, on the SQL itself).
"""
import os
import re

import sqlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATION_PATH = os.path.join(REPO_ROOT, 'migrations', '038_brevethub_brevet_weather.sql')


def _sql():
    with open(MIGRATION_PATH, 'r', encoding='utf-8') as fh:
        return fh.read()


def test_migration_exists():
    assert os.path.exists(MIGRATION_PATH), "migration 038 is missing"


def test_migration_parses():
    statements = [s for s in sqlparse.parse(_sql()) if s.token_first(skip_cm=True)]
    assert statements, "migration 038 parsed to zero statements"


def test_migration_creates_weather_cache_table():
    sql = _sql()
    assert re.search(
        r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+rp_brevet_weather',
        sql, re.IGNORECASE), "migration 038 must CREATE TABLE IF NOT EXISTS rp_brevet_weather"


def test_migration_has_unique_event_date_key():
    """The (event_id, forecast_date) unique key backs the idempotent cron upsert."""
    sql = _sql()
    assert re.search(
        r'UNIQUE\s*\(\s*event_id\s*,\s*forecast_date\s*\)',
        sql, re.IGNORECASE), "migration 038 must UNIQUE (event_id, forecast_date)"


def test_migration_is_idempotent():
    """Re-applying must be safe: IF NOT EXISTS guards every CREATE TABLE / CREATE
    INDEX / ADD COLUMN, with no DROP and no destructive ALTER anywhere."""
    sql = _sql()
    upper = sql.upper()
    assert 'DROP' not in upper, "migration 038 must be strictly additive (no DROP)"

    for m in re.finditer(r'CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "a CREATE TABLE in migration 038 lacks IF NOT EXISTS"
    for m in re.finditer(r'CREATE\s+INDEX\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "a CREATE INDEX in migration 038 lacks IF NOT EXISTS"
    for m in re.finditer(r'ADD\s+COLUMN\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "an ADD COLUMN in migration 038 lacks IF NOT EXISTS"


def test_migration_touches_only_rp_tables():
    """Every table/reference the migration names is rp_-prefixed, so applying it can
    never alter a Team Asha table (reuses the 033/035/036/037 scanner shape)."""
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
    assert seen_any, "no table references found in migration 038 — scan is broken"
    assert not offenders, f"migration 038 touches non-rp_ tables: {sorted(offenders)}"
