"""Migration 041 — the BrevetHub real ride-plan tables.

Like the 033/…/039/040 migration tests, this is a STATIC SQL contract check (no DB):
it proves the mission-required properties the Python/model scanners can't see. It
asserts migration 041:
  - parses as valid SQL,
  - creates rp_brevet_route_plan and rp_brevet_route_plan_stop via
    CREATE TABLE IF NOT EXISTS (additive + idempotent),
  - the plan table carries UNIQUE (event_id) (one real plan per brevet -> idempotent
    upsert) and a UNIQUE slug, plus the TA-mirroring columns,
  - the stop table carries the TA-mirroring per-control columns,
  - guards every CREATE INDEX with IF NOT EXISTS and contains no DROP / destructive
    ALTER (strictly additive),
  - touches ONLY rp_* tables/references (the isolation invariant, on the SQL itself).
"""
import os
import re

import sqlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATION_PATH = os.path.join(REPO_ROOT, 'migrations',
                              '041_brevethub_brevet_route_plan.sql')

_PLAN_COLUMNS = [
    'id', 'club_id', 'name', 'slug', 'total_distance_miles', 'total_elevation_ft',
    'rwgps_url', 'rwgps_route_id', 'distance_km', 'cutoff_hours', 'start_time',
    'avg_moving_speed', 'avg_elapsed_speed', 'total_moving_time_min',
    'total_elapsed_time_min', 'total_break_time_min', 'overall_ft_per_mile',
    'created_at',
]
_STOP_COLUMNS = [
    'ride_plan_id', 'stop_order', 'location', 'stop_type', 'distance_miles',
    'elevation_gain', 'segment_time_min', 'notes', 'seg_dist', 'ft_per_mi',
    'avg_speed', 'cum_time_min', 'bookend_time_min', 'time_bank_min',
    'difficulty_score',
]


def _sql():
    with open(MIGRATION_PATH, 'r', encoding='utf-8') as fh:
        return fh.read()


def test_migration_exists():
    assert os.path.exists(MIGRATION_PATH), "migration 041 is missing"


def test_migration_parses():
    statements = [s for s in sqlparse.parse(_sql()) if s.token_first(skip_cm=True)]
    assert statements, "migration 041 parsed to zero statements"


def test_migration_creates_both_tables():
    sql = _sql()
    for table in ('rp_brevet_route_plan', 'rp_brevet_route_plan_stop'):
        assert re.search(
            rf'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+{table}\b',
            sql, re.IGNORECASE), f"migration 041 must CREATE TABLE IF NOT EXISTS {table}"


def test_plan_table_has_unique_event_and_slug():
    sql = _sql()
    assert re.search(r'UNIQUE\s*\(\s*event_id\s*\)', sql, re.IGNORECASE), \
        "migration 041 must UNIQUE (event_id) — one real plan per brevet"
    assert re.search(r'slug\s+TEXT\s+UNIQUE', sql, re.IGNORECASE), \
        "migration 041 slug must be UNIQUE"


def test_plan_table_mirrors_ta_columns():
    sql = _sql().lower()
    for col in _PLAN_COLUMNS:
        assert col in sql, f"migration 041 plan table missing column {col}"


def test_stop_table_mirrors_ta_columns():
    sql = _sql().lower()
    for col in _STOP_COLUMNS:
        assert col in sql, f"migration 041 stop table missing column {col}"


def test_migration_is_idempotent():
    sql = _sql()
    assert 'DROP' not in sql.upper(), "migration 041 must be strictly additive (no DROP)"
    for m in re.finditer(r'CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "a CREATE TABLE in migration 041 lacks IF NOT EXISTS"
    for m in re.finditer(r'CREATE\s+INDEX\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "a CREATE INDEX in migration 041 lacks IF NOT EXISTS"


def test_migration_touches_only_rp_tables():
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
    assert seen_any, "no table references found in migration 041 — scan is broken"
    assert not offenders, f"migration 041 touches non-rp_ tables: {sorted(offenders)}"
