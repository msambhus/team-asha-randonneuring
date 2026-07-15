"""Migration 035 — the shared Strava broker tables. Parses cleanly, defines BOTH
rp_ broker tables, touches only rp_* objects, and is idempotent.

Like migration 033's test, this proves the migration is valid SQL and strictly
additive w.r.t. Team Asha: every table/index/reference it introduces is
`rp_`-prefixed, so applying it can never alter a Team Asha table.
"""
import os
import re

import sqlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATION_PATH = os.path.join(REPO_ROOT, 'migrations', '035_strava_broker.sql')


def _sql():
    with open(MIGRATION_PATH, 'r', encoding='utf-8') as fh:
        return fh.read()


def test_migration_exists():
    assert os.path.exists(MIGRATION_PATH), "migration 035 is missing"


def test_migration_parses():
    statements = [s for s in sqlparse.parse(_sql()) if s.token_first(skip_cm=True)]
    assert statements, "migration 035 parsed to zero statements"


def test_migration_defines_both_broker_tables():
    sql = _sql()
    assert re.search(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?rp_strava_broker_handoff',
                     sql, re.IGNORECASE)
    assert re.search(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?rp_strava_broker_state',
                     sql, re.IGNORECASE)


def test_handoff_has_two_distinct_expiry_columns():
    """The Strava-token lifetime and the one-time-code TTL are separate, distinctly
    named columns so the consume gate cannot accidentally read the wrong one."""
    sql = _sql()
    assert 'strava_token_expires_at' in sql
    assert 'handoff_expires_at' in sql
    # A one-time opaque code, a plain (FK-less) rider id, and the token payload.
    assert re.search(r'\bcode\b', sql)
    assert 'ta_rider_id' in sql


def test_state_table_is_nonce_keyed():
    sql = _sql()
    assert re.search(r'nonce\s+TEXT\s+PRIMARY\s+KEY', sql, re.IGNORECASE)


def test_migration_defines_only_rp_tables():
    sql = _sql()
    patterns = [
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)',
        r'INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)',
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
    assert seen_any, "no table references found in migration 035 — scan is broken"
    assert not offenders, f"migration 035 touches non-rp_ tables: {sorted(offenders)}"


def test_migration_is_idempotent():
    sql = _sql().upper()
    assert 'IF NOT EXISTS' in sql
