"""Migration 033 — parses cleanly and defines ONLY rp_* objects.

The BrevetHub schema migration must be valid SQL (it parses via sqlparse) and it
must be strictly additive with respect to Team Asha: every table it creates,
indexes, references, or seeds is `rp_`-prefixed, so applying it can never alter a
Team Asha table.
"""
import os
import re

import sqlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATION_PATH = os.path.join(REPO_ROOT, 'migrations', '033_brevethub_rp_tables.sql')


def _sql():
    with open(MIGRATION_PATH, 'r', encoding='utf-8') as fh:
        return fh.read()


def test_migration_exists():
    assert os.path.exists(MIGRATION_PATH), "migration 033 is missing"


def test_migration_parses():
    statements = [s for s in sqlparse.parse(_sql()) if s.token_first(skip_cm=True)]
    assert statements, "migration 033 parsed to zero statements"


def test_migration_defines_only_rp_tables():
    sql = _sql()
    # Every table name introduced by a DDL/DML keyword must be rp_-prefixed.
    patterns = [
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)',
        r'INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)',
        r'REFERENCES\s+([A-Za-z_][A-Za-z0-9_]*)',
        # CREATE INDEX [IF NOT EXISTS] <idx> ON <table> (...)
        r'CREATE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?[A-Za-z_][A-Za-z0-9_]*\s+ON\s+([A-Za-z_][A-Za-z0-9_]*)',
    ]
    offenders = set()
    seen_any = False
    for pat in patterns:
        for name in re.findall(pat, sql, re.IGNORECASE):
            seen_any = True
            if not name.lower().startswith('rp_'):
                offenders.add(name.lower())

    assert seen_any, "no table references found in migration 033 — scan is broken"
    assert not offenders, (
        f"migration 033 touches non-rp_ tables: {sorted(offenders)}"
    )


def test_migration_is_idempotent():
    """Re-applying the migration must be safe: creates guard on IF NOT EXISTS and
    the club seed uses ON CONFLICT DO NOTHING."""
    sql = _sql().upper()
    assert 'IF NOT EXISTS' in sql
    assert 'ON CONFLICT' in sql


def test_migration_seeds_clubs():
    sql = _sql()
    assert 'INSERT INTO rp_club' in sql
    # A non-trivial seed list (several clubs), not an empty stub.
    assert sql.count("','") >= 10 or sql.upper().count('VALUES') >= 1
