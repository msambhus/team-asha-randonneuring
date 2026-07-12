"""SQL allowlist and query execution for chat — Phase 1 scaffold + Phase 3 population.

Security model: The LLM NEVER generates SQL. It picks a named query type
from an enum (Literal type in Phase 3). Python maps that name to a
pre-written parameterized query.

SEC-02: ALLOWED_QUERIES dict enforces the allowlist
SEC-03: validate_sql_safety() is a secondary defense using sqlparse
AGENT-03 through AGENT-08: Populated queries, timeout, row cap
RWGPS-01 through RWGPS-06: Route data lookup, summarization, caching
WTHR-01/02: Weather query tool orchestration
"""
import sqlparse
import logging
from datetime import datetime, timedelta

import requests

from services.rwgps import (
    fetch_route, extract_controls, build_ride_plan,
    extract_rwgps_route_id,
    METERS_TO_MILES, METERS_TO_FEET,
)
from services.weather import (
    sample_track_points, calculate_bearing,
    load_stored_route_weather, format_weather_response,
)
from cache import cache
import models

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
    "get_team_leaderboard": """
        SELECT r.first_name || ' ' || r.last_name AS rider_name,
               COUNT(*) AS rides_finished,
               COALESCE(SUM(ri.distance_km), 0) AS total_km,
               MAX(ri.distance_km) AS longest_ride_km
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        JOIN rider r ON rr.rider_id = r.id
        WHERE rr.status = 'FINISHED'
        GROUP BY r.id, r.first_name, r.last_name
        ORDER BY total_km DESC
        LIMIT 20
    """,
    "get_eddington_scores": """
        SELECT r.first_name || ' ' || r.last_name AS rider_name,
               sc.eddington_number_miles,
               sc.eddington_number_km,
               sc.eddington_calculated_at
        FROM strava_connection sc
        JOIN rider r ON r.id = sc.rider_id
        WHERE sc.eddington_number_miles IS NOT NULL
        ORDER BY sc.eddington_number_miles DESC
    """,
    "get_my_eddington": """
        SELECT sc.eddington_number_miles,
               sc.eddington_number_km,
               sc.eddington_calculated_at
        FROM strava_connection sc
        WHERE sc.rider_id = %s
    """,
    "get_ride_rwgps_url": """
        SELECT ri.name, ri.rwgps_url, ri.distance_km
        FROM ride ri
        WHERE ri.name ILIKE %s OR ri.name ILIKE %s
        ORDER BY ri.date DESC
        LIMIT 1
    """,
    "get_ride_plan_for_weather": """
        SELECT rp.name, rp.slug, rp.distance_km,
               ri.rwgps_url,
               rps.cum_time_min
        FROM ride_plan rp
        JOIN ride ri ON ri.name ILIKE rp.name
        LEFT JOIN ride_plan_stop rps ON rps.ride_plan_id = rp.id
        WHERE rp.slug ILIKE %s OR rp.name ILIKE %s
        ORDER BY rps.stop_order
    """,
    "get_ride_plan": """
        SELECT rp.name, rp.slug, rp.distance_km, rp.total_elevation_ft, rp.cutoff_hours,
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


def execute_web_search(client, query: str) -> dict:
    """Search the web for cycling/bike information using OpenAI Responses API.

    Args:
        client: OpenAI client instance (reused from agent loop)
        query: The user's original question

    Returns:
        dict with "rows" key containing search text and sources, or "error" key
    """
    try:
        response = client.responses.create(
            model="gpt-4o",
            tools=[{"type": "web_search"}],
            input=(
                f"Search for cycling and bike-related information to answer: {query}\n"
                "Focus on randonneuring, long-distance cycling, and brevet-relevant gear."
            ),
            timeout=15,
        )

        # Extract text and citations from response
        text = ""
        sources = []
        for item in response.output:
            if hasattr(item, 'content'):
                for content_block in item.content:
                    if hasattr(content_block, 'text'):
                        text += content_block.text
                    if hasattr(content_block, 'annotations'):
                        for annotation in content_block.annotations:
                            if hasattr(annotation, 'url') and hasattr(annotation, 'title'):
                                sources.append({
                                    "title": annotation.title,
                                    "url": annotation.url,
                                })

        if not text:
            return {"error": "Web search returned no results"}

        # Deduplicate sources
        seen_urls = set()
        unique_sources = []
        for s in sources:
            if s["url"] not in seen_urls:
                seen_urls.add(s["url"])
                unique_sources.append(s)

        return {"rows": [{"text": text, "sources": unique_sources[:5]}]}

    except Exception as e:
        logger.warning(f"Web search failed: {e}")
        return {"error": "Web search unavailable — please try again"}


def summarize_route_for_chat(route_data, controls):
    """Convert RWGPS route data + controls into a compact dict for LLM context.

    RWGPS-03: Returns only the fields the LLM needs — no track_points.
    """
    plan_result = build_ride_plan(route_data, controls)
    plan = plan_result['plan']
    stops = plan_result['stops']

    control_stops = [
        {
            'location': s['location'],
            'distance_miles': s['distance_miles'],
            'stop_type': s['stop_type'],
            'elevation_gain_ft': s['elevation_gain'],
        }
        for s in stops
    ]

    return {
        'name': plan['name'],
        'total_distance_miles': plan['total_distance_miles'],
        'total_elevation_ft': plan['total_elevation_ft'],
        'distance_km': plan['distance_km'],
        'cutoff_hours': plan['cutoff_hours'],
        'overall_ft_per_mile': plan['overall_ft_per_mile'],
        'avg_moving_speed_mph': plan['avg_moving_speed'],
        'rwgps_url': plan['rwgps_url'],
        'control_stops': control_stops,
        'source': 'live_rwgps_api',
    }


def fetch_and_summarize_route(route_id):
    """Fetch RWGPS route data with caching and comprehensive error handling.

    RWGPS-04/05/06: Cache-first pattern with 5-min TTL. All errors produce
    user-friendly dicts, never exceptions.
    """
    cache_key = f'rwgps_route_{route_id}'

    # RWGPS-06: Check cache first
    cached = cache.get(cache_key)
    if cached is not None:
        return {'rows': [cached]}

    # Fetch from RWGPS API
    try:
        route_data = fetch_route(route_id)
    except Exception as e:
        msg = str(e).lower()
        if 'not found' in msg or '404' in msg:
            return {'error': f"Route not found on RideWithGPS (ID: {route_id}). Check the route ID and try again."}
        if '401' in msg or 'authentication' in msg:
            return {'error': "RWGPS API credentials issue. Please contact the admin to verify API keys."}
        if '429' in msg or 'rate limit' in msg:
            return {'error': "RWGPS rate limit hit. Please try again in a few minutes."}
        logger.warning(f"RWGPS fetch error for route {route_id}: {e}")
        return {'error': f"Could not fetch route data: {str(e)[:200]}"}

    # Extract controls — fall back to minimal summary if no waypoints
    try:
        controls = extract_controls(route_data)
        summary = summarize_route_for_chat(route_data, controls)
    except Exception:
        # RWGPS-05: No waypoints — build minimal summary from route-level fields
        total_dist_m = route_data.get('distance', 0) or 0
        total_elev_m = route_data.get('elevation_gain', 0) or 0
        total_miles = round(total_dist_m * METERS_TO_MILES, 1)
        total_ft = int(round(total_elev_m * METERS_TO_FEET))
        route_id_str = str(route_data.get('id', route_id))
        summary = {
            'name': route_data.get('name', 'Unknown Route'),
            'total_distance_miles': total_miles,
            'total_elevation_ft': total_ft,
            'distance_km': int(round(total_dist_m / 1000)),
            'rwgps_url': f'https://ridewithgps.com/routes/{route_id_str}',
            'control_stops': [],
            'source': 'live_rwgps_api',
        }

    # RWGPS-06: Cache with 5-min TTL
    cache.set(cache_key, summary, timeout=300)
    return {'rows': [summary]}


def execute_route_weather(ride_name: str, start_datetime: str = None) -> dict:
    """Fetch weather forecast for a route by ride name.

    Orchestrates: ride plan lookup -> RWGPS fetch -> weather sampling -> formatting.
    WTHR-01/02: Intent-triggered weather tool for the agent loop.

    Args:
        ride_name: Name of the ride/route to look up
        start_datetime: Optional ISO datetime for ride start (default: now + 12h)

    Returns:
        dict with 'rows' containing formatted weather response, or 'error' on failure
    """
    try:
        # Step 1: Look up ride plan to get RWGPS URL (fuzzy match for DB suffixes)
        fuzzy_name = f'%{ride_name}%'
        plan_result = execute_allowed_query(
            query_type='get_ride_plan_for_weather',
            params=(fuzzy_name, fuzzy_name),
            user_id=None,
        )
        rows = plan_result.get('rows', [])
        if not rows:
            return {'error': f"I couldn't find a ride plan for '{ride_name}'. Check the ride name and try again."}

        row = rows[0]
        rwgps_url = row.get('rwgps_url')
        route_slug = row.get('slug', ride_name.lower().replace(' ', '-'))

        if not rwgps_url:
            return {'error': "I need the route's GPS track to forecast weather. This route doesn't have an RWGPS link."}

        route_id = extract_rwgps_route_id(rwgps_url)
        if route_id is None:
            return {'error': "Could not extract a valid route ID from the RWGPS URL."}

        # Weather is pre-fetched hourly by the fetch-route-weather cron and READ from
        # storage — the chat tool never calls Open-Meteo live (TA-237).

        # Step 2: Parse start datetime
        if start_datetime:
            try:
                start_dt = datetime.fromisoformat(start_datetime)
            except (ValueError, TypeError):
                start_dt = datetime.now() + timedelta(hours=12)
        else:
            start_dt = datetime.now() + timedelta(hours=12)

        # Step 3: Check if within 16-day forecast window
        max_forecast = datetime.now() + timedelta(days=16)
        if start_dt > max_forecast:
            return {'error': "Weather forecasts are only available up to 16 days ahead. Check back closer to your ride date."}

        # Step 4: Load the stored forecast for this route + date. The stored sample points
        # ARE the points the forecast was sampled at, so weather_data aligns with them.
        weather_data, sample_points = load_stored_route_weather(route_id, start_dt.date())
        if not weather_data or not sample_points:
            return {'error': "Weather forecast isn't available for this route yet — it's prepared "
                             "hourly for upcoming rides. Check back closer to the ride date."}

        # Step 5: Compute bearings between consecutive sample points
        bearings = []
        for i in range(len(sample_points) - 1):
            b = calculate_bearing(
                sample_points[i]['lat'], sample_points[i]['lng'],
                sample_points[i + 1]['lat'], sample_points[i + 1]['lng'],
            )
            bearings.append(b)

        # Step 6: Format response
        formatted = format_weather_response(sample_points, weather_data, bearings, start_dt)
        return {'rows': [formatted]}

    except requests.RequestException:
        return {'error': "Weather data is temporarily unavailable. The route information is still accessible — try asking about the route details instead."}
    except Exception as e:
        logger.warning(f"Weather fetch failed for '{ride_name}': {e}")
        return {'error': "Unable to fetch weather for this route. Please try again."}


def fetch_custom_plan_comparison(rider_id, ride_name):
    """Fetch custom plan comparison if the rider has customized a base plan.

    Args:
        rider_id: Rider ID to check for custom plan
        ride_name: Ride name or slug to look up the base plan

    Returns dict with 'rows' containing comparison data, or None if no custom plan.
    Uses cached model functions — no new SQL queries.
    """
    if not rider_id or not ride_name:
        return None

    try:
        # Try slug match first, then name match
        from services.rwgps import slugify
        base_plan = models.get_ride_plan_by_slug(slugify(ride_name))
        if not base_plan:
            base_plan = models.get_ride_plan_by_slug(ride_name)
        if not base_plan:
            return None

        custom_plan = models.get_custom_plan(rider_id, base_plan['id'])
        if not custom_plan:
            return None

        from services.custom_plan_service import get_merged_plan_stops, compare_plans
        merged_stops = get_merged_plan_stops(custom_plan['id'])
        if not merged_stops:
            return None

        base_stops = models.get_ride_plan_stops(base_plan['id'])
        if not base_stops:
            return None

        comparison = compare_plans(base_stops, merged_stops)

        summary = {
            'has_custom_plan': True,
            'custom_plan_name': custom_plan.get('name', 'Custom Plan'),
            'total_time_diff_min': comparison.get('total_time_diff', 0),
            'stops_added': comparison.get('stops_added', 0),
            'stops_hidden': comparison.get('stops_hidden', 0),
            'stops_modified': comparison.get('stops_modified', 0),
            'custom_stops': [
                {
                    'stop_order': i + 1,
                    'location': s.get('location', ''),
                    'distance_miles': float(s.get('distance_miles') or 0),
                    'segment_time_min': s.get('segment_time_min', 0),
                    'cum_time_min': s.get('cum_time_min', 0),
                    'is_modified': s.get('is_modified', False),
                    'is_custom_stop': s.get('is_custom_stop', False),
                }
                for i, s in enumerate(merged_stops)
            ],
        }
        return {'rows': [summary]}

    except Exception as e:
        logger.warning(f"Custom plan comparison failed for rider {rider_id}, plan {ride_name}: {e}")
        return None
