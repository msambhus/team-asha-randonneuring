"""Migration 056 — the per-event live-ride link column (Closes #538).

Static SQL contract check (no DB), like the sibling migration tests. Asserts
migration 056:
  - parses as valid SQL,
  - adds rp_ride.event_id via ADD COLUMN IF NOT EXISTS (additive + idempotent),
  - references rp_brevet_event for the event FK,
  - contains no DROP / destructive ALTER and guards its index with IF NOT EXISTS,
  - touches ONLY rp_* tables/references (the isolation invariant, on the SQL itself).
"""
import os
import re

import sqlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATION_PATH = os.path.join(REPO_ROOT, 'migrations',
                              '056_brevethub_event_live_link.sql')


def _sql():
    with open(MIGRATION_PATH, 'r', encoding='utf-8') as fh:
        return fh.read()


def test_migration_exists():
    assert os.path.exists(MIGRATION_PATH), "migration 056 is missing"


def test_migration_parses():
    statements = [s for s in sqlparse.parse(_sql()) if s.token_first(skip_cm=True)]
    assert statements, "migration 056 parsed to zero statements"


def test_adds_event_id_column_idempotently():
    sql = _sql()
    assert re.search(
        r'ALTER\s+TABLE\s+rp_ride\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+event_id',
        sql, re.IGNORECASE), \
        "migration 056 must ADD COLUMN IF NOT EXISTS rp_ride.event_id"


def test_event_id_references_brevet_event():
    sql = _sql()
    assert re.search(r'event_id\s+INTEGER\s+REFERENCES\s+rp_brevet_event',
                     sql, re.IGNORECASE), \
        "event_id must be an FK to rp_brevet_event(id)"


def test_migration_is_idempotent():
    sql = _sql()
    assert 'DROP' not in sql.upper(), "migration 056 must be strictly additive (no DROP)"
    for m in re.finditer(r'ADD\s+COLUMN\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "an ADD COLUMN in migration 056 lacks IF NOT EXISTS"
    for m in re.finditer(r'CREATE\s+INDEX\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "a CREATE INDEX in migration 056 lacks IF NOT EXISTS"


def test_migration_touches_only_rp_tables():
    sql = _sql()
    patterns = [
        r'ALTER\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)',
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
    assert seen_any, "no table references found in migration 056 — scan is broken"
    assert not offenders, f"migration 056 touches non-rp_ tables: {sorted(offenders)}"
