"""SQL allowlist and query execution for chat — Phase 1 scaffold.

Security model: The LLM NEVER generates SQL. It picks a named query type
from an enum (Literal type in Phase 3). Python maps that name to a
pre-written parameterized query. This file is the scaffold — Phase 3
populates ALLOWED_QUERIES with real queries.

SEC-02: ALLOWED_QUERIES dict enforces the allowlist
SEC-03: validate_sql_safety() is a secondary defense using sqlparse
"""
import sqlparse
import logging

logger = logging.getLogger(__name__)

# Phase 1 scaffold — Phase 3 populates with real queries:
#   "fitness_score": "SELECT ... WHERE rider_id = %s",
#   "brevet_history": "SELECT ... WHERE rider_id = %s",
#   "upcoming_rides": "SELECT ...",
#   "career_stats": "SELECT ... WHERE rider_id = %s",
#   "recent_activities": "SELECT ... WHERE rider_id = %s",
ALLOWED_QUERIES: dict[str, str] = {}


def validate_sql_safety(sql: str) -> bool:
    """Secondary defense: confirm SQL is SELECT-only, single statement.

    Uses sqlparse to parse and validate. Returns False on any doubt.
    This is belt-and-suspenders — ALLOWED_QUERIES is the primary control.
    """
    if not sql or not sql.strip():
        return False
    try:
        statements = sqlparse.parse(sql.strip())
        if len(statements) != 1:
            return False  # Multiple statements — possible injection
        stmt = statements[0]
        if stmt.get_type() != 'SELECT':
            return False  # Only SELECT allowed
        return True
    except Exception:
        return False  # Parse error — reject


def execute_allowed_query(query_type: str, params: tuple = (), user_id: int = None) -> dict:
    """Execute a named query from ALLOWED_QUERIES.

    Args:
        query_type: Key in ALLOWED_QUERIES dict (e.g., "fitness_score")
        params: Query parameters (always parameterized, never string interpolation)
        user_id: Authenticated user ID from session (always from session, never from client)

    Returns:
        dict with "rows" key on success, "error" key on failure
    """
    if query_type not in ALLOWED_QUERIES:
        logger.warning(f"Rejected unknown query type: {query_type}")
        return {"error": f"Unknown query type: {query_type}"}

    sql = ALLOWED_QUERIES[query_type]

    # Secondary validation — belt and suspenders
    if not validate_sql_safety(sql):
        logger.error(f"Query '{query_type}' failed safety check — this should never happen with hardcoded queries")
        return {"error": "Query failed safety check"}

    from models import _execute
    try:
        rows = list(_execute(sql, params).fetchmany(50))  # Hard cap: 50 rows
        return {"rows": rows}
    except Exception as e:
        logger.error(f"Query '{query_type}' execution error: {e}")
        return {"error": "Query execution failed"}
