"""Data access layer — all SQL queries live here."""
from datetime import datetime, date
from db import get_db


# ========== SEASONS ==========

def get_all_seasons():
    return get_db().execute("SELECT * FROM season ORDER BY start_date DESC").fetchall()

def get_current_season():
    return get_db().execute("SELECT * FROM season WHERE is_current = 1").fetchone()

def get_season_by_name(name):
    return get_db().execute("SELECT * FROM season WHERE name = ?", (name,)).fetchone()


# ========== RIDERS ==========

def get_all_riders():
    return get_db().execute("""
        SELECT r.*, rp.photo_filename, rp.bio, rp.pbp_2023_registered, rp.pbp_2023_status
        FROM rider r LEFT JOIN rider_profile rp ON r.id = rp.rider_id
        ORDER BY r.first_name
    """).fetchall()

def get_rider_by_rusa(rusa_id):
    return get_db().execute("""
        SELECT r.*, rp.photo_filename, rp.bio, rp.pbp_2023_registered, rp.pbp_2023_status
        FROM rider r LEFT JOIN rider_profile rp ON r.id = rp.rider_id
        WHERE r.rusa_id = ?
    """, (rusa_id,)).fetchone()

def get_riders_for_season(season_id):
    """Get riders who have any participation record in this season."""
    return get_db().execute("""
        SELECT DISTINCT r.*, rp.photo_filename
        FROM rider r
        LEFT JOIN rider_profile rp ON r.id = rp.rider_id
        JOIN rider_ride rr ON r.id = rr.rider_id
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = ?
        ORDER BY r.first_name
    """, (season_id,)).fetchall()


# ========== RIDES ==========

def get_rides_for_season(season_id):
    return get_db().execute("""
        SELECT ri.*, c.code as club_code, c.name as club_name
        FROM ride ri LEFT JOIN club c ON ri.club_id = c.id
        WHERE ri.season_id = ?
        ORDER BY ri.date
    """, (season_id,)).fetchall()

def get_ride_by_id(ride_id):
    return get_db().execute("""
        SELECT ri.*, c.code as club_code, c.name as club_name
        FROM ride ri LEFT JOIN club c ON ri.club_id = c.id
        WHERE ri.id = ?
    """, (ride_id,)).fetchone()

def get_upcoming_rides():
    today = date.today().isoformat()
    return get_db().execute("""
        SELECT ri.*, c.code as club_code,
               (SELECT COUNT(*) FROM rider_ride_signup rrs WHERE rrs.ride_id = ri.id) as signup_count
        FROM ride ri LEFT JOIN club c ON ri.club_id = c.id
        WHERE ri.date >= ? AND ri.is_team_ride = 1
        ORDER BY ri.date
    """, (today,)).fetchall()

def get_past_rides_for_season(season_id):
    today = date.today().isoformat()
    return get_db().execute("""
        SELECT ri.*, c.code as club_code
        FROM ride ri LEFT JOIN club c ON ri.club_id = c.id
        WHERE ri.season_id = ? AND ri.date < ?
        ORDER BY ri.date
    """, (season_id, today)).fetchall()

def get_clubs():
    return get_db().execute("SELECT * FROM club ORDER BY name").fetchall()


# ========== PARTICIPATION ==========

def get_participation_matrix(season_id):
    """Return {rider_id: {ride_id: {status, finish_time}}} for a season."""
    rows = get_db().execute("""
        SELECT rr.rider_id, rr.ride_id, rr.status, rr.finish_time
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = ?
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
    return get_db().execute("""
        SELECT rr.status, rr.finish_time, ri.name as ride_name, ri.date, ri.distance_km,
               ri.elevation_ft, ri.ft_per_mile, ri.rwgps_url, c.code as club_code
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        LEFT JOIN club c ON ri.club_id = c.id
        WHERE rr.rider_id = ? AND ri.season_id = ?
        ORDER BY ri.date
    """, (rider_id, season_id)).fetchall()

def get_rider_career_stats(rider_id):
    """Total rides completed, total KMs, across all seasons."""
    row = get_db().execute("""
        SELECT COUNT(*) as total_rides,
               COALESCE(SUM(ri.distance_km), 0) as total_kms
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE rr.rider_id = ? AND LOWER(rr.status) = 'yes'
    """, (rider_id,)).fetchone()
    return dict(row) if row else {'total_rides': 0, 'total_kms': 0}

def get_rider_season_stats(rider_id, season_id):
    """Rides and KMs for a specific season."""
    row = get_db().execute("""
        SELECT COUNT(*) as rides, COALESCE(SUM(ri.distance_km), 0) as kms
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE rr.rider_id = ? AND ri.season_id = ? AND LOWER(rr.status) = 'yes'
    """, (rider_id, season_id)).fetchone()
    return dict(row) if row else {'rides': 0, 'kms': 0}


# ========== SR DETECTION ==========

def detect_sr_for_rider_season(rider_id, season_id, date_filter=False):
    """Count complete SR sets (200+300+400+600) for a rider in a season.
    Returns count (min across all four buckets), or 0."""
    today = date.today().isoformat()
    if date_filter:
        rows = get_db().execute("""
            SELECT ri.distance_km FROM rider_ride rr
            JOIN ride ri ON rr.ride_id = ri.id
            WHERE rr.rider_id = ? AND ri.season_id = ? AND LOWER(rr.status) = 'yes'
            AND ri.date <= ?
        """, (rider_id, season_id, today)).fetchall()
    else:
        rows = get_db().execute("""
            SELECT ri.distance_km FROM rider_ride rr
            JOIN ride ri ON rr.ride_id = ri.id
            WHERE rr.rider_id = ? AND ri.season_id = ? AND LOWER(rr.status) = 'yes'
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
    rider = get_db().execute("SELECT rusa_id FROM rider WHERE id = ?", (rider_id,)).fetchone()
    if rider and rider['rusa_id'] == 14680:
        total += 1
    return total


# ========== ALL-TIME STATS ==========

def get_all_time_stats():
    db = get_db()
    # Unique riders who completed at least 1 ride
    riders = db.execute("""
        SELECT COUNT(DISTINCT r.id) FROM rider r
        JOIN rider_ride rr ON r.id = rr.rider_id
        WHERE LOWER(rr.status) = 'yes'
    """).fetchone()[0]

    # Total completed rides
    rides = db.execute("""
        SELECT COUNT(*) FROM rider_ride WHERE LOWER(status) = 'yes'
    """).fetchone()[0]

    # Total KMs
    kms = db.execute("""
        SELECT COALESCE(SUM(ri.distance_km), 0) FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE LOWER(rr.status) = 'yes'
    """).fetchone()[0]

    # Unique SR earners
    all_riders = db.execute("SELECT id FROM rider").fetchall()
    seasons = get_all_seasons()
    current = get_current_season()
    sr_riders = set()
    for r in all_riders:
        for s in seasons:
            df = s['id'] == current['id'] if current else False
            if detect_sr_for_rider_season(r['id'], s['id'], date_filter=df) > 0:
                sr_riders.add(r['id'])
    # Mihir's India SR
    mihir = db.execute("SELECT id FROM rider WHERE rusa_id = 14680").fetchone()
    if mihir:
        sr_riders.add(mihir['id'])

    return {
        'riders': riders,
        'rides': rides,
        'kms': kms,
        'srs': len(sr_riders)
    }


# ========== SEASON STATS ==========

def get_season_stats(season_id):
    db = get_db()
    current = get_current_season()
    is_current = current and current['id'] == season_id

    riders = get_riders_for_season(season_id)

    # Active riders (at least 1 completed ride)
    active = db.execute("""
        SELECT COUNT(DISTINCT rr.rider_id) FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = ? AND LOWER(rr.status) = 'yes'
    """, (season_id,)).fetchone()[0]

    total_rides = db.execute("""
        SELECT COUNT(*) FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = ? AND LOWER(rr.status) = 'yes'
    """, (season_id,)).fetchone()[0]

    total_kms = db.execute("""
        SELECT COALESCE(SUM(ri.distance_km), 0) FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = ? AND LOWER(rr.status) = 'yes'
    """, (season_id,)).fetchone()[0]

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

def get_upcoming_rusa_events():
    today = date.today().isoformat()
    return get_db().execute("""
        SELECT * FROM upcoming_rusa_event
        WHERE date >= ?
        ORDER BY date
    """, (today,)).fetchall()


# ========== SIGNUPS ==========

def get_signups_for_ride(ride_id):
    return get_db().execute("""
        SELECT r.* FROM rider r
        JOIN rider_ride_signup rrs ON r.id = rrs.rider_id
        WHERE rrs.ride_id = ?
        ORDER BY r.first_name
    """, (ride_id,)).fetchall()

def signup_rider(rider_id, ride_id):
    db = get_db()
    try:
        db.execute("INSERT INTO rider_ride_signup (rider_id, ride_id) VALUES (?, ?)",
                   (rider_id, ride_id))
        db.commit()
        return True
    except Exception:
        return False

def remove_signup(rider_id, ride_id):
    db = get_db()
    db.execute("DELETE FROM rider_ride_signup WHERE rider_id = ? AND ride_id = ?",
               (rider_id, ride_id))
    db.commit()


# ========== ADMIN WRITES ==========

def create_ride(season_id, club_id, name, ride_type, ride_date, distance_km,
                elevation_ft=None, distance_miles=None, ft_per_mile=None, rwgps_url=None):
    db = get_db()
    db.execute("""INSERT INTO ride (season_id, club_id, name, ride_type, date, distance_km,
                  elevation_ft, distance_miles, ft_per_mile, rwgps_url, is_team_ride)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
               (season_id, club_id, name, ride_type, ride_date, distance_km,
                elevation_ft, distance_miles, ft_per_mile, rwgps_url))
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]

def update_rider_ride_status(ride_id, statuses):
    """statuses is a dict of {rider_id: status_string}."""
    db = get_db()
    for rider_id, status in statuses.items():
        db.execute("""INSERT INTO rider_ride (rider_id, ride_id, status)
                      VALUES (?, ?, ?)
                      ON CONFLICT(rider_id, ride_id) DO UPDATE SET status = excluded.status""",
                   (rider_id, ride_id, status))
    db.commit()

def update_rider_profile(rider_id, photo_filename=None, bio=None):
    db = get_db()
    if photo_filename and bio is not None:
        db.execute("""INSERT INTO rider_profile (rider_id, photo_filename, bio)
                      VALUES (?, ?, ?)
                      ON CONFLICT(rider_id) DO UPDATE SET
                      photo_filename = excluded.photo_filename, bio = excluded.bio""",
                   (rider_id, photo_filename, bio))
    elif photo_filename:
        db.execute("""INSERT INTO rider_profile (rider_id, photo_filename)
                      VALUES (?, ?)
                      ON CONFLICT(rider_id) DO UPDATE SET photo_filename = excluded.photo_filename""",
                   (rider_id, photo_filename))
    elif bio is not None:
        db.execute("""INSERT INTO rider_profile (rider_id, bio)
                      VALUES (?, ?)
                      ON CONFLICT(rider_id) DO UPDATE SET bio = excluded.bio""",
                   (rider_id, bio))
    db.commit()
