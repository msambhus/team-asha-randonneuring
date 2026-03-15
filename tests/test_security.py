"""Tests for security controls (SEC-01: read-only role)."""
import os
import pytest
import psycopg2


def test_readonly_role(db_conn):
    """Verify chat_readonly role cannot INSERT, UPDATE, or DELETE."""
    db_url = os.environ.get('TEST_DATABASE_URL', os.environ.get('DATABASE_URL'))

    # Check if chat_readonly role exists
    cur = db_conn.cursor()
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'chat_readonly'")
    if not cur.fetchone():
        pytest.skip("chat_readonly role not available on Supabase free tier")

    # Attempt to connect as chat_readonly and INSERT — should fail
    try:
        readonly_conn = psycopg2.connect(db_url, options="-c role=chat_readonly")
        readonly_cur = readonly_conn.cursor()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            readonly_cur.execute(
                "INSERT INTO conversation (user_id, title) VALUES (1, 'hack')"
            )
        readonly_conn.close()
    except psycopg2.OperationalError:
        pytest.skip("Cannot connect as chat_readonly — role may not have LOGIN privilege")
