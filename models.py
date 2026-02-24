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
    """Get riders who have completed at least 1 ride (status=yes) in this season, only counting past rides."""
    today = date.today().isoformat()
    return _execute("""
        SELECT DISTINCT r.*, rp.photo_filename
        FROM rider r
        LEFT JOIN rider_profile rp ON r.id = rp.rider_id
        JOIN rider_ride rr ON r.id = rr.rider_id
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = %s AND LOWER(rr.status) = 'yes' AND ri.date <= %s
        ORDER BY r.first_name
    """, (season_id, today)).fetchall()


# ========== RIDES ==========

def get_rides_for_season(season_id):
    return _execute("""
        SELECT ri.*, c.code as club_code, c.name as club_name
        FROM ride ri LEFT JOIN club c ON ri.club_id = c.id
        WHERE ri.season_id = %s
        ORDER BY ri.date
    """, (season_id,)).fetchall()

def get_ride_by_id(ride_id):
    return _execute("""
        SELECT ri.*, c.code as club_code, c.name as club_name
        FROM ride ri LEFT JOIN club c ON ri.club_id = c.id
        WHERE ri.id = %s
    """, (ride_id,)).fetchone()

def get_upcoming_rides():
    today = date.today().isoformat()
    return _execute("""
        SELECT ri.*, c.code as club_code,
               (SELECT COUNT(*) FROM rider_ride_signup rrs WHERE rrs.ride_id = ri.id) as signup_count
        FROM ride ri LEFT JOIN club c ON ri.club_id = c.id
        WHERE ri.date >= %s AND ri.is_team_ride = TRUE
        ORDER BY ri.date
    """, (today,)).fetchall()

def get_past_rides_for_season(season_id):
    today = date.today().isoformat()
    return _execute("""
        SELECT ri.*, c.code as club_code
        FROM ride ri LEFT JOIN club c ON ri.club_id = c.id
        WHERE ri.season_id = %s AND ri.date < %s
        ORDER BY ri.date
    """, (season_id, today)).fetchall()

def get_clubs():
    return _execute("SELECT * FROM club ORDER BY name").fetchall()


# ========== PARTICIPATION ==========

def get_participation_matrix(season_id):
    """Return {rider_id: {ride_id: {status, finish_time}}} for a season."""
    rows = _execute("""
        SELECT rr.rider_id, rr.ride_id, rr.status, rr.finish_time
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = %s
    """, (season_id,)).fetchall()
    matrix = {}
    for row in rows:
        rid = row['rider_id']
        if rid not in matrix:
            matrix[rid] = {}
        matrix[rid][row['ride_id']] = {
            'status': row['status'],
            'finish_time': row['finish_time']
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
        ORDER BY ri.date
    """, (rider_id, season_id)).fetchall()

def get_rider_career_stats(rider_id):
    """Total rides completed, total KMs, across all seasons."""
    row = _execute("""
        SELECT COUNT(*) as total_rides,
               COALESCE(SUM(ri.distance_km), 0) as total_kms
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE rr.rider_id = %s AND LOWER(rr.status) = 'yes'
    """, (rider_id,)).fetchone()
    return dict(row) if row else {'total_rides': 0, 'total_kms': 0}

def get_rider_season_stats(rider_id, season_id):
    """Rides and KMs for a specific season."""
    row = _execute("""
        SELECT COUNT(*) as rides, COALESCE(SUM(ri.distance_km), 0) as kms
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE rr.rider_id = %s AND ri.season_id = %s AND LOWER(rr.status) = 'yes'
    """, (rider_id, season_id)).fetchone()
    return dict(row) if row else {'rides': 0, 'kms': 0}


# ========== SR DETECTION ==========

def detect_sr_for_rider_season(rider_id, season_id, date_filter=False):
    """Count complete SR sets (200+300+400+600) for a rider in a season.
    Returns count (min across all four buckets), or 0."""
    today = date.today().isoformat()
    if date_filter:
        rows = _execute("""
            SELECT ri.distance_km FROM rider_ride rr
            JOIN ride ri ON rr.ride_id = ri.id
            WHERE rr.rider_id = %s AND ri.season_id = %s AND LOWER(rr.status) = 'yes'
            AND ri.date <= %s
        """, (rider_id, season_id, today)).fetchall()
    else:
        rows = _execute("""
            SELECT ri.distance_km FROM rider_ride rr
            JOIN ride ri ON rr.ride_id = ri.id
            WHERE rr.rider_id = %s AND ri.season_id = %s AND LOWER(rr.status) = 'yes'
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

def get_rider_total_srs(rider_id):
    """Total SRs across all seasons."""
    seasons = get_all_seasons()
    current = get_current_season()
    total = 0
    for s in seasons:
        df = s['id'] == current['id'] if current else False
        total += detect_sr_for_rider_season(rider_id, s['id'], date_filter=df)
    # Mihir's 2014/15 SR in India
    rider = _execute("SELECT rusa_id FROM rider WHERE id = %s", (rider_id,)).fetchone()
    if rider and rider['rusa_id'] == 14680:
        total += 1
    return total


# ========== ALL-TIME STATS ==========

def get_all_time_stats():
    # Unique riders who completed at least 1 ride
    riders = _execute("""
        SELECT COUNT(DISTINCT r.id) as cnt FROM rider r
        JOIN rider_ride rr ON r.id = rr.rider_id
        WHERE LOWER(rr.status) = 'yes'
    """).fetchone()['cnt']

    # Total completed rides
    rides = _execute("""
        SELECT COUNT(*) as cnt FROM rider_ride WHERE LOWER(status) = 'yes'
    """).fetchone()['cnt']

    # Total KMs
    kms = _execute("""
        SELECT COALESCE(SUM(ri.distance_km), 0) as total FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE LOWER(rr.status) = 'yes'
    """).fetchone()['total']

    # Unique SR earners
    all_riders = _execute("SELECT id FROM rider").fetchall()
    seasons = get_all_seasons()
    current = get_current_season()
    sr_riders = set()
    for r in all_riders:
        for s in seasons:
            df = s['id'] == current['id'] if current else False
            if detect_sr_for_rider_season(r['id'], s['id'], date_filter=df) > 0:
                sr_riders.add(r['id'])
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

    riders = get_riders_for_season(season_id)

    date_clause = ""
    params = [season_id]
    if past_only:
        today = date.today().isoformat()
        date_clause = " AND ri.date <= %s"
        params.append(today)

    # Active riders (at least 1 completed ride)
    active = _execute(f"""
        SELECT COUNT(DISTINCT rr.rider_id) as cnt FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = %s AND LOWER(rr.status) = 'yes'{date_clause}
    """, params).fetchone()['cnt']

    total_rides = _execute(f"""
        SELECT COUNT(*) as cnt FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = %s AND LOWER(rr.status) = 'yes'{date_clause}
    """, params).fetchone()['cnt']

    total_kms = _execute(f"""
        SELECT COALESCE(SUM(ri.distance_km), 0) as total FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = %s AND LOWER(rr.status) = 'yes'{date_clause}
    """, params).fetchone()['total']

    # SR counts
    sr_count = 0
    sr_rider_count = 0
    for r in riders:
        n = detect_sr_for_rider_season(r['id'], season_id, date_filter=is_current)
        sr_count += n
        if n > 0:
            sr_rider_count += 1

    return {
        'active_riders': active,
        'total_rides': total_rides,
        'total_kms': total_kms,
        'sr_count': sr_count,
        'sr_rider_count': sr_rider_count,
    }


# ========== UPCOMING RUSA EVENTS ==========

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

def get_upcoming_rusa_events():
    today = date.today()
    events = _execute("""
        SELECT * FROM upcoming_rusa_event
        WHERE date >= %s AND event_status IN ('ACTIVE', 'INPROGRESS')
        ORDER BY date
    """, (today,)).fetchall()

    # Add default time limits for events that don't have them
    events_with_defaults = []
    for event in events:
        event_dict = dict(event)
        # Convert date to string for template slicing (PG returns datetime.date)
        d = event_dict.get('date')
        event_dict['date_str'] = d.isoformat() if hasattr(d, 'isoformat') else str(d or '')
        if not event_dict.get('time_limit_hours') and event_dict.get('distance_km'):
            event_dict['time_limit_hours'] = get_default_time_limit(event_dict['distance_km'])
        events_with_defaults.append(event_dict)

    return events_with_defaults


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
              AND LOWER(rr.status) = 'yes'
        ORDER BY rr.finish_time
    """, (season_id,)).fetchall()


# ========== SIGNUPS ==========

def get_signups_for_ride(ride_id):
    return _execute("""
        SELECT r.* FROM rider r
        JOIN rider_ride_signup rrs ON r.id = rrs.rider_id
        WHERE rrs.ride_id = %s
        ORDER BY r.first_name
    """, (ride_id,)).fetchall()

def signup_rider(rider_id, ride_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("INSERT INTO rider_ride_signup (rider_id, ride_id) VALUES (%s, %s)",
                    (rider_id, ride_id))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False

def remove_signup(rider_id, ride_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("DELETE FROM rider_ride_signup WHERE rider_id = %s AND ride_id = %s",
                (rider_id, ride_id))
    conn.commit()


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
