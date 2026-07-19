"""Migration 044 — live-tracking schema (telemetry columns + rp_live_tracking).

Like the 033/036 migration tests, this is a STATIC SQL contract check (no DB): it
proves the mission-required properties the Python/model scanners can't see (they
read model SQL, not migration files). It asserts migration 044:
  - parses as valid SQL,
  - extends rp_live_position with the telemetry columns (source/accuracy/speed/
    heart_rate/power/cadence + created_at) via ADD COLUMN IF NOT EXISTS (additive),
  - creates rp_live_tracking with the mirrored TA columns (rider_id/enabled/
    garmin_session_url/garmin_session_token/active_ride_id/updated_at),
  - adds rp_ride.rwgps_url for the route overlay,
  - is idempotent (IF NOT EXISTS guards every ADD COLUMN / CREATE) and strictly
    additive (no DROP),
  - touches ONLY rp_* tables/references (the isolation invariant, on the SQL).
"""
import os
import re

import sqlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATION_PATH = os.path.join(REPO_ROOT, 'migrations', '044_brevethub_live_tracking.sql')


def _sql():
    with open(MIGRATION_PATH, 'r', encoding='utf-8') as fh:
        return fh.read()


def test_migration_exists():
    assert os.path.exists(MIGRATION_PATH), "migration 044 is missing"


def test_migration_parses():
    statements = [s for s in sqlparse.parse(_sql()) if s.token_first(skip_cm=True)]
    assert statements, "migration 044 parsed to zero statements"


def test_position_telemetry_columns_added_additively():
    """Every telemetry column is added via ALTER … ADD COLUMN IF NOT EXISTS on
    rp_live_position (additive + idempotent)."""
    sql = _sql()
    for col in ('source', 'accuracy', 'speed', 'heart_rate', 'power', 'cadence',
                'created_at'):
        assert re.search(
            r'ALTER\s+TABLE\s+rp_live_position\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+'
            + col + r'\b',
            sql, re.IGNORECASE), f"migration 044 must ADD COLUMN IF NOT EXISTS {col}"


def test_live_tracking_table_created_with_expected_columns():
    """rp_live_tracking is created (IF NOT EXISTS) with the TA-mirrored columns."""
    sql = _sql()
    assert re.search(
        r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+rp_live_tracking',
        sql, re.IGNORECASE), "migration 044 must CREATE TABLE IF NOT EXISTS rp_live_tracking"
    # The block from CREATE TABLE rp_live_tracking to its closing ');'.
    block = re.search(r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+rp_live_tracking.*?\);',
                      sql, re.IGNORECASE | re.DOTALL).group(0)
    for col in ('rider_id', 'enabled', 'garmin_session_url', 'garmin_session_token',
                'active_ride_id', 'updated_at'):
        assert re.search(r'\b' + col + r'\b', block), \
            f"rp_live_tracking must define {col}"


def test_ride_gets_rwgps_url():
    sql = _sql()
    assert re.search(
        r'ALTER\s+TABLE\s+rp_ride\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+rwgps_url',
        sql, re.IGNORECASE), "migration 044 must ADD COLUMN IF NOT EXISTS rwgps_url on rp_ride"


def test_migration_is_idempotent_and_additive():
    """IF NOT EXISTS guards every ADD COLUMN and CREATE; no DROP anywhere."""
    sql = _sql()
    assert 'DROP' not in sql.upper(), "migration 044 must be strictly additive (no DROP)"
    for m in re.finditer(r'ADD\s+COLUMN\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "an ADD COLUMN in migration 044 lacks IF NOT EXISTS"
    for m in re.finditer(r'CREATE\s+INDEX\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "a CREATE INDEX in migration 044 lacks IF NOT EXISTS"
    for m in re.finditer(r'CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?', sql, re.IGNORECASE):
        assert m.group(1), "a CREATE TABLE in migration 044 lacks IF NOT EXISTS"


def test_migration_touches_only_rp_tables():
    """Every table/reference the migration names is rp_-prefixed (isolation)."""
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
    assert seen_any, "no table references found in migration 044 — scan is broken"
    assert not offenders, f"migration 044 touches non-rp_ tables: {sorted(offenders)}"
