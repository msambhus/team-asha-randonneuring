"""Migration 046 — the per-rider Eddington cache columns.

Like the 037/042/045 migration tests, this is a STATIC SQL contract check (no DB):
it proves the mission-required properties the Python/model scanners cannot see,
because they read model SQL, not migration files. It asserts migration 046:
  - parses as valid SQL,
  - adds eddington_miles, eddington_km, eddington_calculated_at, each via
    ALTER TABLE ... ADD COLUMN IF NOT EXISTS (additive + idempotent),
  - contains no DROP and no destructive ALTER COLUMN (strictly additive),
  - touches ONLY rp_* tables/references (the isolation invariant, on the SQL itself).
"""
import os
import re

import sqlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATION_PATH = os.path.join(REPO_ROOT, 'migrations', '046_brevethub_eddington.sql')

# The three cache columns the migration must add to rp_rider.
_COLUMNS = ('eddington_miles', 'eddington_km', 'eddington_calculated_at')


def _sql():
    with open(MIGRATION_PATH, 'r', encoding='utf-8') as fh:
        return fh.read()


def test_migration_exists():
    assert os.path.exists(MIGRATION_PATH), "migration 046 is missing"


def test_migration_parses():
    statements = [s for s in sqlparse.parse(_sql()) if s.token_first(skip_cm=True)]
    assert statements, "migration 046 parsed to zero statements"


def test_migration_adds_all_three_columns_additively():
    """Each cache column is added with ADD COLUMN IF NOT EXISTS on rp_rider."""
    sql = _sql()
    for col in _COLUMNS:
        assert re.search(
            r'ALTER\s+TABLE\s+rp_rider\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+' + col,
            sql, re.IGNORECASE), (
            "migration 046 must ADD COLUMN IF NOT EXISTS %s on rp_rider" % col)


def test_migration_is_additive():
    """Re-applying must be safe: no DROP, no destructive ALTER COLUMN; every
    ADD COLUMN is guarded with IF NOT EXISTS."""
    sql = _sql()
    upper = sql.upper()
    assert 'DROP' not in upper, "migration 046 must be strictly additive (no DROP)"
    assert 'ALTER COLUMN' not in upper, "migration 046 must not ALTER COLUMN (destructive)"
    for m in re.finditer(r'ADD\s+COLUMN\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "an ADD COLUMN in migration 046 lacks IF NOT EXISTS"


def test_migration_touches_only_rp_tables():
    """Every table the migration names is rp_-prefixed, so applying it can never
    alter a parent-app table (reuses the 045 scanner shape)."""
    sql = _sql()
    patterns = [
        r'ALTER\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)',
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)',
        r'INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)',
        r'UPDATE\s+([A-Za-z_][A-Za-z0-9_]*)',
        r'REFERENCES\s+([A-Za-z_][A-Za-z0-9_]*)',
    ]
    offenders = set()
    seen_any = False
    for pat in patterns:
        for name in re.findall(pat, sql, re.IGNORECASE):
            seen_any = True
            if not name.lower().startswith('rp_'):
                offenders.add(name.lower())
    assert seen_any, "no table references found in migration 046 — scan is broken"
    assert not offenders, f"migration 046 touches non-rp_ tables: {sorted(offenders)}"
