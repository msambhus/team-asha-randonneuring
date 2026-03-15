"""SQL allowlist and query execution for chat — Phase 1 scaffold + Phase 3 population.

Security model: The LLM NEVER generates SQL. It picks a named query type
from an enum (Literal type in Phase 3). Python maps that name to a
pre-written parameterized query.

SEC-02: ALLOWED_QUERIES dict enforces the allowlist
SEC-03: validate_sql_safety() is a secondary defense using sqlparse
AGENT-03 through AGENT-08: Populated queries, timeout, row cap
"""
import sqlparse
import logging

logger = logging.getLogger(__name__)

# AGENT-04: User-scoped tools (parameterized by rider_id)
# AGENT-05: Team-scoped tool (no rider_id)
# AGENT-06: Ride plan tool (parameterized by slug/name)
ALLOWED_QUERIES: dict[str, str] = {
    "fitness_score": """
        SELECT
            COUNT(*) FILTER (WHERE activity_type = 'Ride') AS ride_count,
            COALESCE(SUM(distance), 0) / 1000.0 AS total_km,
            COALESCE(SUM(total_elevation_gain), 0) AS total_elevation_m,
            MAX(start_date_local) AS last_activity_date
        FROM strava_activity
        WHERE rider_id = %s
          AND start_date >= NOW() - INTERVAL '28 days'
    """,
    "brevet_history": """
        SELECT ri.name, ri.date, ri.distance_km, ri.elevation_ft,
               rr.status, rr.finish_time
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE rr.rider_id = %s
          AND rr.status = 'FINISHED'
        ORDER BY ri.date DESC
        LIMIT 20
    """,
    "upcoming_rides": """
        SELECT ri.name, ri.date, ri.distance_km, rr.status
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE rr.rider_id = %s
          AND ri.date >= CURRENT_DATE
          AND rr.status IN ('GOING', 'INTERESTED', 'MAYBE')
        ORDER BY ri.date
    """,
    "career_stats": """
        SELECT
            COUNT(*) AS total_rides_finished,
            COALESCE(SUM(ri.distance_km), 0) AS total_km,
            COUNT(DISTINCT ri.season_id) AS seasons_participated,
            MAX(ri.distance_km) AS longest_ride_km
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE rr.rider_id = %s
          AND rr.status = 'FINISHED'
    """,
    "recent_activities": """
        SELECT name, activity_type, distance / 1000.0 AS km,
               total_elevation_gain AS elevation_m,
               moving_time / 3600.0 AS hours,
               start_date_local
        FROM strava_activity
        WHERE rider_id = %s
          AND start_date >= NOW() - INTERVAL '28 days'
        ORDER BY start_date_local DESC
        LIMIT 10
    """,
    "get_team_stats": """
        SELECT
            s.name AS season_name,
            COUNT(DISTINCT rr.rider_id) AS active_riders,
            COUNT(*) AS total_finishes,
            COALESCE(SUM(ri.distance_km), 0) AS total_km
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        JOIN season s ON ri.season_id = s.id
        WHERE rr.status = 'FINISHED'
          AND s.is_current = TRUE
        GROUP BY s.name
    """,
    "get_ride_plan": """
        SELECT rp.name, rp.distance_km, rp.total_elevation_ft, rp.cutoff_hours,
               rps.stop_order, rps.stop_name, rps.location, rps.stop_type,
               rps.distance_miles AS distance_from_start_miles,
               rps.elevation_gain AS segment_elevation_ft,
               rps.segment_time_min, rps.cum_time_min
        FROM ride_plan rp
        JOIN ride_plan_stop rps ON rps.ride_plan_id = rp.id
        WHERE rp.slug ILIKE %s OR rp.name ILIKE %s
        ORDER BY rps.stop_order
    """,
}


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
    """Execute a named query from ALLOWED_QUERIES with timeout enforcement.

    Args:
        query_type: Key in ALLOWED_QUERIES dict (e.g., "fitness_score")
        params: Query parameters (always parameterized, never string interpolation)
        user_id: Authenticated user ID from session (always from session, never from client)

    Returns:
        dict with "rows" key on success, "error" key on failure

    AGENT-08: Enforces 5-second PostgreSQL statement timeout via SET LOCAL
    and caps results at 50 rows via fetchmany(50).
    """
    if query_type not in ALLOWED_QUERIES:
        logger.warning(f"Rejected unknown query type: {query_type}")
        return {"error": f"Unknown query type: {query_type}"}

    sql = ALLOWED_QUERIES[query_type]

    # Secondary validation — belt and suspenders
    if not validate_sql_safety(sql):
        logger.error(f"Query '{query_type}' failed safety check — this should never happen with hardcoded queries")
        return {"error": "Query failed safety check"}

    import psycopg2.extras
    from db import get_db
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET LOCAL statement_timeout = '5000'")  # AGENT-08: 5s timeout
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchmany(50)]  # AGENT-08: 50-row cap
        return {"rows": rows}
    except Exception as e:
        msg = str(e).lower()
        if 'statement timeout' in msg or 'canceling statement' in msg:
            logger.warning(f"Query '{query_type}' timed out after 5s")
            return {"error": "Query timed out after 5 seconds"}
        logger.error(f"Query '{query_type}' execution error: {e}")
        return {"error": "Query execution failed"}
