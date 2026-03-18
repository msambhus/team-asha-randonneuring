"""Tests for services/chat_tools.py — SQL allowlist and validation (SEC-02, SEC-03, AGENT-03 through AGENT-08)."""
from unittest.mock import patch, MagicMock


def test_allowlist_enforcement():
    """SEC-02: execute_allowed_query rejects unknown query types."""
    from services.chat_tools import execute_allowed_query

    result = execute_allowed_query(query_type="nonexistent_query")
    assert "error" in result
    assert "Unknown query type" in result["error"]


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


# --- AGENT-04 through AGENT-06: ALLOWED_QUERIES population tests ---

def test_allowed_queries_populated():
    """AGENT-04/05/06: ALLOWED_QUERIES contains all named queries."""
    from services.chat_tools import ALLOWED_QUERIES

    expected_keys = {
        "fitness_score", "brevet_history", "upcoming_rides",
        "career_stats", "recent_activities", "get_team_stats", "get_ride_plan",
        "get_team_leaderboard", "get_eddington_scores", "get_my_eddington",
        "get_ride_rwgps_url", "get_ride_plan_for_weather",
    }
    assert set(ALLOWED_QUERIES.keys()) == expected_keys
    assert len(ALLOWED_QUERIES) == 12


def test_all_queries_pass_safety_check():
    """SEC-03 + AGENT-04: Every query in ALLOWED_QUERIES is SELECT-only, single statement."""
    from services.chat_tools import ALLOWED_QUERIES, validate_sql_safety

    for name, sql in ALLOWED_QUERIES.items():
        assert validate_sql_safety(sql) is True, f"Query '{name}' failed safety check"


def test_user_scoped_queries_have_rider_param():
    """AGENT-04: User-scoped queries contain %s placeholder for rider_id."""
    from services.chat_tools import ALLOWED_QUERIES

    user_scoped = ["fitness_score", "brevet_history", "upcoming_rides", "career_stats", "recent_activities"]
    for name in user_scoped:
        assert "%s" in ALLOWED_QUERIES[name], f"Query '{name}' missing rider_id placeholder"


def test_team_stats_no_user_param():
    """AGENT-05: get_team_stats has no %s placeholder (team-scoped)."""
    from services.chat_tools import ALLOWED_QUERIES

    assert "%s" not in ALLOWED_QUERIES["get_team_stats"]


def test_ride_plan_two_params():
    """AGENT-06: get_ride_plan has exactly 2 %s placeholders (slug ILIKE + name ILIKE)."""
    from services.chat_tools import ALLOWED_QUERIES

    count = ALLOWED_QUERIES["get_ride_plan"].count("%s")
    assert count == 2, f"Expected 2 placeholders, got {count}"


# --- AGENT-08: Timeout and row cap tests ---

def test_timeout_enforcement():
    """AGENT-08: SET LOCAL statement_timeout = '5000' is executed before the main query."""
    from services.chat_tools import execute_allowed_query

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchmany.return_value = []

    with patch("db.get_db", return_value=mock_conn):
        execute_allowed_query(query_type="get_team_stats")

    # First execute call should be the SET LOCAL timeout
    calls = mock_cursor.execute.call_args_list
    assert len(calls) >= 2
    assert calls[0][0][0] == "SET LOCAL statement_timeout = '5000'"


def test_timeout_error_handling():
    """AGENT-08: Timeout error returns clean dict, not exception."""
    from services.chat_tools import execute_allowed_query

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    # First execute (SET LOCAL) succeeds, second (query) times out
    mock_cursor.execute.side_effect = [
        None,
        Exception("canceling statement due to statement timeout"),
    ]

    with patch("db.get_db", return_value=mock_conn):
        result = execute_allowed_query(query_type="get_team_stats")

    assert result == {"error": "Query timed out after 5 seconds"}


def test_row_cap_50():
    """AGENT-08: Results capped at 50 rows via fetchmany(50)."""
    from services.chat_tools import execute_allowed_query

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchmany.return_value = [{"id": 1}]

    with patch("db.get_db", return_value=mock_conn):
        execute_allowed_query(query_type="get_team_stats")

    mock_cursor.fetchmany.assert_called_once_with(50)


def test_uses_real_dict_cursor():
    """AGENT-08: RealDictCursor is used for dict-style rows."""
    import psycopg2.extras
    from services.chat_tools import execute_allowed_query

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchmany.return_value = []

    with patch("db.get_db", return_value=mock_conn):
        execute_allowed_query(query_type="get_team_stats")

    mock_conn.cursor.assert_called_once_with(cursor_factory=psycopg2.extras.RealDictCursor)


def test_execute_returns_rows_as_dicts():
    """Results are converted to plain dicts."""
    from services.chat_tools import execute_allowed_query

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    # Simulate RealDictRow (acts like dict)
    mock_cursor.fetchmany.return_value = [{"season_name": "2024-2025", "total_km": 500}]

    with patch("db.get_db", return_value=mock_conn):
        result = execute_allowed_query(query_type="get_team_stats")

    assert "rows" in result
    assert result["rows"] == [{"season_name": "2024-2025", "total_km": 500}]


def test_general_execution_error():
    """Non-timeout execution errors return generic error dict."""
    from services.chat_tools import execute_allowed_query

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.execute.side_effect = [None, Exception("connection lost")]

    with patch("db.get_db", return_value=mock_conn):
        result = execute_allowed_query(query_type="get_team_stats")

    assert result == {"error": "Query execution failed"}
