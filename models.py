"""Data access layer — all SQL queries live here (PostgreSQL via psycopg2)."""
from datetime import datetime, date
import psycopg2.extras
from db import get_db


def _execute(sql, params=None):
    """Execute a query and return a RealDictCursor."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params or ())
    return cur


# ========== SEASONS ==========

def get_all_seasons():
    return _execute("SELECT * FROM season ORDER BY start_date DESC").fetchall()

def get_current_season():
    return _execute("SELECT * FROM season WHERE is_current = TRUE").fetchone()

def get_season_by_name(name):
    return _execute("SELECT * FROM season WHERE name = %s", (name,)).fetchone()


# ========== RIDERS ==========

def get_all_riders():
    return _execute("""
        SELECT r.*, rp.photo_filename, rp.bio, rp.pbp_2023_registered, rp.pbp_2023_status
        FROM rider r LEFT JOIN rider_profile rp ON r.id = rp.rider_id
        ORDER BY r.first_name
    """).fetchall()

def get_rider_by_rusa(rusa_id):
    return _execute("""
        SELECT r.*, rp.photo_filename, rp.bio, rp.pbp_2023_registered, rp.pbp_2023_status
        FROM rider r LEFT JOIN rider_profile rp ON r.id = rp.rider_id
        WHERE r.rusa_id = %s
    """, (rusa_id,)).fetchone()

def get_riders_for_season(season_id):
    """Get riders who have any participation record in this season."""
    return _execute("""
        SELECT DISTINCT r.*, rp.photo_filename
        FROM rider r
        LEFT JOIN rider_profile rp ON r.id = rp.rider_id
        JOIN rider_ride rr ON r.id = rr.rider_id
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = %s
        ORDER BY r.first_name
    """, (season_id,)).fetchall()

def get_active_riders_for_season(season_id):
    """Get riders who have completed at least 1 ride (status=FINISHED) in this season, only counting past rides."""
    today = date.today()
    return _execute("""
        SELECT DISTINCT r.*, rp.photo_filename
        FROM rider r
        LEFT JOIN rider_profile rp ON r.id = rp.rider_id
        JOIN rider_ride rr ON r.id = rr.rider_id
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = %s AND rr.status = 'FINISHED' AND ri.date <= %s
        ORDER BY r.first_name
    """, (season_id, today)).fetchall()


# ========== RIDES ==========

def get_rides_for_season(season_id):
    """Get all rides for a season with club info."""
    return _execute("""
        SELECT ri.*, 
               c.code as club_code, 
               c.name as club_name,
               c.region as region,
               rp.slug as plan_slug,
               (c.code = 'TA') as is_team_ride
        FROM ride ri 
        INNER JOIN club c ON ri.club_id = c.id
        LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
        WHERE ri.season_id = %s
        ORDER BY ri.date
    """, (season_id,)).fetchall()

def get_ride_by_id(ride_id):
    """Get a single ride by ID with club info."""
    return _execute("""
        SELECT ri.*, 
               c.code as club_code, 
               c.name as club_name,
               c.region as region,
               (c.code = 'TA') as is_team_ride
        FROM ride ri 
        INNER JOIN club c ON ri.club_id = c.id
        WHERE ri.id = %s
    """, (ride_id,)).fetchone()

def get_upcoming_rides():
    """Get Team Asha upcoming rides."""
    today = date.today()
    ta_club_id = get_team_asha_club_id()
    return _execute("""
        SELECT ri.*, 
               c.code as club_code, 
               c.name as club_name,
               c.region as region,
               rp.slug as plan_slug,
               (SELECT COUNT(*) FROM rider_ride rr WHERE rr.ride_id = ri.id AND rr.signed_up_at IS NOT NULL) as signup_count
        FROM ride ri 
        INNER JOIN club c ON ri.club_id = c.id
        LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
        WHERE ri.date >= %s AND ri.club_id = %s
        ORDER BY ri.date
    """, (today, ta_club_id)).fetchall()

def get_past_rides_for_season(season_id):
    """Get past Team Asha rides for a season."""
    today = date.today()
    ta_club_id = get_team_asha_club_id()
    return _execute("""
        SELECT ri.*, 
               c.code as club_code, 
               c.name as club_name,
               c.region as region,
               rp.slug as plan_slug
        FROM ride ri 
        INNER JOIN club c ON ri.club_id = c.id
        LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
        WHERE ri.season_id = %s AND ri.date < %s AND ri.club_id = %s
        ORDER BY ri.date
    """, (season_id, today, ta_club_id)).fetchall()

def get_clubs():
    return _execute("SELECT * FROM club ORDER BY name").fetchall()


# ========== PARTICIPATION ==========

def get_participation_matrix(season_id):
    """Return {rider_id: {ride_id: {status, finish_time, signed_up_at}}} for a season."""
    rows = _execute("""
        SELECT rr.rider_id, rr.ride_id, rr.status, rr.finish_time, rr.signed_up_at
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = %s
          AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
    """, (season_id,)).fetchall()
    matrix = {}
    for row in rows:
        rid = row['rider_id']
        if rid not in matrix:
            matrix[rid] = {}
        matrix[rid][row['ride_id']] = {
            'status': row['status'],
            'finish_time': row['finish_time'],
            'signed_up_at': row['signed_up_at']
        }
    return matrix

def get_rider_participation(rider_id, season_id):
    return _execute("""
        SELECT rr.status, rr.finish_time, ri.name as ride_name, ri.date, ri.distance_km,
               ri.elevation_ft, ri.ft_per_mile, ri.rwgps_url, c.code as club_code
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        LEFT JOIN club c ON ri.club_id = c.id
        WHERE rr.rider_id = %s AND ri.season_id = %s
          AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
        ORDER BY ri.date
    """, (rider_id, season_id)).fetchall()

def get_rider_career_stats(rider_id):
    """Total rides completed, total KMs, across all seasons."""
    row = _execute("""
        SELECT COUNT(*) as total_rides,
               COALESCE(SUM(ri.distance_km), 0) as total_kms
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE rr.rider_id = %s AND rr.status = 'FINISHED'
          AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
    """, (rider_id,)).fetchone()
    return dict(row) if row else {'total_rides': 0, 'total_kms': 0}

def get_rider_season_stats(rider_id, season_id):
    """Rides and KMs for a specific season."""
    row = _execute("""
        SELECT COUNT(*) as rides, COALESCE(SUM(ri.distance_km), 0) as kms
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE rr.rider_id = %s AND ri.season_id = %s AND rr.status = 'FINISHED'
          AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
    """, (rider_id, season_id)).fetchone()
    return dict(row) if row else {'rides': 0, 'kms': 0}

def get_all_rider_season_stats(season_id):
    """Batch: rides and KMs for ALL riders in a season. Returns dict keyed by rider_id."""
    rows = _execute("""
        SELECT rr.rider_id, COUNT(*) as rides, COALESCE(SUM(ri.distance_km), 0) as kms
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = %s AND rr.status = 'FINISHED'
        GROUP BY rr.rider_id
    """, (season_id,)).fetchall()
    return {r['rider_id']: {'rides': r['rides'], 'kms': r['kms']} for r in rows}


# ========== SR DETECTION ==========

def detect_sr_for_rider_season(rider_id, season_id, date_filter=False):
    """Count complete SR sets (200+300+400+600) for a rider in a season.
    Returns count (min across all four buckets), or 0."""
    today = date.today()
    if date_filter:
        rows = _execute("""
            SELECT ri.distance_km FROM rider_ride rr
            JOIN ride ri ON rr.ride_id = ri.id
            WHERE rr.rider_id = %s AND ri.season_id = %s AND rr.status = 'FINISHED'
              AND ri.date <= %s
              AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
        """, (rider_id, season_id, today)).fetchall()
    else:
        rows = _execute("""
            SELECT ri.distance_km FROM rider_ride rr
            JOIN ride ri ON rr.ride_id = ri.id
            WHERE rr.rider_id = %s AND ri.season_id = %s AND rr.status = 'FINISHED'
              AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
        """, (rider_id, season_id)).fetchall()

    buckets = {200: 0, 300: 0, 400: 0, 600: 0}
    for row in rows:
        d = row['distance_km']
        if 200 <= d < 300:
            buckets[200] += 1
        elif 300 <= d < 400:
            buckets[300] += 1
        elif 400 <= d < 600:
            buckets[400] += 1
        elif d >= 600:
            buckets[600] += 1
    return min(buckets.values())

def detect_sr_for_all_riders_in_season(season_id, date_filter=False):
    """Batch: SR count for ALL riders in a season. Returns dict keyed by rider_id."""
    today = date.today()
    if date_filter:
        rows = _execute("""
            SELECT rr.rider_id, ri.distance_km FROM rider_ride rr
            JOIN ride ri ON rr.ride_id = ri.id
            WHERE ri.season_id = %s AND rr.status = 'FINISHED' AND ri.date <= %s
        """, (season_id, today)).fetchall()
    else:
        rows = _execute("""
            SELECT rr.rider_id, ri.distance_km FROM rider_ride rr
            JOIN ride ri ON rr.ride_id = ri.id
            WHERE ri.season_id = %s AND rr.status = 'FINISHED'
        """, (season_id,)).fetchall()

    # Group by rider, then compute SR per rider
    from collections import defaultdict
    rider_distances = defaultdict(list)
    for row in rows:
        rider_distances[row['rider_id']].append(row['distance_km'])

    result = {}
    for rider_id, distances in rider_distances.items():
        buckets = {200: 0, 300: 0, 400: 0, 600: 0}
        for d in distances:
            if 200 <= d < 300:
                buckets[200] += 1
            elif 300 <= d < 400:
                buckets[300] += 1
            elif 400 <= d < 600:
                buckets[400] += 1
            elif d >= 600:
                buckets[600] += 1
        result[rider_id] = min(buckets.values())
    return result

def get_rider_total_srs(rider_id):
    """Total SRs across all seasons."""
    seasons = get_all_seasons()
    current = get_current_season()
    total = 0
    for s in seasons:
        df = s['id'] == current['id'] if current else False
        total += detect_sr_for_rider_season(rider_id, s['id'], date_filter=df)
    return total


# ========== ALL-TIME STATS ==========

def get_all_time_stats():
    # Single query for riders, rides, kms
    row = _execute("""
        SELECT COUNT(DISTINCT rr.rider_id) as riders,
               COUNT(*) as rides,
               COALESCE(SUM(ri.distance_km), 0) as kms
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE rr.status = 'FINISHED'
          AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
    """).fetchone()
    riders = row['riders']
    rides = row['rides']
    kms = row['kms']

    # Unique SR earners (batch — 1 query per season instead of riders×seasons)
    seasons = get_all_seasons()
    current = get_current_season()
    sr_riders = set()
    for s in seasons:
        df = s['id'] == current['id'] if current else False
        all_srs = detect_sr_for_all_riders_in_season(s['id'], date_filter=df)
        for rider_id, n in all_srs.items():
            if n > 0:
                sr_riders.add(rider_id)
    # Mihir's India SR
    mihir = _execute("SELECT id FROM rider WHERE rusa_id = 14680").fetchone()
    if mihir:
        sr_riders.add(mihir['id'])

    return {
        'riders': riders,
        'rides': rides,
        'kms': kms,
        'srs': len(sr_riders)
    }


# ========== SEASON STATS ==========

def get_season_stats(season_id, past_only=False):
    """Get season stats. If past_only=True, only count rides before today."""
    current = get_current_season()
    is_current = current and current['id'] == season_id

    date_clause = ""
    params = [season_id]
    if past_only:
        today = date.today()
        date_clause = " AND ri.date <= %s"
        params.append(today)

    # Single query for active riders, total rides, total kms
    row = _execute(f"""
        SELECT COUNT(DISTINCT rr.rider_id) as active,
               COUNT(*) as rides,
               COALESCE(SUM(ri.distance_km), 0) as kms
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = %s AND rr.status = 'FINISHED'{date_clause}
    """, params).fetchone()
    active = row['active']
    total_rides = row['rides']
    total_kms = row['kms']

    # SR counts (batch — 1 query instead of N)
    all_srs = detect_sr_for_all_riders_in_season(season_id, date_filter=is_current)
    sr_count = sum(all_srs.values())
    sr_rider_count = sum(1 for n in all_srs.values() if n > 0)

    return {
        'active_riders': active,
        'total_rides': total_rides,
        'total_kms': total_kms,
        'sr_count': sr_count,
        'sr_rider_count': sr_rider_count,
    }


# ========== CLUB HELPERS ==========

def get_team_asha_club_id():
    """Get Team Asha club ID (cached helper)."""
    club = _execute("SELECT id FROM club WHERE code = 'TA'").fetchone()
    return club['id'] if club else None


# ========== UPCOMING EVENTS (UNIFIED) ==========

def get_default_time_limit(distance_km):
    """Return standard RUSA/ACP time limit in hours based on distance."""
    if distance_km <= 0:
        return None
    elif distance_km <= 200:
        return 13.5
    elif distance_km <= 300:
        return 20
    elif distance_km <= 400:
        return 27
    elif distance_km <= 600:
        return 40
    else:
        return None

def get_all_upcoming_events():
    """Get all upcoming events (Team Asha and external) with club info."""
    today = date.today()
    events = _execute("""
        SELECT ri.*, 
               c.code as club_code, 
               c.name as club_name,
               c.region as region,
               rp.slug as plan_slug,
               rp.rwgps_url_team as plan_rwgps_url_team,
               (c.code = 'TA') as is_team_ride,
               (SELECT COUNT(*) FROM rider_ride rr WHERE rr.ride_id = ri.id AND rr.signed_up_at IS NOT NULL) as signup_count
        FROM ride ri 
        INNER JOIN club c ON ri.club_id = c.id
        LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
        WHERE ri.date >= %s AND ri.event_status = 'UPCOMING'
        ORDER BY ri.date
    """, (today,)).fetchall()

    events_with_defaults = []
    for event in events:
        event_dict = dict(event)
        d = event_dict.get('date')
        event_dict['date_str'] = d if isinstance(d, str) else (d.isoformat() if hasattr(d, 'isoformat') else str(d or ''))
        
        # Add route_name alias for compatibility with templates
        if not event_dict.get('route_name'):
            event_dict['route_name'] = event_dict.get('name')
        
        # Add default time limits if missing
        if not event_dict.get('time_limit_hours') and event_dict.get('distance_km'):
            event_dict['time_limit_hours'] = get_default_time_limit(event_dict['distance_km'])
        
        events_with_defaults.append(event_dict)

    return events_with_defaults

def get_upcoming_rusa_events():
    """Get external RUSA events (not Team Asha). Legacy function for compatibility."""
    all_events = get_all_upcoming_events()
    return [e for e in all_events if not e.get('is_team_ride')]


# ========== PBP FINISHERS ==========

def get_pbp_finishers(season_id):
    """Get PBP finishers for a season, sorted by finish time."""
    return _execute("""
        SELECT r.id, r.rusa_id, r.first_name, r.last_name,
               rp.photo_filename, rp.pbp_2023_status,
               rr.finish_time
        FROM rider r
        JOIN rider_profile rp ON r.id = rp.rider_id
        JOIN rider_ride rr ON r.id = rr.rider_id
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = %s AND ri.ride_type = 'PBP'
              AND rr.status = 'FINISHED'
        ORDER BY rr.finish_time
    """, (season_id,)).fetchall()


# ========== SIGNUPS ==========

def get_signups_for_ride(ride_id):
    """Get all riders signed up for a ride (including those with results)."""
    return _execute("""
        SELECT r.*, rr.status, rr.signed_up_at 
        FROM rider r
        JOIN rider_ride rr ON r.id = rr.rider_id
        WHERE rr.ride_id = %s AND rr.signed_up_at IS NOT NULL
        ORDER BY r.first_name, r.last_name
    """, (ride_id,)).fetchall()

def get_rider_signup_status(rider_id, ride_id):
    """Check if rider is signed up and get their current status."""
    return _execute("""
        SELECT status, signed_up_at, finish_time 
        FROM rider_ride 
        WHERE rider_id = %s AND ride_id = %s
    """, (rider_id, ride_id)).fetchone()

def get_signup_count(ride_id):
    """Get count of riders signed up for a ride."""
    row = _execute("""
        SELECT COUNT(*) as count 
        FROM rider_ride 
        WHERE ride_id = %s AND signed_up_at IS NOT NULL
    """, (ride_id,)).fetchone()
    return row['count'] if row else 0

def signup_rider(rider_id, ride_id):
    """Sign up a rider for a ride."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO rider_ride (rider_id, ride_id, status, signed_up_at) 
            VALUES (%s, %s, 'SIGNED_UP', CURRENT_TIMESTAMP)
        """, (rider_id, ride_id))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False

def remove_signup(rider_id, ride_id):
    """Remove a rider's signup (only if status is SIGNED_UP)."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        DELETE FROM rider_ride 
        WHERE rider_id = %s AND ride_id = %s AND status = 'SIGNED_UP'
    """, (rider_id, ride_id))
    conn.commit()
    return cur.rowcount > 0


# ========== ADMIN WRITES ==========

def create_ride(season_id, club_id, name, ride_type, ride_date, distance_km,
                elevation_ft=None, distance_miles=None, ft_per_mile=None, rwgps_url=None):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""INSERT INTO ride (season_id, club_id, name, ride_type, date, distance_km,
                  elevation_ft, distance_miles, ft_per_mile, rwgps_url, is_team_ride)
                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                  RETURNING id""",
               (season_id, club_id, name, ride_type, ride_date, distance_km,
                elevation_ft, distance_miles, ft_per_mile, rwgps_url))
    new_id = cur.fetchone()['id']
    conn.commit()
    return new_id

def update_rider_ride_status(ride_id, statuses):
    """statuses is a dict of {rider_id: status_string}."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    for rider_id, status in statuses.items():
        cur.execute("""INSERT INTO rider_ride (rider_id, ride_id, status)
                      VALUES (%s, %s, %s)
                      ON CONFLICT(rider_id, ride_id) DO UPDATE SET status = EXCLUDED.status""",
                   (rider_id, ride_id, status))
    conn.commit()

def update_ride_details(ride_id, rwgps_url=None, ride_plan_id=None, start_time=None, 
                       start_location=None, time_limit_hours=None):
    """Update ride details (route, team route, start time, location, time limit)."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    updates = []
    params = []
    
    if rwgps_url is not None:
        updates.append("rwgps_url = %s")
        params.append(rwgps_url if rwgps_url.strip() else None)
    
    if ride_plan_id is not None:
        updates.append("ride_plan_id = %s")
        params.append(ride_plan_id if ride_plan_id else None)
    
    if start_time is not None:
        updates.append("start_time = %s")
        params.append(start_time if start_time.strip() else None)
    
    if start_location is not None:
        updates.append("start_location = %s")
        params.append(start_location if start_location.strip() else None)
    
    if time_limit_hours is not None:
        updates.append("time_limit_hours = %s")
        params.append(time_limit_hours if time_limit_hours else None)
    
    if updates:
        params.append(ride_id)
        sql = f"UPDATE ride SET {', '.join(updates)} WHERE id = %s"
        cur.execute(sql, params)
        conn.commit()
        return True
    return False

# ========== RIDE PLANS ==========

def get_all_ride_plans():
    return _execute("""
        SELECT * FROM ride_plan ORDER BY name
    """).fetchall()

def get_ride_plan_by_slug(slug):
    return _execute("""
        SELECT * FROM ride_plan WHERE slug = %s
    """, (slug,)).fetchone()

def get_ride_plan_stops(ride_plan_id):
    return _execute("""
        SELECT * FROM ride_plan_stop
        WHERE ride_plan_id = %s
        ORDER BY stop_order
    """, (ride_plan_id,)).fetchall()

def find_ride_plan_for_ride(ride_name):
    """Try to match a ride to a ride plan by fuzzy name matching."""
    plans = _execute("SELECT id, name, slug FROM ride_plan").fetchall()
    ride_lower = ride_name.lower()
    for plan in plans:
        plan_lower = plan['name'].lower()
        # Extract key words from both (remove common suffixes like 'plan', '200k', etc.)
        plan_key = plan_lower.replace(' plan', '').replace('-', ' ').strip()
        if plan_key in ride_lower or ride_lower in plan_key:
            return plan
    # Try matching on the core route name (e.g., "Healdsburg" in "SFR 300k Healdsburg")
    for plan in plans:
        plan_words = set(plan['name'].lower().replace('-', ' ').replace('plan', '').split())
        ride_words = set(ride_lower.replace('-', ' ').split())
        # Remove common words
        common_ignore = {'200k', '300k', '400k', '600k', '1000k', 'sfr', 'scr', 'dbc', 'plan', 'route', 'k', '2022', '2023', '2024', '2025', '2026'}
        plan_words -= common_ignore
        ride_words -= common_ignore
        if plan_words and ride_words and plan_words & ride_words:
            return plan
    return None


def update_rider_profile(rider_id, photo_filename=None, bio=None):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if photo_filename and bio is not None:
        cur.execute("""INSERT INTO rider_profile (rider_id, photo_filename, bio)
                      VALUES (%s, %s, %s)
                      ON CONFLICT(rider_id) DO UPDATE SET
                      photo_filename = EXCLUDED.photo_filename, bio = EXCLUDED.bio""",
                   (rider_id, photo_filename, bio))
    elif photo_filename:
        cur.execute("""INSERT INTO rider_profile (rider_id, photo_filename)
                      VALUES (%s, %s)
                      ON CONFLICT(rider_id) DO UPDATE SET photo_filename = EXCLUDED.photo_filename""",
                   (rider_id, photo_filename))
    elif bio is not None:
        cur.execute("""INSERT INTO rider_profile (rider_id, bio)
                      VALUES (%s, %s)
                      ON CONFLICT(rider_id) DO UPDATE SET bio = EXCLUDED.bio""",
                   (rider_id, bio))
    conn.commit()


# ========== USER AUTHENTICATION ==========

def get_user_by_email(email):
    """Get user by email."""
    return _execute("SELECT * FROM app_user WHERE email = %s", (email,)).fetchone()

def get_user_by_google_id(google_id):
    """Get user by Google ID."""
    return _execute("SELECT * FROM app_user WHERE google_id = %s", (google_id,)).fetchone()

def get_user_by_id(user_id):
    """Get user by ID."""
    return _execute("SELECT * FROM app_user WHERE id = %s", (user_id,)).fetchone()

def create_user(email, google_id):
    """Create a new user with Google credentials."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""INSERT INTO app_user (email, google_id, profile_completed, last_login)
                  VALUES (%s, %s, FALSE, CURRENT_TIMESTAMP)
                  RETURNING id, email, google_id, profile_completed, rider_id""",
               (email, google_id))
    user = cur.fetchone()
    conn.commit()
    return dict(user) if user else None

def update_user_login_time(user_id):
    """Update last login timestamp."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("UPDATE app_user SET last_login = CURRENT_TIMESTAMP WHERE id = %s", (user_id,))
    conn.commit()

def complete_user_profile(user_id, rider_id):
    """Link user to rider and mark profile as completed."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""UPDATE app_user SET rider_id = %s, profile_completed = TRUE 
                      WHERE id = %s""",
                   (rider_id, user_id))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        return False

def get_rider_by_name_and_rusa(first_name, last_name, rusa_id):
    """Get rider by exact name match and RUSA ID."""
    return _execute("""
        SELECT * FROM rider 
        WHERE LOWER(first_name) = LOWER(%s) 
        AND LOWER(last_name) = LOWER(%s) 
        AND rusa_id = %s
    """, (first_name, last_name, rusa_id)).fetchone()

def create_rider(first_name, last_name, rusa_id):
    """Create a new rider record."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""INSERT INTO rider (first_name, last_name, rusa_id)
                      VALUES (%s, %s, %s)
                      RETURNING id, first_name, last_name, rusa_id""",
                   (first_name, last_name, rusa_id))
        rider = cur.fetchone()
        conn.commit()
        return dict(rider) if rider else None
    except Exception as e:
        conn.rollback()
        return None

def check_rusa_id_exists(rusa_id):
    """Check if a RUSA ID is already registered."""
    return _execute("SELECT id FROM rider WHERE rusa_id = %s", (rusa_id,)).fetchone()

def is_rider_linked_to_user(rider_id):
    """Check if a rider is already linked to a user account."""
    return _execute("SELECT id FROM app_user WHERE rider_id = %s", (rider_id,)).fetchone()

def get_rider_by_rusa_id(rusa_id):
    """Get rider by RUSA ID."""
    return _execute("SELECT * FROM rider WHERE rusa_id = %s", (rusa_id,)).fetchone()
