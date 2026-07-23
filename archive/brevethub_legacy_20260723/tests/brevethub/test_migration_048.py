"""Migration 048 — the strategy_pace + is_public columns on rp_brevet_plan.

Like the 045/046/047 migration tests, this is a STATIC SQL contract check (no DB): it
proves the mission-required properties the Python/model scanners can't see, because
they read model SQL, not migration files. It asserts migration 048:
  - parses as valid SQL,
  - adds `strategy_pace TEXT` via ALTER TABLE ... ADD COLUMN IF NOT EXISTS (additive +
    idempotent; nullable, so legacy rows keep NULL = no chosen strategy),
  - adds `is_public BOOLEAN NOT NULL DEFAULT FALSE` via ADD COLUMN IF NOT EXISTS
    (additive + idempotent; every legacy row backfills to private),
  - is NON-DESTRUCTIVE: no DROP TABLE, no DROP COLUMN, no DROP CONSTRAINT, no ALTER
    COLUMN,
  - touches ONLY rp_* tables/references (the isolation invariant, on the SQL itself).
"""
import os
import re

import sqlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, 'migrations', '048_brevethub_brevet_strategy.sql')


def _sql():
    with open(MIGRATION_PATH, 'r', encoding='utf-8') as fh:
        return fh.read()


def _sql_code():
    """The migration's executable SQL with `-- ...` comments stripped, so the naive
    property scanners below match statements rather than doc prose (e.g. a comment that
    says "references only rp_* tables" or "ADD COLUMN IF NOT EXISTS")."""
    return sqlparse.format(_sql(), strip_comments=True)


def test_migration_exists():
    assert os.path.exists(MIGRATION_PATH), "migration 048 is missing"


def test_migration_parses():
    statements = [s for s in sqlparse.parse(_sql()) if s.token_first(skip_cm=True)]
    assert statements, "migration 048 parsed to zero statements"


def test_migration_adds_strategy_pace_additively():
    """strategy_pace is added with ADD COLUMN IF NOT EXISTS, TEXT, nullable (no default),
    so legacy rows keep NULL = no chosen strategy."""
    sql = _sql()
    assert re.search(
        r"ALTER\s+TABLE\s+rp_brevet_plan\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+"
        r"strategy_pace\s+TEXT",
        sql, re.IGNORECASE), (
        "migration 048 must ADD COLUMN IF NOT EXISTS strategy_pace TEXT on rp_brevet_plan")


def test_migration_adds_is_public_additively():
    """is_public is added with ADD COLUMN IF NOT EXISTS, NOT NULL DEFAULT FALSE, so every
    existing row backfills to private."""
    sql = _sql()
    assert re.search(
        r"ALTER\s+TABLE\s+rp_brevet_plan\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+"
        r"is_public\s+BOOLEAN\s+NOT\s+NULL\s+DEFAULT\s+FALSE",
        sql, re.IGNORECASE), (
        "migration 048 must ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL "
        "DEFAULT FALSE on rp_brevet_plan")


def test_migration_is_non_destructive():
    """No table/column/constraint drops and no ALTER COLUMN — purely additive."""
    code = _sql_code().upper()
    for forbidden in ('DROP TABLE', 'DROP COLUMN', 'DROP CONSTRAINT', 'ALTER COLUMN'):
        assert forbidden not in code, (
            f"migration 048 must be non-destructive; found {forbidden!r}")


def test_migration_touches_only_rp_tables():
    """Every table an ALTER/INDEX/FROM/JOIN/INTO/UPDATE names is rp_-prefixed."""
    code = _sql_code()
    refs = re.findall(
        r'\b(?:ALTER\s+TABLE|INDEX\s+\w+\s+ON|FROM|JOIN|INTO|UPDATE)\s+'
        r'("?[A-Za-z_][A-Za-z0-9_]*"?)',
        code, re.IGNORECASE)
    assert refs, "no table references found in migration 048 — scan is broken"
    offenders = sorted({r.strip('"').lower() for r in refs
                        if not r.strip('"').lower().startswith('rp_')})
    assert not offenders, f"migration 048 references non-rp_ tables: {offenders}"
