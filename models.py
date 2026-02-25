"""Data access layer — all SQL queries live here (PostgreSQL via psycopg2)."""
from datetime import datetime, date
from enum import Enum
import psycopg2.extras
from db import get_db


class RideStatus(str, Enum):
    """
    Enumeration for rider_ride.status field.
    Uses EXISTING database string values - no data migration needed.
    Inherits from str to allow direct comparison with database TEXT values.
    """
    # Pre-ride statuses
    INTERESTED = "INTERESTED"       # Soft interest, considering the ride
    MAYBE = "MAYBE"                 # Tentative, less certain than interested
    GOING = "GOING"                 # Rider officially registered for upcoming ride (formerly SIGNED_UP)
    WITHDRAW = "WITHDRAW"           # Was going but withdrew

    # Post-ride statuses (ride has occurred)
    FINISHED = "FINISHED"           # Successfully completed within time limit
    DNF = "DNF"                     # Did Not Finish
    DNS = "DNS"                     # Did Not Start (signed up but didn't show)
    OTL = "OTL"                     # Over Time Limit (finished but past cutoff)

    @classmethod
    def normalize(cls, value: str) -> 'RideStatus':
        """
        Normalize legacy status values to current enum.
        Raises ValueError if status is invalid.
        """
        if not value or not value.strip():
            raise ValueError("Status cannot be empty")

        # Normalize to uppercase
        val = value.upper().strip()

        # Handle legacy values
        legacy_mapping = {
            'YES': cls.FINISHED,
            '1': cls.FINISHED,
            'NO': cls.DNS,
            '0': cls.DNS,
            'SIGNED_UP': cls.GOING,  # Legacy: SIGNED_UP renamed to GOING
        }

        if val in legacy_mapping:
            return legacy_mapping[val]

        # Try to match enum value
        try:
            return cls[val]
        except KeyError:
            raise ValueError(f"Invalid status: {value}. Must be one of: {', '.join([s.value for s in cls])}")

    @classmethod
    def is_pre_ride(cls, status: 'RideStatus') -> bool:
        """Check if status is pre-ride (INTERESTED, MAYBE, or GOING)."""
        return status in (cls.INTERESTED, cls.MAYBE, cls.GOING)

    @classmethod
    def is_post_ride(cls, status: 'RideStatus') -> bool:
        """Check if status is post-ride (finished, dnf, dns, otl)."""
        return status in (cls.FINISHED, cls.DNF, cls.DNS, cls.OTL)

    @classmethod
    def is_successful(cls, status: 'RideStatus') -> bool:
        """Check if status represents successful completion."""
        return status == cls.FINISHED

    @classmethod
    def can_remove_signup(cls, status: 'RideStatus') -> bool:
        """Check if rider can remove their signup (INTERESTED, MAYBE, or GOING)."""
        return status in (cls.INTERESTED, cls.MAYBE, cls.GOING)


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
        WHERE ri.season_id = %s AND rr.status = %s AND ri.date <= %s
        ORDER BY r.first_name
    """, (season_id, RideStatus.FINISHED.value, today)).fetchall()


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
        WHERE rr.rider_id = %s AND rr.status = %s
          AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
    """, (rider_id, RideStatus.FINISHED.value)).fetchone()
    return dict(row) if row else {'total_rides': 0, 'total_kms': 0}

def get_rider_season_stats(rider_id, season_id):
    """Rides and KMs for a specific season."""
    row = _execute("""
        SELECT COUNT(*) as rides, COALESCE(SUM(ri.distance_km), 0) as kms
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE rr.rider_id = %s AND ri.season_id = %s AND rr.status = %s
          AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
    """, (rider_id, season_id, RideStatus.FINISHED.value)).fetchone()
    return dict(row) if row else {'rides': 0, 'kms': 0}

def get_all_rider_season_stats(season_id):
    """Batch: rides and KMs for ALL riders in a season. Returns dict keyed by rider_id."""
    rows = _execute("""
        SELECT rr.rider_id, COUNT(*) as rides, COALESCE(SUM(ri.distance_km), 0) as kms
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE ri.season_id = %s AND rr.status = %s
        GROUP BY rr.rider_id
    """, (season_id, RideStatus.FINISHED.value)).fetchall()
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
            WHERE rr.rider_id = %s AND ri.season_id = %s AND rr.status = %s
              AND ri.date <= %s
              AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
        """, (rider_id, season_id, RideStatus.FINISHED.value, today)).fetchall()
    else:
        rows = _execute("""
            SELECT ri.distance_km FROM rider_ride rr
            JOIN ride ri ON rr.ride_id = ri.id
            WHERE rr.rider_id = %s AND ri.season_id = %s AND rr.status = %s
              AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
        """, (rider_id, season_id, RideStatus.FINISHED.value)).fetchall()

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
            WHERE ri.season_id = %s AND rr.status = %s AND ri.date <= %s
        """, (season_id, RideStatus.FINISHED.value, today)).fetchall()
    else:
        rows = _execute("""
            SELECT rr.rider_id, ri.distance_km FROM rider_ride rr
            JOIN ride ri ON rr.ride_id = ri.id
            WHERE ri.season_id = %s AND rr.status = %s
        """, (season_id, RideStatus.FINISHED.value)).fetchall()

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
        WHERE rr.status = %s
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
    params = [season_id, RideStatus.FINISHED.value]
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
        WHERE ri.season_id = %s AND rr.status = %s{date_clause}
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
              AND rr.status = %s
        ORDER BY rr.finish_time
    """, (season_id, RideStatus.FINISHED.value)).fetchall()


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
    """Get count of riders signed up for a ride (excludes WITHDRAW status)."""
    row = _execute("""
        SELECT COUNT(*) as count 
        FROM rider_ride 
        WHERE ride_id = %s AND signed_up_at IS NOT NULL AND status != %s
    """, (ride_id, RideStatus.WITHDRAW.value)).fetchone()
    return row['count'] if row else 0

def signup_rider(rider_id, ride_id):
    """Sign up a rider for a ride. Updates status to GOING regardless of current status."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO rider_ride (rider_id, ride_id, status, signed_up_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (rider_id, ride_id) DO UPDATE
              SET status = %s, signed_up_at = CURRENT_TIMESTAMP
        """, (rider_id, ride_id,
              RideStatus.GOING.value,
              RideStatus.GOING.value))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def mark_interested(rider_id, ride_id):
    """Mark a rider as interested in a ride. Updates status to INTERESTED regardless of current status."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO rider_ride (rider_id, ride_id, status, signed_up_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (rider_id, ride_id) DO UPDATE
              SET status = %s, signed_up_at = CURRENT_TIMESTAMP
        """, (rider_id, ride_id, 
              RideStatus.INTERESTED.value,
              RideStatus.INTERESTED.value))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def mark_maybe(rider_id, ride_id):
    """Mark a rider as maybe for a ride. Updates status to MAYBE regardless of current status."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO rider_ride (rider_id, ride_id, status, signed_up_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (rider_id, ride_id) DO UPDATE
              SET status = %s, signed_up_at = CURRENT_TIMESTAMP
        """, (rider_id, ride_id, 
              RideStatus.MAYBE.value,
              RideStatus.MAYBE.value))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def mark_withdraw(rider_id, ride_id):
    """Mark a rider as withdrawn from a ride. Updates status to WITHDRAW."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            UPDATE rider_ride
            SET status = %s, signed_up_at = CURRENT_TIMESTAMP
            WHERE rider_id = %s AND ride_id = %s
        """, (RideStatus.WITHDRAW.value, rider_id, ride_id))
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        return False


def remove_signup(rider_id, ride_id):
    """
    Remove a rider's signup (only if status is pre-ride: GOING, INTERESTED, or MAYBE).

    Returns:
        bool: True if signup was removed, False otherwise

    Raises:
        ValueError: If signup exists but status doesn't allow removal
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Get current status first to provide better error message
    cur.execute("""
        SELECT status FROM rider_ride
        WHERE rider_id = %s AND ride_id = %s
    """, (rider_id, ride_id))
    row = cur.fetchone()

    if row:
        current_status = RideStatus.normalize(row['status'])
        if not RideStatus.can_remove_signup(current_status):
            raise ValueError(f"Cannot remove signup with status '{current_status.value}'. Only pre-ride signups can be removed.")

    # Delete if status allows it (GOING, INTERESTED, or MAYBE can be removed)
    cur.execute("""
        DELETE FROM rider_ride
        WHERE rider_id = %s AND ride_id = %s
        AND status IN (%s, %s, %s)
    """, (rider_id, ride_id, RideStatus.GOING.value, RideStatus.INTERESTED.value, RideStatus.MAYBE.value))

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
    """
    Update rider status for a specific ride.

    Args:
        ride_id: The ride ID
        statuses: Dict mapping rider_id -> status string

    Raises:
        ValueError: If any status value is invalid
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Validate all statuses before making any changes
    normalized_statuses = {}
    for rider_id, status in statuses.items():
        try:
            normalized_statuses[rider_id] = RideStatus.normalize(status).value
        except ValueError as e:
            raise ValueError(f"Invalid status for rider {rider_id}: {e}")

    # Insert/update with validated statuses
    for rider_id, status in normalized_statuses.items():
        cur.execute("""
            INSERT INTO rider_ride (rider_id, ride_id, status)
            VALUES (%s, %s, %s)
            ON CONFLICT(rider_id, ride_id)
            DO UPDATE SET status = EXCLUDED.status
        """, (rider_id, ride_id, status))

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


# ========== STRAVA ==========

def get_strava_connection(rider_id):
    """Get Strava connection for a rider."""
    return _execute(
        "SELECT * FROM strava_connection WHERE rider_id = %s", (rider_id,)
    ).fetchone()

def create_strava_connection(rider_id, strava_athlete_id, access_token,
                              refresh_token, expires_at, scope=None):
    """Create or update Strava connection for a rider."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        INSERT INTO strava_connection
            (rider_id, strava_athlete_id, access_token, refresh_token, expires_at, scope)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (rider_id) DO UPDATE SET
            strava_athlete_id = EXCLUDED.strava_athlete_id,
            access_token = EXCLUDED.access_token,
            refresh_token = EXCLUDED.refresh_token,
            expires_at = EXCLUDED.expires_at,
            scope = EXCLUDED.scope,
            connected_at = CURRENT_TIMESTAMP
    """, (rider_id, strava_athlete_id, access_token, refresh_token, expires_at, scope))
    conn.commit()

def update_strava_tokens(rider_id, access_token, refresh_token, expires_at):
    """Update tokens after a refresh."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        UPDATE strava_connection
        SET access_token = %s, refresh_token = %s, expires_at = %s
        WHERE rider_id = %s
    """, (access_token, refresh_token, expires_at, rider_id))
    conn.commit()

def update_strava_last_sync(rider_id):
    """Update last_sync_at timestamp."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "UPDATE strava_connection SET last_sync_at = CURRENT_TIMESTAMP WHERE rider_id = %s",
        (rider_id,)
    )
    conn.commit()

def delete_strava_connection(rider_id):
    """Delete Strava connection and all stored activities."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("DELETE FROM strava_activity WHERE rider_id = %s", (rider_id,))
    cur.execute("DELETE FROM strava_connection WHERE rider_id = %s", (rider_id,))
    conn.commit()

def upsert_strava_activity(row):
    """Insert or update a Strava activity."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        INSERT INTO strava_activity (
            rider_id, strava_activity_id, name, activity_type, distance,
            moving_time, elapsed_time, total_elevation_gain, start_date,
            start_date_local, average_heartrate, max_heartrate, has_heartrate,
            average_watts, max_watts, weighted_average_watts, kilojoules,
            device_watts, average_speed, max_speed, suffer_score, strava_url
        ) VALUES (
            %(rider_id)s, %(strava_activity_id)s, %(name)s, %(activity_type)s,
            %(distance)s, %(moving_time)s, %(elapsed_time)s, %(total_elevation_gain)s,
            %(start_date)s, %(start_date_local)s, %(average_heartrate)s,
            %(max_heartrate)s, %(has_heartrate)s, %(average_watts)s, %(max_watts)s,
            %(weighted_average_watts)s, %(kilojoules)s, %(device_watts)s,
            %(average_speed)s, %(max_speed)s, %(suffer_score)s, %(strava_url)s
        )
        ON CONFLICT (strava_activity_id) DO UPDATE SET
            name = EXCLUDED.name,
            distance = EXCLUDED.distance,
            moving_time = EXCLUDED.moving_time,
            elapsed_time = EXCLUDED.elapsed_time,
            total_elevation_gain = EXCLUDED.total_elevation_gain,
            average_heartrate = EXCLUDED.average_heartrate,
            max_heartrate = EXCLUDED.max_heartrate,
            has_heartrate = EXCLUDED.has_heartrate,
            average_watts = EXCLUDED.average_watts,
            max_watts = EXCLUDED.max_watts,
            weighted_average_watts = EXCLUDED.weighted_average_watts,
            kilojoules = EXCLUDED.kilojoules,
            device_watts = EXCLUDED.device_watts,
            average_speed = EXCLUDED.average_speed,
            max_speed = EXCLUDED.max_speed,
            suffer_score = EXCLUDED.suffer_score,
            fetched_at = CURRENT_TIMESTAMP
    """, row)
    conn.commit()

def get_strava_activities(rider_id, days=28):
    """Get recent Strava activities for a rider."""
    return _execute("""
        SELECT * FROM strava_activity
        WHERE rider_id = %s AND start_date_local >= NOW() - INTERVAL '%s days'
        ORDER BY start_date_local DESC
    """, (rider_id, days)).fetchall()

def get_strava_activities_for_calendar(rider_id, days=28):
    """Get activities with date column for calendar display."""
    return _execute("""
        SELECT *, DATE(start_date_local) as activity_date
        FROM strava_activity
        WHERE rider_id = %s AND start_date_local >= NOW() - INTERVAL '%s days'
        ORDER BY start_date_local ASC
    """, (rider_id, days)).fetchall()


def get_rider_upcoming_signups(rider_id):
    """Get upcoming rides a rider has signed up for or expressed interest in.

    Returns list of dicts with ride details + signup status, ordered by date.
    """
    today = date.today()
    return _execute("""
        SELECT ri.id, ri.name, ri.date, ri.distance_km, ri.distance_miles,
               ri.elevation_ft, ri.ft_per_mile, ri.time_limit_hours, ri.ride_type,
               ri.rwgps_url, ri.event_status,
               c.code as club_code, c.name as club_name,
               rp.slug as plan_slug, rp.name as plan_name,
               rr.status as signup_status, rr.signed_up_at
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        JOIN club c ON ri.club_id = c.id
        LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
        WHERE rr.rider_id = %s
          AND ri.date >= %s
          AND rr.status IN (%s, %s, %s)
        ORDER BY ri.date ASC
    """, (rider_id, today, RideStatus.GOING.value, RideStatus.INTERESTED.value, RideStatus.MAYBE.value)).fetchall()
