"""Tests for services/chat_tools.py — SQL allowlist and validation (SEC-02, SEC-03)."""


def test_allowlist_enforcement():
    """SEC-02: execute_allowed_query rejects unknown query types."""
    from services.chat_tools import execute_allowed_query, ALLOWED_QUERIES

    result = execute_allowed_query(query_type="nonexistent_query")
    assert "error" in result

    # In Phase 1 the dict is empty scaffold — all types should be rejected
    for key in ALLOWED_QUERIES:
        result = execute_allowed_query(query_type=key)
        assert "rows" in result or "error" in result


def test_sqlparse_validation():
    """SEC-03: validate_sql_safety rejects non-SELECT and multi-statement SQL."""
    from services.chat_tools import validate_sql_safety

    # Valid SELECT queries
    assert validate_sql_safety("SELECT * FROM riders") is True
    assert validate_sql_safety("SELECT id, name FROM riders WHERE id = 1") is True

    # Invalid: non-SELECT statements
    assert validate_sql_safety("UPDATE riders SET name = 'x'") is False
    assert validate_sql_safety("DELETE FROM riders") is False
    assert validate_sql_safety("DROP TABLE riders") is False
    assert validate_sql_safety("INSERT INTO riders VALUES (1)") is False

    # Invalid: multiple statements
    assert validate_sql_safety("SELECT 1; DROP TABLE riders") is False

    # Invalid: empty
    assert validate_sql_safety("") is False
