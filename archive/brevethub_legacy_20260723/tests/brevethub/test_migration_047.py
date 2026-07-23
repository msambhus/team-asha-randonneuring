"""Migration 047 — the conservative/aggressive `variant` column + re-keyed uniqueness.

Like the 045/046 migration tests, this is a STATIC SQL contract check (no DB): it
proves the mission-required properties the Python/model scanners can't see, because
they read model SQL, not migration files. It asserts migration 047:
  - parses as valid SQL,
  - adds `variant` via ALTER TABLE ... ADD COLUMN IF NOT EXISTS with NOT NULL
    DEFAULT 'conservative' (additive + idempotent; legacy rows become conservative),
  - swaps UNIQUE(event_id) → UNIQUE(event_id, variant): a guarded
    DROP CONSTRAINT IF EXISTS plus a re-runnable pg_constraint-checked ADD CONSTRAINT,
  - is NON-DESTRUCTIVE: no DROP TABLE, no DROP COLUMN, no ALTER COLUMN — the only DROPs
    are guarded `DROP CONSTRAINT IF EXISTS` swaps,
  - touches ONLY rp_* tables/references (the isolation invariant, on the SQL itself).
"""
import os
import re

import sqlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, 'migrations', '047_brevethub_brevet_route_plan_variant.sql')


def _sql():
    with open(MIGRATION_PATH, 'r', encoding='utf-8') as fh:
        return fh.read()


def _sql_code():
    """The migration's executable SQL with `-- ...` comments stripped, so the
    naive property scanners below match statements rather than doc prose (e.g. a
    comment that says "references only rp_* tables" or "ADD COLUMN IF NOT EXISTS.")."""
    return sqlparse.format(_sql(), strip_comments=True)


def test_migration_exists():
    assert os.path.exists(MIGRATION_PATH), "migration 047 is missing"


def test_migration_parses():
    statements = [s for s in sqlparse.parse(_sql()) if s.token_first(skip_cm=True)]
    assert statements, "migration 047 parsed to zero statements"


def test_migration_adds_variant_column_additively():
    """variant is added with ADD COLUMN IF NOT EXISTS, NOT NULL DEFAULT 'conservative'."""
    sql = _sql()
    assert re.search(
        r"ALTER\s+TABLE\s+rp_brevet_route_plan\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+"
        r"variant\s+TEXT\s+NOT\s+NULL\s+DEFAULT\s+'conservative'",
        sql, re.IGNORECASE), (
        "migration 047 must ADD COLUMN IF NOT EXISTS variant TEXT NOT NULL "
        "DEFAULT 'conservative' on rp_brevet_route_plan")


def test_migration_swaps_unique_to_event_variant():
    """The one-plan-per-event UNIQUE(event_id) is dropped (guarded) and replaced with
    UNIQUE(event_id, variant), guarded by a re-runnable pg_constraint check."""
    sql = _sql()
    assert re.search(
        r'DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+rp_brevet_route_plan_event_id_key',
        sql, re.IGNORECASE), "migration 047 must drop the old UNIQUE(event_id) guardedly"
    assert re.search(
        r'ADD\s+CONSTRAINT\s+\w+\s+UNIQUE\s*\(\s*event_id\s*,\s*variant\s*\)',
        sql, re.IGNORECASE), "migration 047 must add UNIQUE(event_id, variant)"
    assert re.search(r'DO\s+\$\$', sql, re.IGNORECASE), \
        "migration 047 must wrap each ADD CONSTRAINT in a re-runnable DO block"
    assert re.search(
        r'IF\s+NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+pg_constraint',
        sql, re.IGNORECASE), "migration 047 must guard the constraint on pg_constraint"


def test_migration_slug_unique_re_keyed_per_variant():
    """Slug uniqueness is re-keyed per (event_id, variant)."""
    sql = _sql()
    assert re.search(
        r'DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+rp_brevet_route_plan_slug_key',
        sql, re.IGNORECASE), "migration 047 must drop the old global UNIQUE(slug) guardedly"
    assert re.search(
        r'UNIQUE\s*\(\s*event_id\s*,\s*variant\s*,\s*slug\s*\)',
        sql, re.IGNORECASE), "migration 047 must add UNIQUE(event_id, variant, slug)"


def test_migration_is_non_destructive():
    """No table/column drop: the ONLY DROPs are guarded constraint swaps, every
    ADD COLUMN is IF NOT EXISTS, and no ALTER COLUMN appears."""
    sql = _sql_code()
    upper = sql.upper()
    assert 'DROP TABLE' not in upper, "migration 047 must not DROP TABLE"
    assert 'DROP COLUMN' not in upper, "migration 047 must not DROP COLUMN"
    assert 'ALTER COLUMN' not in upper, "migration 047 must not ALTER COLUMN (destructive)"
    # Every DROP present must be a guarded DROP CONSTRAINT IF EXISTS.
    for m in re.finditer(r'DROP\s+(\w+)(\s+IF\s+EXISTS)?', sql, re.IGNORECASE):
        assert m.group(1).upper() == 'CONSTRAINT' and m.group(2), \
            "every DROP in migration 047 must be DROP CONSTRAINT IF EXISTS"
    # Every ADD COLUMN is guarded.
    for m in re.finditer(r'ADD\s+COLUMN\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "an ADD COLUMN in migration 047 lacks IF NOT EXISTS"


def test_migration_touches_only_rp_tables():
    """Every table the migration names is rp_-prefixed (reuses the 045/046 scanner)."""
    sql = _sql_code()
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
    assert seen_any, "no table references found in migration 047 — scan is broken"
    assert not offenders, f"migration 047 touches non-rp_ tables: {sorted(offenders)}"
