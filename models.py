"""Data access layer — all SQL queries live here (PostgreSQL via psycopg2)."""
from datetime import datetime, date
from enum import Enum
import psycopg2.extras
from db import get_db
from cache import cache, CACHE_TIMEOUT


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

@cache.memoize(CACHE_TIMEOUT)
def get_all_seasons():
    return _execute("SELECT * FROM season ORDER BY start_date DESC").fetchall()

@cache.memoize(CACHE_TIMEOUT)
def get_current_season():
    return _execute("SELECT * FROM season WHERE is_current = TRUE").fetchone()

@cache.memoize(CACHE_TIMEOUT)
def get_season_by_name(name):
    return _execute("SELECT * FROM season WHERE name = %s", (name,)).fetchone()


# ========== RIDERS ==========

@cache.memoize(CACHE_TIMEOUT)
def get_all_riders():
    return _execute("""
        SELECT r.*, rp.photo_filename, rp.bio, rp.pbp_2023_registered, rp.pbp_2023_status
        FROM rider r LEFT JOIN rider_profile rp ON r.id = rp.rider_id
        ORDER BY r.first_name
    """).fetchall()

def get_rider_by_rusa(rusa_id):
    """Get rider by RUSA ID. NOT CACHED - rider data should not be cached in serverless environments."""
    return _execute("""
        SELECT r.*, rp.photo_filename, rp.bio, rp.pbp_2023_registered, rp.pbp_2023_status, rp.strava_data_private
        FROM rider r LEFT JOIN rider_profile rp ON r.id = rp.rider_id
        WHERE r.rusa_id = %s
    """, (rusa_id,)).fetchone()


def get_rider_by_id(rider_id):
    """Get rider by primary key ID. Returns dict or None."""
    return _execute(
        "SELECT * FROM rider WHERE id = %s",
        (rider_id,)
    ).fetchone()


@cache.memoize(CACHE_TIMEOUT)
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

@cache.memoize(CACHE_TIMEOUT)
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

@cache.memoize(CACHE_TIMEOUT)
def get_rides_for_season(season_id):
    """Get all rides for a season with club info."""
    return _execute("""
        SELECT ri.*,
               c.code as club_code,
               c.name as club_name,
               c.region as region,
               rp.slug as plan_slug,
               rp.start_time as plan_start_time,
               (c.code = 'TA') as is_team_ride
        FROM ride ri
        INNER JOIN club c ON ri.club_id = c.id
        LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
        WHERE ri.season_id = %s
        ORDER BY ri.date
    """, (season_id,)).fetchall()

@cache.memoize(CACHE_TIMEOUT)
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

@cache.memoize(CACHE_TIMEOUT)
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

@cache.memoize(CACHE_TIMEOUT)
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

@cache.memoize(CACHE_TIMEOUT)
def get_clubs():
    return _execute("SELECT * FROM club ORDER BY name").fetchall()


# ========== PARTICIPATION ==========

@cache.memoize(CACHE_TIMEOUT)
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

#  NOT CACHED - rider-specific data should not be cached in serverless environments
def get_rider_participation(rider_id, season_id):
    return _execute("""
        SELECT rr.status, rr.finish_time, ri.id as ride_id, ri.name as ride_name,
               ri.date, ri.distance_km, ri.elevation_ft, ri.ft_per_mile, ri.rwgps_url,
               ri.ride_plan_id, c.code as club_code, rp.slug as plan_slug
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        LEFT JOIN club c ON ri.club_id = c.id
        LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
        WHERE rr.rider_id = %s AND ri.season_id = %s
          AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
        ORDER BY ri.date
    """, (rider_id, season_id)).fetchall()

# NOT CACHED - rider-specific data should not be cached in serverless environments
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

# NOT CACHED - rider-specific data should not be cached in serverless environments
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

@cache.memoize(CACHE_TIMEOUT)
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

@cache.memoize(CACHE_TIMEOUT)
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

@cache.memoize(CACHE_TIMEOUT)
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

@cache.memoize(CACHE_TIMEOUT)
def get_rider_total_srs(rider_id):
    """Total SRs across all seasons."""
    seasons = get_all_seasons()
    current = get_current_season()
    total = 0
    for s in seasons:
        df = s['id'] == current['id'] if current else False
        total += detect_sr_for_rider_season(rider_id, s['id'], date_filter=df)
    return total


@cache.memoize(CACHE_TIMEOUT)
def detect_r12_awards(rider_id):
    """Detect R-12 awards: 12 consecutive months each with at least one 200+km finished ride.

    Returns a list of dicts with 'start_month' and 'end_month' (YYYY-MM strings)
    for each R-12 completion. A rider can earn multiple R-12s.
    """
    rows = _execute("""
        SELECT DISTINCT TO_CHAR(ri.date, 'YYYY-MM') as ride_month
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE rr.rider_id = %s
          AND rr.status = %s
          AND ri.distance_km >= 200
          AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
        ORDER BY ride_month
    """, (rider_id, RideStatus.FINISHED.value)).fetchall()

    if not rows:
        return []

    months = [r['ride_month'] for r in rows]

    # Convert to (year, month) tuples for consecutive checking
    def parse_ym(ym_str):
        y, m = ym_str.split('-')
        return int(y), int(m)

    def month_diff(ym1, ym2):
        """Number of months between two (year, month) tuples."""
        return (ym2[0] - ym1[0]) * 12 + (ym2[1] - ym1[1])

    parsed = [parse_ym(m) for m in months]

    # Find all runs of consecutive months
    r12_awards = []
    run_start = 0
    for i in range(1, len(parsed)):
        if month_diff(parsed[i - 1], parsed[i]) != 1:
            # Break in consecutive months — check if we had 12+ consecutive
            run_len = i - run_start
            if run_len >= 12:
                # Award one R-12 per non-overlapping 12-month block
                j = 0
                while j + 12 <= run_len:
                    s = parsed[run_start + j]
                    e = parsed[run_start + j + 11]
                    r12_awards.append({
                        'start_month': f'{s[0]}-{s[1]:02d}',
                        'end_month': f'{e[0]}-{e[1]:02d}',
                        'end_year': e[0],
                    })
                    j += 12
            run_start = i

    # Check final run
    run_len = len(parsed) - run_start
    if run_len >= 12:
        j = 0
        while j + 12 <= run_len:
            s = parsed[run_start + j]
            e = parsed[run_start + j + 11]
            r12_awards.append({
                'start_month': f'{s[0]}-{s[1]:02d}',
                'end_month': f'{e[0]}-{e[1]:02d}',
                'end_year': e[0],
            })
            j += 12

    return r12_awards


# ========== ALL-TIME STATS ==========

@cache.memoize(CACHE_TIMEOUT)
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
    """, (RideStatus.FINISHED.value,)).fetchone()
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

@cache.memoize(CACHE_TIMEOUT)
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

@cache.memoize(CACHE_TIMEOUT)
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

@cache.memoize(CACHE_TIMEOUT)
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
               rp.start_time as plan_start_time,
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

@cache.memoize(CACHE_TIMEOUT)
def get_upcoming_rusa_events():
    """Get external RUSA events (not Team Asha). Legacy function for compatibility."""
    all_events = get_all_upcoming_events()
    return [e for e in all_events if not e.get('is_team_ride')]


# ========== PBP FINISHERS ==========

@cache.memoize(CACHE_TIMEOUT)
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

@cache.memoize(CACHE_TIMEOUT)
def get_signups_for_ride(ride_id):
    """Get all riders signed up for a ride (including those with results)."""
    return _execute("""
        SELECT r.*, rr.status, rr.signed_up_at 
        FROM rider r
        JOIN rider_ride rr ON r.id = rr.rider_id
        WHERE rr.ride_id = %s AND rr.signed_up_at IS NOT NULL
        ORDER BY r.first_name, r.last_name
    """, (ride_id,)).fetchall()

# NOT CACHED - rider-specific data should not be cached in serverless environments
def get_rider_signup_status(rider_id, ride_id):
    """Check if rider is signed up and get their current status."""
    return _execute("""
        SELECT status, signed_up_at, finish_time 
        FROM rider_ride 
        WHERE rider_id = %s AND ride_id = %s
    """, (rider_id, ride_id)).fetchone()

@cache.memoize(CACHE_TIMEOUT)
def get_signup_count(ride_id):
    """Get count of riders signed up for a ride (excludes WITHDRAW status)."""
    row = _execute("""
        SELECT COUNT(*) as count 
        FROM rider_ride 
        WHERE ride_id = %s AND signed_up_at IS NOT NULL AND status != %s
    """, (ride_id, RideStatus.WITHDRAW.value)).fetchone()
    return row['count'] if row else 0

@cache.memoize(CACHE_TIMEOUT)
def get_signup_counts_batch(ride_ids):
    """Get signup counts for multiple rides in one query. Returns dict {ride_id: count}."""
    if not ride_ids:
        return {}
    
    placeholders = ','.join(['%s'] * len(ride_ids))
    rows = _execute(f"""
        SELECT ride_id, COUNT(*) as count 
        FROM rider_ride 
        WHERE ride_id IN ({placeholders}) 
          AND signed_up_at IS NOT NULL 
          AND status != %s
        GROUP BY ride_id
    """, tuple(ride_ids) + (RideStatus.WITHDRAW.value,)).fetchall()
    
    counts = {r['ride_id']: r['count'] for r in rows}
    # Fill in zeros for rides with no signups
    return {ride_id: counts.get(ride_id, 0) for ride_id in ride_ids}

@cache.memoize(CACHE_TIMEOUT)
def get_rider_signup_statuses_batch(rider_id, ride_ids):
    """Get signup statuses for a rider across multiple rides in one query. Returns dict {ride_id: status_dict}."""
    if not ride_ids or not rider_id:
        return {}
    
    placeholders = ','.join(['%s'] * len(ride_ids))
    rows = _execute(f"""
        SELECT ride_id, status, signed_up_at, finish_time 
        FROM rider_ride 
        WHERE rider_id = %s AND ride_id IN ({placeholders})
    """, (rider_id,) + tuple(ride_ids)).fetchall()
    
    return {r['ride_id']: {'status': r['status'], 'signed_up_at': r['signed_up_at'], 'finish_time': r['finish_time']} for r in rows}

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


def admin_delete_rider_ride(rider_id, ride_id):
    """Admin-only: Remove a rider_ride record regardless of status.

    Unlike remove_signup() which is user-facing and restricted to pre-ride
    statuses, this function allows admins to delete any participation record
    (e.g. correcting data entry errors).

    Returns:
        bool: True if a record was deleted, False if no matching record found.
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM rider_ride WHERE rider_id = %s AND ride_id = %s",
        (rider_id, ride_id),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    if deleted:
        cache.clear()
    return deleted


# ========== ADMIN WRITES ==========

def update_base_plan_stop(stop_id, changes):
    """Admin-only: Update a base plan stop's details."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    updates = []
    params = []

    # Core metrics
    if 'distance_miles' in changes:
        updates.append("distance_miles = %s")
        params.append(changes['distance_miles'])

    if 'segment_time_min' in changes:
        updates.append("segment_time_min = %s")
        params.append(changes['segment_time_min'])

    if 'elevation_gain' in changes:
        updates.append("elevation_gain = %s")
        params.append(changes['elevation_gain'])

    # Break / stop details
    if 'stop_duration_min' in changes:
        updates.append("stop_duration_min = %s")
        params.append(changes['stop_duration_min'])

    if 'stop_name' in changes:
        updates.append("stop_name = %s")
        # Store None (NULL) for empty strings so the template condition
        # `stop.stop_name and stop.stop_duration_min > 0` works correctly.
        params.append(changes['stop_name'] or None)

    if 'location' in changes:
        updates.append("location = %s")
        params.append(changes['location'] or None)

    if 'stop_type' in changes:
        updates.append("stop_type = %s")
        params.append(changes['stop_type'] or None)

    if 'notes' in changes:
        updates.append("notes = %s")
        params.append(changes['notes'] or None)

    if not updates:
        return False

    params.append(stop_id)
    sql = f"UPDATE ride_plan_stop SET {', '.join(updates)} WHERE id = %s"

    cur.execute(sql, params)
    row_count = cur.rowcount  # save before subsequent queries change it
    conn.commit()

    # Clear cache for the affected plan
    cur.execute("SELECT ride_plan_id FROM ride_plan_stop WHERE id = %s", (stop_id,))
    result = cur.fetchone()
    if result:
        plan_id = result['ride_plan_id']
        cache.delete_memoized(get_ride_plan_stops, plan_id)
        # Recalculate cum_time_min for all stops in this plan
        if any(k in changes for k in ('segment_time_min', 'stop_duration_min', 'distance_miles')):
            recalculate_base_plan_cumulative(plan_id, cur, conn)
            conn.commit()  # commit the recalculated cumulative times
    cache.clear()

    return row_count > 0


def recalculate_base_plan_cumulative(ride_plan_id, cur=None, conn=None):
    """Recalculate cum_time_min for all stops and sync ride_plan summary.

    cum_time_min = running sum of (segment_time_min + stop_duration_min).
    Also updates ride_plan.total_moving_time_min, total_elapsed_time_min,
    and total_break_time_min to stay in sync with the stops.
    """
    own_conn = False
    if cur is None:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        own_conn = True

    cur.execute(
        "SELECT id, stop_order, segment_time_min, stop_duration_min, bookend_time_min "
        "FROM ride_plan_stop WHERE ride_plan_id = %s ORDER BY stop_order",
        (ride_plan_id,)
    )
    stops = cur.fetchall()

    cum = 0
    total_moving = 0
    total_break = 0
    for s in stops:
        seg = s['segment_time_min'] or 0
        brk = s['stop_duration_min'] or 0
        cum += seg + brk
        total_moving += seg
        total_break += brk
        arrival = cum - brk
        bookend = s.get('bookend_time_min')
        time_bank = (bookend - arrival) if bookend else None
        cur.execute(
            "UPDATE ride_plan_stop SET cum_time_min = %s, time_bank_min = %s WHERE id = %s",
            (cum, time_bank, s['id'])
        )

    # Sync ride_plan summary fields from stops (single source of truth)
    cur.execute(
        "UPDATE ride_plan SET total_moving_time_min = %s, "
        "total_elapsed_time_min = %s, total_break_time_min = %s "
        "WHERE id = %s",
        (total_moving, cum, total_break, ride_plan_id)
    )

    if own_conn:
        conn.commit()

def insert_ride_plan_stop(ride_plan_id, stop_order, location, stop_type='waypoint',
                         distance_miles=None, elevation_gain=None, notes=None):
    """Insert a new stop into a ride plan and reorder subsequent stops."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Shift existing stops at or after this position
    cur.execute(
        "UPDATE ride_plan_stop SET stop_order = stop_order + 1 WHERE ride_plan_id = %s AND stop_order >= %s",
        (ride_plan_id, stop_order)
    )
    cur.execute(
        """INSERT INTO ride_plan_stop (ride_plan_id, stop_order, location, stop_type, distance_miles, elevation_gain, notes)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (ride_plan_id, stop_order, location, stop_type, distance_miles, elevation_gain, notes)
    )
    result = cur.fetchone()
    conn.commit()
    recalculate_base_plan_cumulative(ride_plan_id)
    cache.clear()
    return result


def delete_ride_plan_stop(stop_id):
    """Delete a stop from a ride plan and reorder remaining stops."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Get the stop info before deleting
    cur.execute("SELECT ride_plan_id, stop_order FROM ride_plan_stop WHERE id = %s", (stop_id,))
    stop = cur.fetchone()
    if not stop:
        return False
    cur.execute("DELETE FROM ride_plan_stop WHERE id = %s", (stop_id,))
    # Reorder remaining stops
    cur.execute(
        "UPDATE ride_plan_stop SET stop_order = stop_order - 1 WHERE ride_plan_id = %s AND stop_order > %s",
        (stop['ride_plan_id'], stop['stop_order'])
    )
    conn.commit()
    recalculate_base_plan_cumulative(stop['ride_plan_id'])
    cache.clear()
    return True


def get_ride_plan_by_rwgps_route_id(route_id):
    """Check if a ride plan already exists for a given RWGPS route ID."""
    return _execute(
        "SELECT * FROM ride_plan WHERE rwgps_route_id = %s", (route_id,)
    ).fetchone()


def create_ride_plan_from_rwgps(plan_data, stops_data):
    """Insert or update a ride plan and its stops generated from RWGPS data.

    Uses upsert on slug to handle duplicates. Deletes old stops and re-inserts.

    Args:
        plan_data: dict with ride_plan column values
        stops_data: list of dicts with ride_plan_stop column values

    Returns:
        New/updated ride_plan id
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Upsert ride_plan
    cur.execute("""
        INSERT INTO ride_plan (name, slug, total_distance_miles, total_elevation_ft,
            rwgps_url, rwgps_route_id, distance_km, cutoff_hours, start_time,
            avg_moving_speed, avg_elapsed_speed, total_moving_time_min,
            total_elapsed_time_min, total_break_time_min, overall_ft_per_mile)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (slug) DO UPDATE SET
            name = EXCLUDED.name,
            total_distance_miles = EXCLUDED.total_distance_miles,
            total_elevation_ft = EXCLUDED.total_elevation_ft,
            rwgps_url = EXCLUDED.rwgps_url,
            rwgps_route_id = EXCLUDED.rwgps_route_id,
            distance_km = EXCLUDED.distance_km,
            cutoff_hours = EXCLUDED.cutoff_hours,
            avg_moving_speed = EXCLUDED.avg_moving_speed,
            avg_elapsed_speed = EXCLUDED.avg_elapsed_speed,
            total_moving_time_min = EXCLUDED.total_moving_time_min,
            total_elapsed_time_min = EXCLUDED.total_elapsed_time_min,
            total_break_time_min = EXCLUDED.total_break_time_min,
            overall_ft_per_mile = EXCLUDED.overall_ft_per_mile
        RETURNING id
    """, (
        plan_data['name'], plan_data['slug'],
        plan_data.get('total_distance_miles'), plan_data.get('total_elevation_ft'),
        plan_data.get('rwgps_url'), plan_data.get('rwgps_route_id'),
        plan_data.get('distance_km'), plan_data.get('cutoff_hours'),
        plan_data.get('start_time', '07:00'),
        plan_data.get('avg_moving_speed'), plan_data.get('avg_elapsed_speed'),
        plan_data.get('total_moving_time_min'), plan_data.get('total_elapsed_time_min'),
        plan_data.get('total_break_time_min'), plan_data.get('overall_ft_per_mile'),
    ))

    plan_id = cur.fetchone()['id']

    # Delete old stops and re-insert
    cur.execute("DELETE FROM ride_plan_stop WHERE ride_plan_id = %s", (plan_id,))

    for stop in stops_data:
        cur.execute("""
            INSERT INTO ride_plan_stop (ride_plan_id, stop_order, location, stop_type,
                distance_miles, elevation_gain, segment_time_min, notes,
                seg_dist, ft_per_mi, avg_speed, cum_time_min,
                bookend_time_min, time_bank_min, difficulty_score)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            plan_id, stop['stop_order'], stop['location'], stop['stop_type'],
            stop.get('distance_miles'), stop.get('elevation_gain'),
            stop.get('segment_time_min'), stop.get('notes', ''),
            stop.get('seg_dist'), stop.get('ft_per_mi'),
            stop.get('avg_speed'), stop.get('cum_time_min'),
            stop.get('bookend_time_min'), stop.get('time_bank_min'),
            stop.get('difficulty_score'),
        ))

    conn.commit()

    # Clear relevant caches
    cache.delete_memoized(get_all_ride_plans)
    cache.delete_memoized(get_ride_plan_by_slug, plan_data['slug'])
    cache.delete_memoized(get_ride_plan_stops, plan_id)

    return plan_id


def create_ride(season_id, club_id, name, ride_type, ride_date, distance_km,
                elevation_ft=None, distance_miles=None, ft_per_mile=None, rwgps_url=None):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Auto-match ride plan by name (e.g. "Healdsburg" matches "SFR 300k Healdsburg" plan)
    matched_plan = find_ride_plan_for_ride(name)
    plan_id = matched_plan['id'] if matched_plan else None

    cur.execute("""INSERT INTO ride (season_id, club_id, name, ride_type, date, distance_km,
                  elevation_ft, distance_miles, ft_per_mile, rwgps_url, is_team_ride,
                  ride_plan_id)
                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
                  RETURNING id""",
               (season_id, club_id, name, ride_type, ride_date, distance_km,
                elevation_ft, distance_miles, ft_per_mile, rwgps_url, plan_id))
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
    cache.clear()


def auto_finalize_past_rides():
    """Mark all GOING riders as FINISHED for rides whose date has passed.

    Also sets event_status='COMPLETED' on those rides.

    Returns:
        list of dicts: [{'ride_id': int, 'ride_name': str, 'riders_finalized': int}]
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Find past rides that still have GOING riders
    cur.execute("""
        SELECT ri.id AS ride_id, ri.name AS ride_name, COUNT(rr.id) AS going_count
        FROM ride ri
        JOIN rider_ride rr ON rr.ride_id = ri.id
        WHERE ri.date < CURRENT_DATE
          AND rr.status = 'GOING'
        GROUP BY ri.id, ri.name
    """)
    rides_to_finalize = cur.fetchall()

    results = []
    for ride in rides_to_finalize:
        # Mark GOING riders as FINISHED
        cur.execute("""
            UPDATE rider_ride
            SET status = 'FINISHED'
            WHERE ride_id = %s AND status = 'GOING'
        """, (ride['ride_id'],))
        count = cur.rowcount

        # Mark ride as COMPLETED
        cur.execute("""
            UPDATE ride SET event_status = 'COMPLETED'
            WHERE id = %s AND event_status = 'UPCOMING'
        """, (ride['ride_id'],))

        results.append({
            'ride_id': ride['ride_id'],
            'ride_name': ride['ride_name'],
            'riders_finalized': count,
        })

    conn.commit()
    cache.clear()
    return results


def sync_rusa_finish_times():
    """Fetch official finish times from RUSA for FINISHED rides missing them.

    Groups by rider to minimize RUSA page fetches (one per rider).
    Matches RUSA results to rides using date ±5 days and distance ±20km.

    Returns:
        list of dicts with per-rider sync details
    """
    import time
    from services.rusa import fetch_rider_results

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Find all FINISHED rider_ride records missing finish_time
    cur.execute("""
        SELECT rr.id AS rr_id, rr.rider_id, rr.ride_id,
               r.rusa_id, r.first_name, r.last_name,
               ri.date AS ride_date, ri.distance_km
        FROM rider_ride rr
        JOIN rider r ON rr.rider_id = r.id
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE rr.status = 'FINISHED'
          AND (rr.finish_time IS NULL OR rr.finish_time = '')
          AND r.rusa_id IS NOT NULL
        ORDER BY r.id, ri.date
    """)
    rows = cur.fetchall()

    if not rows:
        return []

    # Group by rider
    riders = {}
    for row in rows:
        rid = row['rider_id']
        if rid not in riders:
            riders[rid] = {
                'rusa_id': row['rusa_id'],
                'name': f"{row['first_name']} {row['last_name']}",
                'rides': [],
            }
        riders[rid]['rides'].append(row)

    results = []
    total_updated = 0

    for i, (rider_id, info) in enumerate(riders.items()):
        rusa_results = fetch_rider_results(info['rusa_id'])
        matched = 0

        for ride_row in info['rides']:
            ride_date = ride_row['ride_date']
            if not ride_date:
                continue
            # Ensure ride_date is a date object
            if hasattr(ride_date, 'date'):
                ride_date = ride_date.date()

            distance_km = ride_row['distance_km'] or 0

            # Find matching RUSA result
            for rr in rusa_results:
                date_diff = abs((ride_date - rr['date']).days)
                dist_diff = abs(distance_km - rr['distance_km'])
                if date_diff <= 5 and (dist_diff <= 20 or (distance_km >= 1000 and rr['distance_km'] >= 1000)):
                    cur.execute(
                        "UPDATE rider_ride SET finish_time = %s WHERE id = %s",
                        (rr['finish_time'], ride_row['rr_id'])
                    )
                    matched += 1
                    break

        results.append({
            'rider_name': info['name'],
            'rusa_id': info['rusa_id'],
            'rides_checked': len(info['rides']),
            'results_found': matched,
        })
        total_updated += matched

        # Be respectful to RUSA servers
        if i < len(riders) - 1:
            time.sleep(1)

    if total_updated > 0:
        conn.commit()
        cache.clear()

    return results


def get_rides_with_signup_counts(season_id):
    """Get all rides for a season with signup/result counts for admin dashboard."""
    return _execute("""
        SELECT ri.*,
               c.code AS club_code,
               c.name AS club_name,
               COUNT(rr.id) FILTER (WHERE rr.status = 'GOING') AS going_count,
               COUNT(rr.id) FILTER (WHERE rr.status IN ('FINISHED','DNF','DNS','OTL')) AS result_count,
               COUNT(rr.id) FILTER (WHERE rr.status IS NOT NULL) AS total_signups,
               EXISTS (SELECT 1 FROM ride_wind_data rwd WHERE rwd.ride_id = ri.id) AS has_wind
        FROM ride ri
        JOIN club c ON ri.club_id = c.id
        LEFT JOIN rider_ride rr ON rr.ride_id = ri.id
        WHERE ri.season_id = %s
        GROUP BY ri.id, c.code, c.name
        ORDER BY ri.date
    """, (season_id,)).fetchall()


def update_ride_core(ride_id, fields):
    """Update core ride fields: name, date, distance_km, ride_type, club_id, elevation_ft, distance_miles, ft_per_mile."""
    allowed = {'name', 'date', 'distance_km', 'ride_type', 'club_id', 'elevation_ft', 'distance_miles', 'ft_per_mile'}
    conn = get_db()
    cur = conn.cursor()
    updates = []
    params = []
    for col in allowed:
        if col in fields:
            updates.append(f"{col} = %s")
            params.append(fields[col] if fields[col] != '' else None)
    if updates:
        params.append(ride_id)
        cur.execute(f"UPDATE ride SET {', '.join(updates)} WHERE id = %s", params)
        conn.commit()
        cache.clear()
        return True
    return False


def update_ride_details(ride_id, rwgps_url=None, ride_plan_id=None,
                       start_location=None, time_limit_hours=None):
    """Update ride details (route, location, time limit). Start time lives on ride_plan."""
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

def update_ride_plan_info(plan_id, name, rwgps_url, rwgps_url_team, start_time, distance_km, cutoff_hours):
    """Update ride plan top-level metadata."""
    _execute("""
        UPDATE ride_plan SET name=%s, rwgps_url=%s, rwgps_url_team=%s, start_time=%s,
            distance_km=%s, cutoff_hours=%s
        WHERE id=%s
    """, (name or None, rwgps_url or None, rwgps_url_team or None, start_time or '06:00',
          int(distance_km) if distance_km else None,
          float(cutoff_hours) if cutoff_hours else None,
          plan_id))
    cache.delete_memoized(get_all_ride_plans)
    cache.delete_memoized(get_ride_plan_by_slug)

@cache.memoize(CACHE_TIMEOUT)
def get_all_ride_plans():
    return _execute("""
        SELECT * FROM ride_plan ORDER BY name
    """).fetchall()

@cache.memoize(CACHE_TIMEOUT)
def get_ride_plan_by_slug(slug):
    return _execute("""
        SELECT * FROM ride_plan WHERE slug = %s
    """, (slug,)).fetchone()

@cache.memoize(CACHE_TIMEOUT)
def get_ride_plan_stops(ride_plan_id):
    return _execute("""
        SELECT * FROM ride_plan_stop
        WHERE ride_plan_id = %s
        ORDER BY stop_order
    """, (ride_plan_id,)).fetchall()

@cache.memoize(CACHE_TIMEOUT)
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


def update_strava_privacy(rider_id, is_private):
    """Update Strava data privacy setting for a rider."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        INSERT INTO rider_profile (rider_id, strava_data_private)
        VALUES (%s, %s)
        ON CONFLICT(rider_id) DO UPDATE SET strava_data_private = EXCLUDED.strava_data_private
    """, (rider_id, is_private))
    conn.commit()


# ========== USER AUTHENTICATION ==========

def get_user_by_email(email):
    """Get user by email. NOT CACHED - user data should not be cached in serverless environments."""
    return _execute("SELECT * FROM app_user WHERE email = %s", (email,)).fetchone()

def get_user_by_google_id(google_id):
    """Get user by Google ID. NOT CACHED - user data should not be cached in serverless environments."""
    return _execute("SELECT * FROM app_user WHERE google_id = %s", (google_id,)).fetchone()

def get_user_by_id(user_id):
    """Get user by ID. NOT CACHED - user data should not be cached in serverless environments."""
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

@cache.memoize(CACHE_TIMEOUT)
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

@cache.memoize(CACHE_TIMEOUT)
def check_rusa_id_exists(rusa_id):
    """Check if a RUSA ID is already registered."""
    return _execute("SELECT id FROM rider WHERE rusa_id = %s", (rusa_id,)).fetchone()

@cache.memoize(CACHE_TIMEOUT)
def is_rider_linked_to_user(rider_id):
    """Check if a rider is already linked to a user account."""
    return _execute("SELECT id FROM app_user WHERE rider_id = %s", (rider_id,)).fetchone()

def get_rider_by_rusa_id(rusa_id):
    """Get rider by RUSA ID. NOT CACHED - rider data should not be cached in serverless environments."""
    return _execute("SELECT * FROM rider WHERE rusa_id = %s", (rusa_id,)).fetchone()


# ========== STRAVA ==========

@cache.memoize(CACHE_TIMEOUT)
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

def get_all_active_strava_connections():
    """Get all riders with active Strava connections.

    Returns riders ordered by last_sync (oldest first) to prioritize
    those who haven't synced in longest time.

    Returns:
        list of dicts with rider_id, rider_name, access_token, refresh_token, expires_at, last_sync_at
    """
    return _execute("""
        SELECT sc.rider_id, r.first_name || ' ' || r.last_name AS rider_name,
               sc.access_token, sc.refresh_token, sc.expires_at,
               sc.last_sync_at AS last_sync
        FROM strava_connection sc
        JOIN rider r ON r.id = sc.rider_id
        WHERE sc.access_token IS NOT NULL
        ORDER BY sc.last_sync_at ASC NULLS FIRST
        LIMIT 100
    """).fetchall()


def get_strava_admin_summary():
    """Get Strava sync summary for all connected riders (admin page)."""
    return _execute("""
        SELECT r.id AS rider_id, r.first_name, r.last_name, r.rusa_id,
               sc.eddington_number_miles, sc.eddington_number_km,
               sc.eddington_calculated_at, sc.backfill_cursor, sc.last_sync_at,
               COUNT(sa.id) AS activity_count,
               MIN(sa.start_date)::date AS oldest_activity,
               MAX(sa.start_date)::date AS newest_activity
        FROM strava_connection sc
        JOIN rider r ON r.id = sc.rider_id
        LEFT JOIN strava_activity sa ON sa.rider_id = sc.rider_id
        GROUP BY r.id, r.first_name, r.last_name, r.rusa_id,
                 sc.eddington_number_miles, sc.eddington_number_km,
                 sc.eddington_calculated_at, sc.backfill_cursor, sc.last_sync_at
        ORDER BY r.first_name, r.last_name
    """).fetchall()


def get_oldest_activity_date(rider_id):
    """Get the earliest activity start_date for a rider.

    Returns:
        str or None: ISO date string of oldest activity, or None if no activities
    """
    row = _execute("""
        SELECT MIN(start_date) AS oldest
        FROM strava_activity
        WHERE rider_id = %s
    """, (rider_id,)).fetchone()
    return row['oldest'] if row else None


def get_backfill_cursor(rider_id):
    """Get the backfill cursor date for a rider.

    The cursor tracks how far back we've searched for Strava activities,
    independent of whether activities were found (handles gaps in history).

    Returns:
        date or None: How far back we've searched, or None if never backfilled
    """
    row = _execute("""
        SELECT backfill_cursor FROM strava_connection WHERE rider_id = %s
    """, (rider_id,)).fetchone()
    return row['backfill_cursor'] if row else None


def update_backfill_cursor(rider_id, cursor_date):
    """Update the backfill cursor to track how far back we've searched."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE strava_connection SET backfill_cursor = %s WHERE rider_id = %s
    """, (cursor_date, rider_id))
    conn.commit()


def upsert_strava_activity(row):
    """Insert or update a Strava activity.

    Returns:
        bool: True if this was a new insert, False if it updated an existing row
    """
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
        RETURNING (xmax = 0) AS is_new
    """, row)
    result = cur.fetchone()
    conn.commit()
    return result['is_new'] if result else False

@cache.memoize(CACHE_TIMEOUT)
def get_strava_activities(rider_id, days=28):
    """Get recent Strava activities for a rider."""
    return _execute("""
        SELECT * FROM strava_activity
        WHERE rider_id = %s AND start_date_local >= NOW() - INTERVAL '%s days'
        ORDER BY start_date_local DESC
    """, (rider_id, days)).fetchall()

@cache.memoize(CACHE_TIMEOUT)
def get_strava_activities_for_calendar(rider_id, days=28):
    """Get activities with date column for calendar display."""
    return _execute("""
        SELECT *, DATE(start_date_local) as activity_date
        FROM strava_activity
        WHERE rider_id = %s AND start_date_local >= NOW() - INTERVAL '%s days'
        ORDER BY start_date_local ASC
    """, (rider_id, days)).fetchall()


def update_eddington_number(rider_id, eddington_miles, eddington_km):
    """Update Eddington numbers for a rider."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        UPDATE strava_connection
        SET eddington_number_miles = %s,
            eddington_number_km = %s,
            eddington_calculated_at = CURRENT_TIMESTAMP
        WHERE rider_id = %s
    """, (eddington_miles, eddington_km, rider_id))
    conn.commit()
    cache.clear()

@cache.memoize(CACHE_TIMEOUT)
def get_all_strava_activities_for_eddington(rider_id):
    """Get ALL Strava cycling activities for Eddington calculation (no time limit).

    Includes all cycling-related activity types: Ride, VirtualRide,
    MountainBikeRide, GravelRide, EBikeRide, Handcycle, Velomobile.
    """
    return _execute("""
        SELECT distance, start_date, start_date_local, activity_type, elapsed_time
        FROM strava_activity
        WHERE rider_id = %s
          AND activity_type IN (
              'Ride', 'VirtualRide', 'MountainBikeRide', 'GravelRide',
              'EBikeRide', 'Handcycle', 'Velomobile'
          )
        ORDER BY start_date_local DESC
    """, (rider_id,)).fetchall()

@cache.memoize(CACHE_TIMEOUT)
def get_all_strava_activities_unfiltered(rider_id):
    """Get ALL Strava activities regardless of type (for 'All' Eddington comparison)."""
    return _execute("""
        SELECT distance, start_date, start_date_local, activity_type, elapsed_time
        FROM strava_activity
        WHERE rider_id = %s
        ORDER BY start_date_local DESC
    """, (rider_id,)).fetchall()


@cache.memoize(CACHE_TIMEOUT)
def get_rider_upcoming_signups(rider_id):
    """Get upcoming rides a rider has signed up for or expressed interest in.

    Returns list of dicts with ride details + signup status, ordered by date.
    """
    today = date.today()
    return _execute("""
        SELECT ri.id, ri.name, ri.date, ri.distance_km, ri.distance_miles,
               ri.elevation_ft, ri.ft_per_mile, ri.time_limit_hours, ri.ride_type,
               ri.rwgps_url, ri.event_status, ri.start_location,
               c.code as club_code, c.name as club_name,
               rp.slug as plan_slug, rp.name as plan_name,
               rp.start_time as start_time,
               rp.rwgps_url as plan_rwgps_url, rp.rwgps_url_team as plan_rwgps_url_team,
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


# ========== CUSTOM RIDE PLANS ==========

@cache.memoize(CACHE_TIMEOUT)
def get_custom_plan(rider_id, base_plan_id):
    """Get a rider's custom plan for a specific base plan."""
    return _execute("""
        SELECT * FROM custom_ride_plan
        WHERE rider_id = %s AND base_plan_id = %s
    """, (rider_id, base_plan_id)).fetchone()

@cache.memoize(CACHE_TIMEOUT)
def get_custom_plan_by_id(custom_plan_id):
    """Get a custom plan by ID."""
    return _execute("""
        SELECT * FROM custom_ride_plan WHERE id = %s
    """, (custom_plan_id,)).fetchone()

@cache.memoize(CACHE_TIMEOUT)
def get_custom_plan_with_rider_info(custom_plan_id):
    """Get a custom plan with rider information for display."""
    return _execute("""
        SELECT cp.*, r.first_name, r.last_name, r.rusa_id,
               rp.name as base_plan_name, rp.slug as base_plan_slug
        FROM custom_ride_plan cp
        JOIN rider r ON cp.rider_id = r.id
        JOIN ride_plan rp ON cp.base_plan_id = rp.id
        WHERE cp.id = %s
    """, (custom_plan_id,)).fetchone()

def create_custom_plan(rider_id, base_plan_id, name, description=None, avg_moving_speed=None):
    """Create a new custom plan. Returns the new plan ID."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO custom_ride_plan (rider_id, base_plan_id, name, description, avg_moving_speed)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (rider_id, base_plan_id, name, description, avg_moving_speed))
        result = cur.fetchone()
        conn.commit()
        cache.delete_memoized(get_custom_plan, rider_id, base_plan_id)
        cache.delete_memoized(get_public_custom_plans, base_plan_id)
        
        # Clear the ride plan detail page cache
        cur.execute("SELECT slug FROM ride_plan WHERE id = %s", (base_plan_id,))
        plan = cur.fetchone()
        if plan:
            cache_key = f"flask_cache_view//ride-plan/{plan['slug']}"
            cache.delete(cache_key)
        
        return result['id'] if result else None
    except Exception as e:
        conn.rollback()
        raise e

@cache.memoize(CACHE_TIMEOUT)
def get_custom_plan_stops_raw(custom_plan_id):
    """Get raw custom plan stop overrides (not merged with base)."""
    return _execute("""
        SELECT * FROM custom_ride_plan_stop
        WHERE custom_plan_id = %s
        ORDER BY stop_order
    """, (custom_plan_id,)).fetchall()

def _clear_custom_plan_cache(custom_plan_id):
    """Force clear all caches related to a custom plan."""
    print(f"[DEBUG] Clearing all caches for custom_plan_id={custom_plan_id}")
    
    # Clear memoized function caches
    cache.delete_memoized(get_custom_plan_stops_raw, custom_plan_id)
    cache.delete_memoized(get_custom_plan_by_id, custom_plan_id)
    
    # Clear direct cache keys
    cache.cache.delete(f'get_custom_plan_stops_raw_{custom_plan_id}')
    cache.cache.delete(f'get_custom_plan_by_id_{custom_plan_id}')
    
    # Clear any pattern-based cache keys
    try:
        # Flask-Caching doesn't have a built-in pattern delete, so we'll do specific keys
        for key in [f'custom_plan_{custom_plan_id}', f'merged_plan_stops_{custom_plan_id}']:
            cache.cache.delete(key)
    except Exception as e:
        print(f"[DEBUG] Error clearing additional cache keys: {e}")
    
    print(f"[DEBUG] Cache cleared successfully")

def update_custom_plan_stop(custom_plan_id, stop_id, segment_time_min=None, stop_duration_min=None, stop_name=None, location=None, notes=None, distance_miles=None, elevation_gain=None, explicit_fields=None):
    """Update timing, distance, elevation, stop_name, location, or notes for a custom plan stop.
    
    stop_id can be either:
    - A custom_ride_plan_stop.id (for existing overrides or custom stops)
    - A ride_plan_stop.id (base stop) - in which case we create an override
    
    explicit_fields: Set of field names that were explicitly provided (to distinguish None from missing)
    """
    if explicit_fields is None:
        explicit_fields = set()
    
    print(f"[DEBUG] update_custom_plan_stop called:")
    print(f"  custom_plan_id={custom_plan_id}, stop_id={stop_id}")
    print(f"  stop_name={stop_name}, stop_duration_min={stop_duration_min}")
    print(f"  explicit_fields={explicit_fields}")
    
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    # First, check if this is an existing custom stop record
    cur.execute("""
        SELECT id, base_stop_id, is_custom_stop 
        FROM custom_ride_plan_stop
        WHERE id = %s AND custom_plan_id = %s
    """, (stop_id, custom_plan_id))
    existing_custom_stop = cur.fetchone()
    print(f"[DEBUG] existing_custom_stop query result: {existing_custom_stop}")
    
    if existing_custom_stop:
        # This is an existing custom stop override - update it directly
        updates = []
        params = []
        
        if 'segment_time_min' in explicit_fields:
            updates.append("segment_time_min = %s")
            params.append(segment_time_min)
        
        if 'stop_duration_min' in explicit_fields:
            updates.append("stop_duration_min = %s")
            params.append(stop_duration_min)
        
        if 'stop_name' in explicit_fields:
            updates.append("stop_name = %s")
            params.append(stop_name)
        
        if 'location' in explicit_fields:
            updates.append("location = %s")
            params.append(location)
        
        if 'distance_miles' in explicit_fields:
            updates.append("distance_miles = %s")
            params.append(distance_miles)
        
        if 'elevation_gain' in explicit_fields:
            updates.append("elevation_gain = %s")
            params.append(elevation_gain)
        
        if 'notes' in explicit_fields:
            updates.append("notes = %s")
            params.append(notes)
        
        if not updates:
            return False
        
        params.extend([stop_id, custom_plan_id])
        sql = f"UPDATE custom_ride_plan_stop SET {', '.join(updates)} WHERE id = %s AND custom_plan_id = %s"
        
        print(f"[DEBUG] Executing UPDATE on existing custom stop: {sql}")
        print(f"[DEBUG] Params: {params}")
        cur.execute(sql, params)
        conn.commit()
        
        # Clear all caches
        _clear_custom_plan_cache(custom_plan_id)
        print(f"[DEBUG] Updated {cur.rowcount} row(s)")
        return cur.rowcount > 0
    else:
        # This might be a base_stop_id - check if an override exists for this base stop
        cur.execute("""
            SELECT id 
            FROM custom_ride_plan_stop
            WHERE custom_plan_id = %s AND base_stop_id = %s
        """, (custom_plan_id, stop_id))
        override = cur.fetchone()
        print(f"[DEBUG] Checking for override by base_stop_id={stop_id}: {override}")
        
        if override:
            # Override exists, update it
            updates = []
            params = []
            
            if 'segment_time_min' in explicit_fields:
                updates.append("segment_time_min = %s")
                params.append(segment_time_min)
            
            if 'stop_duration_min' in explicit_fields:
                updates.append("stop_duration_min = %s")
                params.append(stop_duration_min)
            
            if 'stop_name' in explicit_fields:
                updates.append("stop_name = %s")
                params.append(stop_name)
            
            if 'location' in explicit_fields:
                updates.append("location = %s")
                params.append(location)
            
            if 'distance_miles' in explicit_fields:
                updates.append("distance_miles = %s")
                params.append(distance_miles)
            
            if 'elevation_gain' in explicit_fields:
                updates.append("elevation_gain = %s")
                params.append(elevation_gain)
            
            if 'notes' in explicit_fields:
                updates.append("notes = %s")
                params.append(notes)
            
            if not updates:
                return False
            
            params.append(override['id'])
            sql = f"UPDATE custom_ride_plan_stop SET {', '.join(updates)} WHERE id = %s"
            
            print(f"[DEBUG] Executing UPDATE on override: {sql}")
            print(f"[DEBUG] Params: {params}")
            cur.execute(sql, params)
            conn.commit()
            
            # Clear all caches
            _clear_custom_plan_cache(custom_plan_id)
            print(f"[DEBUG] Updated {cur.rowcount} row(s) via override")
            return cur.rowcount > 0
        else:
            # No override exists - create one for this base stop
            print(f"[DEBUG] No override found, fetching base stop with id={stop_id}")
            cur.execute("""
                SELECT id, stop_order, stop_name, location, stop_type, distance_miles, elevation_gain
                FROM ride_plan_stop
                WHERE id = %s
            """, (stop_id,))
            base_stop = cur.fetchone()
            
            if not base_stop:
                print(f"[DEBUG] Base stop not found!")
                return False
            
            print(f"[DEBUG] Base stop found: {base_stop}")
            
            # Create new override - ONLY store explicitly provided fields (delta model)
            # Required fields from base, optional fields only if explicitly changed
            columns = ['custom_plan_id', 'base_stop_id', 'stop_order', 'location', 'stop_type', 'distance_miles', 'elevation_gain', 'is_custom_stop']
            values = [
                custom_plan_id,
                base_stop['id'],
                base_stop['stop_order'],
                location if 'location' in explicit_fields else base_stop['location'],
                base_stop['stop_type'],
                distance_miles if 'distance_miles' in explicit_fields else base_stop['distance_miles'],
                elevation_gain if 'elevation_gain' in explicit_fields else base_stop['elevation_gain'],
                False
            ]
            
            # Only add optional columns if explicitly provided in the request
            if 'segment_time_min' in explicit_fields:
                columns.append('segment_time_min')
                values.append(segment_time_min)
            
            if 'stop_duration_min' in explicit_fields:
                columns.append('stop_duration_min')
                values.append(stop_duration_min)
            
            if 'stop_name' in explicit_fields:
                columns.append('stop_name')
                values.append(stop_name)
            
            if 'notes' in explicit_fields:
                columns.append('notes')
                values.append(notes)
            
            placeholders = ', '.join(['%s'] * len(values))
            sql = f"INSERT INTO custom_ride_plan_stop ({', '.join(columns)}) VALUES ({placeholders})"
            
            print(f"[DEBUG] Inserting new override with SQL: {sql}")
            print(f"[DEBUG] Values: {values}")
            
            cur.execute(sql, values)
            conn.commit()
            
            # Clear all caches
            _clear_custom_plan_cache(custom_plan_id)
            print(f"[DEBUG] Created new override successfully")
            return True

def add_custom_stop(custom_plan_id, location, stop_type, distance_miles, elevation_gain, after_stop_order, segment_time_min=None, notes=None):
    """Add a custom stop at a specific position by distance."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    try:
        # Get the base plan id
        cur.execute("SELECT base_plan_id, rider_id FROM custom_ride_plan WHERE id = %s", (custom_plan_id,))
        plan = cur.fetchone()
        if not plan:
            raise Exception("Custom plan not found")
        
        # Find max stop_order to append at end
        cur.execute("""
            SELECT COALESCE(MAX(stop_order), 0) as max_order
            FROM (
                SELECT stop_order FROM ride_plan_stop WHERE ride_plan_id = %s
                UNION
                SELECT stop_order FROM custom_ride_plan_stop WHERE custom_plan_id = %s
            ) combined
        """, (plan['base_plan_id'], custom_plan_id))
        result = cur.fetchone()
        new_order = result['max_order'] + 1
        
        # Insert new custom stop with high stop_order
        # It will be sorted by distance_miles for display
        cur.execute("""
            INSERT INTO custom_ride_plan_stop 
            (custom_plan_id, stop_order, location, stop_type, distance_miles, 
             elevation_gain, segment_time_min, notes, is_custom_stop)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
            RETURNING id
        """, (custom_plan_id, new_order, location, stop_type, distance_miles, 
              elevation_gain, segment_time_min, notes))
        
        new_stop_id = cur.fetchone()['id']
        
        # Adjust timing for the next stop if segment_time_min was provided
        if segment_time_min and segment_time_min > 0:
            # Find the next stop by distance
            cur.execute("""
                SELECT rps.id as base_stop_id, rps.distance_miles, rps.segment_time_min,
                       crps.id as override_id, crps.segment_time_min as custom_time
                FROM ride_plan_stop rps
                LEFT JOIN custom_ride_plan_stop crps 
                    ON crps.custom_plan_id = %s AND crps.base_stop_id = rps.id
                WHERE rps.ride_plan_id = %s 
                AND rps.distance_miles > %s
                AND NOT EXISTS (
                    SELECT 1 FROM custom_ride_plan_stop 
                    WHERE custom_plan_id = %s AND base_stop_id = rps.id AND is_hidden = TRUE
                )
                ORDER BY rps.distance_miles ASC
                LIMIT 1
            """, (custom_plan_id, plan['base_plan_id'], distance_miles, custom_plan_id))
            
            next_stop = cur.fetchone()
            if next_stop:
                original_time = int(next_stop['custom_time'] or next_stop['segment_time_min'] or 0)
                if original_time > 0:
                    # Calculate adjusted time: original_time - new_stop_time
                    adjusted_time = max(1, original_time - segment_time_min)
                    
                    # Create or update override for the next stop
                    if next_stop['override_id']:
                        # Update existing override
                        cur.execute("""
                            UPDATE custom_ride_plan_stop
                            SET segment_time_min = %s
                            WHERE id = %s
                        """, (adjusted_time, next_stop['override_id']))
                    else:
                        # Create new override
                        cur.execute("""
                            INSERT INTO custom_ride_plan_stop
                            (custom_plan_id, base_stop_id, stop_order, location, stop_type,
                             distance_miles, elevation_gain, segment_time_min, is_custom_stop)
                            SELECT %s, id, stop_order, location, stop_type,
                                   distance_miles, elevation_gain, %s, FALSE
                            FROM ride_plan_stop
                            WHERE id = %s
                        """, (custom_plan_id, adjusted_time, next_stop['base_stop_id']))
        
        conn.commit()
        
        # Clear all caches
        _clear_custom_plan_cache(custom_plan_id)
        cache.delete_memoized(get_custom_plan, plan['rider_id'], plan['base_plan_id'])
        
        return new_stop_id
    except Exception as e:
        conn.rollback()
        raise e

def hide_base_stop(custom_plan_id, base_stop_id):
    """Mark a base stop as hidden in the custom plan."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    try:
        # Check if override already exists
        cur.execute("""
            SELECT id FROM custom_ride_plan_stop
            WHERE custom_plan_id = %s AND base_stop_id = %s
        """, (custom_plan_id, base_stop_id))
        existing = cur.fetchone()
        
        if existing:
            # Update existing override
            cur.execute("""
                UPDATE custom_ride_plan_stop
                SET is_hidden = TRUE
                WHERE id = %s
            """, (existing['id'],))
        else:
            # Get base stop info to create override
            cur.execute("SELECT * FROM ride_plan_stop WHERE id = %s", (base_stop_id,))
            base_stop = cur.fetchone()
            
            if not base_stop:
                conn.rollback()
                return False
            
            # Create new override with hidden flag
            cur.execute("""
                INSERT INTO custom_ride_plan_stop
                (custom_plan_id, base_stop_id, stop_order, location, stop_type,
                 distance_miles, elevation_gain, is_hidden)
                VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
            """, (custom_plan_id, base_stop['id'], base_stop['stop_order'],
                  base_stop['location'], base_stop['stop_type'],
                  base_stop['distance_miles'], base_stop['elevation_gain']))
        
        conn.commit()
        _clear_custom_plan_cache(custom_plan_id)
        return True
    except Exception as e:
        conn.rollback()
        raise e

def unhide_base_stop(custom_plan_id, base_stop_id):
    """Unhide a previously hidden base stop."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    cur.execute("""
        UPDATE custom_ride_plan_stop
        SET is_hidden = FALSE
        WHERE custom_plan_id = %s AND base_stop_id = %s
    """, (custom_plan_id, base_stop_id))
    
    conn.commit()
    _clear_custom_plan_cache(custom_plan_id)
    return cur.rowcount > 0

def update_custom_plan_settings(custom_plan_id, rider_id, name=None, description=None, 
                                 is_public=None, avg_moving_speed=None):
    """Update custom plan settings."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    updates = []
    params = []
    
    if name is not None:
        updates.append("name = %s")
        params.append(name)
    
    if description is not None:
        updates.append("description = %s")
        params.append(description)
    
    if is_public is not None:
        updates.append("is_public = %s")
        params.append(is_public)
    
    if avg_moving_speed is not None:
        updates.append("avg_moving_speed = %s")
        params.append(avg_moving_speed)
    
    if not updates:
        return False
    
    params.extend([custom_plan_id, rider_id])
    sql = f"UPDATE custom_ride_plan SET {', '.join(updates)} WHERE id = %s AND rider_id = %s"
    
    cur.execute(sql, params)
    conn.commit()
    
    # Get the plan to invalidate correct cache
    cur.execute("""
        SELECT cp.rider_id, cp.base_plan_id, rp.slug 
        FROM custom_ride_plan cp
        JOIN ride_plan rp ON cp.base_plan_id = rp.id
        WHERE cp.id = %s
    """, (custom_plan_id,))
    plan = cur.fetchone()
    if plan:
        cache.delete_memoized(get_custom_plan, plan['rider_id'], plan['base_plan_id'])
        cache.delete_memoized(get_custom_plan_by_id, custom_plan_id)
        if is_public is not None:
            cache.delete_memoized(get_public_custom_plans, plan['base_plan_id'])
        
        # Clear the ride plan detail page cache
        cache_key = f"flask_cache_view//ride-plan/{plan['slug']}"
        cache.delete(cache_key)
    
    return cur.rowcount > 0

def delete_custom_plan(custom_plan_id, rider_id):
    """Delete a custom plan (only owner can delete)."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    # Get plan info before deletion for cache invalidation
    cur.execute("""
        SELECT cp.rider_id, cp.base_plan_id, rp.slug 
        FROM custom_ride_plan cp
        JOIN ride_plan rp ON cp.base_plan_id = rp.id
        WHERE cp.id = %s AND cp.rider_id = %s
    """, (custom_plan_id, rider_id))
    plan = cur.fetchone()
    
    if not plan:
        return False
    
    cur.execute("""
        DELETE FROM custom_ride_plan
        WHERE id = %s AND rider_id = %s
    """, (custom_plan_id, rider_id))
    
    conn.commit()
    
    # Clear all caches
    _clear_custom_plan_cache(custom_plan_id)
    cache.delete_memoized(get_custom_plan, plan['rider_id'], plan['base_plan_id'])
    cache.delete_memoized(get_public_custom_plans, plan['base_plan_id'])
    
    # Clear the ride plan detail page cache for this specific plan
    from flask import request
    cache_key = f"flask_cache_view//ride-plan/{plan['slug']}"
    cache.delete(cache_key)
    
    return cur.rowcount > 0

@cache.memoize(CACHE_TIMEOUT)
def get_public_custom_plans(base_plan_id):
    """Get all public custom plans for a base plan."""
    return _execute("""
        SELECT cp.*, r.first_name, r.last_name, r.rusa_id
        FROM custom_ride_plan cp
        JOIN rider r ON cp.rider_id = r.id
        WHERE cp.base_plan_id = %s AND cp.is_public = TRUE
        ORDER BY cp.updated_at DESC
    """, (base_plan_id,)).fetchall()

def delete_custom_stop(custom_stop_id, rider_id):
    """Delete a custom stop (only works for custom-added stops, not base stops)."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    # Verify the stop belongs to a plan owned by this rider
    cur.execute("""
        SELECT cs.custom_plan_id, cs.is_custom_stop, cp.rider_id, cp.base_plan_id
        FROM custom_ride_plan_stop cs
        JOIN custom_ride_plan cp ON cs.custom_plan_id = cp.id
        WHERE cs.id = %s
    """, (custom_stop_id,))
    result = cur.fetchone()
    
    if not result or result['rider_id'] != rider_id:
        return False
    
    if not result['is_custom_stop']:
        # Cannot delete base stops, only hide them
        return False
    
    # Delete the custom stop
    cur.execute("DELETE FROM custom_ride_plan_stop WHERE id = %s", (custom_stop_id,))
    conn.commit()
    
    # Clear all caches
    _clear_custom_plan_cache(result['custom_plan_id'])
    cache.delete_memoized(get_custom_plan, result['rider_id'], result['base_plan_id'])
    
    return True

def clone_custom_plan(source_plan_id, target_rider_id, new_name=None):
    """Clone a public custom plan to a new rider."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    try:
        # Get source plan
        cur.execute("SELECT * FROM custom_ride_plan WHERE id = %s AND is_public = TRUE", (source_plan_id,))
        source_plan = cur.fetchone()
        
        if not source_plan:
            return None
        
        # Create new plan
        plan_name = new_name or f"{source_plan['name']} (Copy)"
        cur.execute("""
            INSERT INTO custom_ride_plan 
            (rider_id, base_plan_id, name, description, avg_moving_speed, is_public)
            VALUES (%s, %s, %s, %s, %s, FALSE)
            RETURNING id
        """, (target_rider_id, source_plan['base_plan_id'], plan_name,
              source_plan['description'], source_plan['avg_moving_speed']))
        
        new_plan = cur.fetchone()
        new_plan_id = new_plan['id']
        
        # Copy stops
        cur.execute("""
            INSERT INTO custom_ride_plan_stop
            (custom_plan_id, base_stop_id, stop_order, location, stop_type,
             distance_miles, elevation_gain, segment_time_min, notes, 
             is_custom_stop, is_hidden)
            SELECT %s, base_stop_id, stop_order, location, stop_type,
                   distance_miles, elevation_gain, segment_time_min, notes,
                   is_custom_stop, is_hidden
            FROM custom_ride_plan_stop
            WHERE custom_plan_id = %s
        """, (new_plan_id, source_plan_id))
        
        conn.commit()
        cache.delete_memoized(get_custom_plan, target_rider_id, source_plan['base_plan_id'])
        return new_plan_id
    except Exception as e:
        conn.rollback()
        raise e


# ========== STRAVA RIDE ANALYSIS ==========

def get_strava_ride_match(rider_id, ride_id):
    """Get existing Strava match for a rider's ride."""
    return _execute("""
        SELECT srm.*, sa.strava_url, sa.name as activity_name,
               sa.distance, sa.moving_time, sa.elapsed_time,
               sa.total_elevation_gain, sa.average_speed,
               sa.average_heartrate, sa.max_heartrate, sa.has_heartrate,
               sa.average_watts, sa.max_watts, sa.weighted_average_watts,
               sa.kilojoules, sa.device_watts, sa.suffer_score,
               sa.start_date_local
        FROM strava_ride_match srm
        JOIN strava_activity sa ON sa.strava_activity_id = srm.strava_activity_id
                                AND sa.rider_id = srm.rider_id
        WHERE srm.rider_id = %s AND srm.ride_id = %s
    """, (rider_id, ride_id)).fetchone()


def create_strava_ride_match(rider_id, ride_id, strava_activity_id, confidence='auto'):
    """Create a ride-to-activity match."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO strava_ride_match (rider_id, ride_id, strava_activity_id, match_confidence)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (rider_id, ride_id) DO UPDATE SET
            strava_activity_id = EXCLUDED.strava_activity_id,
            match_confidence = EXCLUDED.match_confidence,
            matched_at = CURRENT_TIMESTAMP
        RETURNING id
    """, (rider_id, ride_id, strava_activity_id, confidence))
    result = cur.fetchone()
    conn.commit()
    return result['id'] if result else None


def get_all_strava_ride_matches(rider_id, ride_ids):
    """Batch get matches for multiple rides. Returns {ride_id: {strava_activity_id, strava_url}}."""
    if not ride_ids:
        return {}
    placeholders = ','.join(['%s'] * len(ride_ids))
    rows = _execute(f"""
        SELECT srm.ride_id, srm.strava_activity_id, sa.strava_url
        FROM strava_ride_match srm
        JOIN strava_activity sa ON sa.strava_activity_id = srm.strava_activity_id
                                AND sa.rider_id = srm.rider_id
        WHERE srm.rider_id = %s AND srm.ride_id IN ({placeholders})
    """, (rider_id, *ride_ids)).fetchall()
    return {r['ride_id']: dict(r) for r in rows}


def get_strava_ride_analysis(match_id):
    """Get cached analysis for a match."""
    return _execute("""
        SELECT * FROM strava_ride_analysis WHERE match_id = %s
    """, (match_id,)).fetchone()


def upsert_strava_ride_analysis(match_id, detected_stops, stream_summary,
                                error=None, compressed_streams=None):
    """Insert or update analysis results.

    compressed_streams: optional zlib-compressed bytes of the full Strava
    streams dict.  When provided, stored as BYTEA; when None the existing
    cached streams are preserved (COALESCE).
    """
    conn = get_db()
    cur = conn.cursor()
    import json
    streams_param = psycopg2.Binary(compressed_streams) if compressed_streams else None
    cur.execute("""
        INSERT INTO strava_ride_analysis
            (match_id, detected_stops, stream_summary, strava_api_error,
             activity_streams, streams_fetched_at)
        VALUES (%s, %s, %s, %s, %s, CASE WHEN %s IS NOT NULL THEN CURRENT_TIMESTAMP END)
        ON CONFLICT (match_id) DO UPDATE SET
            detected_stops = EXCLUDED.detected_stops,
            stream_summary = EXCLUDED.stream_summary,
            strava_api_error = EXCLUDED.strava_api_error,
            analyzed_at = CURRENT_TIMESTAMP,
            activity_streams = COALESCE(EXCLUDED.activity_streams,
                                        strava_ride_analysis.activity_streams),
            streams_fetched_at = COALESCE(EXCLUDED.streams_fetched_at,
                                          strava_ride_analysis.streams_fetched_at)
    """, (match_id, json.dumps(detected_stops), json.dumps(stream_summary),
          error, streams_param, streams_param))
    conn.commit()


def clear_strava_ride_analysis(match_id):
    """Clear cached analysis (for retry)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM strava_ride_analysis WHERE match_id = %s", (match_id,))
    conn.commit()


def get_rider_rides_with_cached_streams(rider_id):
    """Get all finished rides for a rider that have cached Strava stream data.

    Returns ride metadata plus the compressed activity_streams blob for each ride.
    Only includes rides where stream analysis completed successfully.
    """
    return _execute("""
        SELECT r.id AS ride_id, r.name AS ride_name, r.date, r.distance_km,
               r.elevation_ft,
               s.name AS season_name,
               srm.id AS match_id,
               sa.elapsed_time, sa.moving_time, sa.distance AS strava_distance_m,
               sa.total_elevation_gain, sa.average_speed,
               sa.average_heartrate, sa.max_heartrate, sa.has_heartrate,
               sa.average_watts, sa.weighted_average_watts, sa.device_watts,
               sa.suffer_score, sa.strava_url,
               sra.activity_streams
        FROM rider_ride rr
        JOIN ride r ON r.id = rr.ride_id
        JOIN season s ON s.id = r.season_id
        JOIN strava_ride_match srm ON srm.rider_id = rr.rider_id AND srm.ride_id = rr.ride_id
        JOIN strava_activity sa ON sa.strava_activity_id = srm.strava_activity_id
                                AND sa.rider_id = srm.rider_id
        JOIN strava_ride_analysis sra ON sra.match_id = srm.id
        WHERE rr.rider_id = %s
          AND rr.status = %s
          AND sra.activity_streams IS NOT NULL
          AND sra.strava_api_error IS NULL
        ORDER BY r.date DESC
    """, (rider_id, RideStatus.FINISHED.value)).fetchall()


def get_cohort_cached_streams(ride_id):
    """Get cached Strava streams for all public finishers of a ride.

    Returns list of dicts with rider info and compressed activity_streams blob.
    Only includes riders with cached streams and non-private Strava data.
    """
    return _execute("""
        SELECT
            r.id AS rider_id,
            r.first_name,
            r.last_name,
            sa.elapsed_time, sa.moving_time, sa.average_speed,
            sra.activity_streams
        FROM rider_ride rr
        JOIN rider r ON r.id = rr.rider_id
        LEFT JOIN rider_profile rp ON rp.rider_id = r.id
        JOIN strava_ride_match srm ON srm.rider_id = r.id AND srm.ride_id = rr.ride_id
        JOIN strava_activity sa ON sa.strava_activity_id = srm.strava_activity_id
                                AND sa.rider_id = srm.rider_id
        JOIN strava_ride_analysis sra ON sra.match_id = srm.id
        WHERE rr.ride_id = %s
          AND rr.status = %s
          AND (rp.strava_data_private IS NULL OR rp.strava_data_private = FALSE)
          AND sra.activity_streams IS NOT NULL
          AND sra.strava_api_error IS NULL
        ORDER BY sa.elapsed_time ASC
    """, (ride_id, RideStatus.FINISHED.value)).fetchall()


def get_strava_activities_in_date_range(rider_id, date_start, date_end):
    """Get Ride-type Strava activities in a date range for matching."""
    return _execute("""
        SELECT strava_activity_id, name, distance, moving_time, elapsed_time,
               total_elevation_gain, start_date_local, strava_url,
               average_heartrate, has_heartrate, average_watts, device_watts
        FROM strava_activity
        WHERE rider_id = %s AND activity_type = 'Ride'
          AND start_date_local::date BETWEEN %s AND %s
        ORDER BY distance DESC
    """, (rider_id, date_start, date_end)).fetchall()


def get_ride_by_id_full(ride_id):
    """Get ride with plan info."""
    return _execute("""
        SELECT ri.*, rp.slug as plan_slug, rp.id as plan_id,
               rp.start_time as plan_start_time
        FROM ride ri
        LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
        WHERE ri.id = %s
    """, (ride_id,)).fetchone()


def get_finished_riders_for_ride(ride_id):
    """Get all FINISHED riders for a ride with their strava match and analysis status.

    Returns list of dicts with rider info, match details, activity summary,
    and a boolean has_analysis flag indicating cached analysis availability.
    Privacy-sensitive riders included (caller handles filtering).
    """
    return _execute("""
        SELECT
            r.id as rider_id,
            r.first_name,
            r.last_name,
            r.rusa_id,
            COALESCE(rp.strava_data_private, FALSE) as strava_data_private,
            srm.id as match_id,
            srm.strava_activity_id,
            sa.strava_url,
            sa.start_date_local,
            sa.distance,
            sa.moving_time,
            sa.elapsed_time,
            sa.total_elevation_gain,
            sa.average_speed,
            sa.average_heartrate,
            sa.max_heartrate,
            sa.has_heartrate,
            sa.average_watts,
            sa.max_watts,
            sa.weighted_average_watts,
            sa.kilojoules,
            sa.device_watts,
            sa.suffer_score,
            CASE WHEN sra.id IS NOT NULL THEN TRUE ELSE FALSE END as has_analysis,
            sra.strava_api_error
        FROM rider_ride rr
        JOIN rider r ON r.id = rr.rider_id
        LEFT JOIN rider_profile rp ON rp.rider_id = r.id
        LEFT JOIN strava_ride_match srm ON srm.rider_id = r.id AND srm.ride_id = rr.ride_id
        LEFT JOIN strava_activity sa ON sa.strava_activity_id = srm.strava_activity_id
        LEFT JOIN strava_ride_analysis sra ON sra.match_id = srm.id
        WHERE rr.ride_id = %s
          AND rr.status = %s
        ORDER BY r.first_name, r.last_name
    """, (ride_id, RideStatus.FINISHED.value)).fetchall()


# ========== COHORT ANALYSIS ==========

def get_ride_cohort_stats(ride_id):
    """Get Strava stats for all riders who finished a ride and share Strava data.

    Returns list of dicts ordered by elapsed_time ASC.
    Excludes riders with strava_data_private = TRUE.
    Uses LEFT JOIN on rider_profile so riders without a profile row are treated as public.
    """
    return _execute("""
        SELECT
            r.id AS rider_id,
            r.first_name,
            r.last_name,
            r.rusa_id,
            sa.elapsed_time,
            sa.moving_time,
            (sa.elapsed_time - sa.moving_time) AS stopped_time,
            sa.average_speed,
            sa.average_heartrate,
            sa.max_heartrate,
            sa.has_heartrate,
            sa.total_elevation_gain,
            sa.suffer_score,
            sa.average_watts,
            sa.weighted_average_watts,
            sa.device_watts,
            sa.strava_url
        FROM rider_ride rr
        JOIN rider r ON r.id = rr.rider_id
        LEFT JOIN rider_profile rp ON rp.rider_id = r.id
        JOIN strava_ride_match srm ON srm.rider_id = r.id AND srm.ride_id = rr.ride_id
        JOIN strava_activity sa ON sa.strava_activity_id = srm.strava_activity_id
                                AND sa.rider_id = srm.rider_id
        WHERE rr.ride_id = %s
          AND rr.status = %s
          AND (rp.strava_data_private IS NULL OR rp.strava_data_private = FALSE)
        ORDER BY sa.elapsed_time ASC
    """, (ride_id, RideStatus.FINISHED.value)).fetchall()


def get_ride_cohort_breakdown(ride_id):
    """Return finisher counts at each filter stage for display in the cohort page header.

    Returns a dict with:
        total_finished  — all riders with FINISHED status
        strava_linked   — subset who have a matched Strava activity
        private         — subset with a match but strava_data_private = TRUE
        compared        — subset actually included in the comparison
    """
    row = _execute("""
        SELECT
            COUNT(*) FILTER (WHERE TRUE)                                   AS total_finished,
            COUNT(*) FILTER (WHERE srm.strava_activity_id IS NOT NULL)     AS strava_linked,
            COUNT(*) FILTER (WHERE srm.strava_activity_id IS NOT NULL
                               AND rp.strava_data_private = TRUE)          AS private,
            COUNT(*) FILTER (WHERE srm.strava_activity_id IS NOT NULL
                               AND (rp.strava_data_private IS NULL
                                    OR rp.strava_data_private = FALSE))    AS compared
        FROM rider_ride rr
        JOIN rider r ON r.id = rr.rider_id
        LEFT JOIN rider_profile rp ON rp.rider_id = r.id
        LEFT JOIN strava_ride_match srm
               ON srm.rider_id = r.id AND srm.ride_id = rr.ride_id
        WHERE rr.ride_id = %s
          AND rr.status = %s
    """, (ride_id, RideStatus.FINISHED.value)).fetchone()
    return dict(row) if row else {'total_finished': 0, 'strava_linked': 0, 'private': 0, 'compared': 0}



# ========== CHAT ==========

def create_conversation(user_id, title=None):
    """Create a new chat conversation. Returns the created row as dict."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "INSERT INTO conversation (user_id, title) VALUES (%s, %s) RETURNING *",
        (user_id, title)
    )
    result = cur.fetchone()
    conn.commit()
    return result


def get_conversation(conversation_id, user_id):
    """Get a conversation by ID. ALWAYS requires user_id — never look up by ID alone (SEC-10)."""
    return _execute(
        "SELECT * FROM conversation WHERE id = %s AND user_id = %s",
        (conversation_id, user_id)
    ).fetchone()


def get_conversations_for_user(user_id, limit=20):
    """Get recent conversations for a user, ordered by last activity."""
    return _execute(
        "SELECT * FROM conversation WHERE user_id = %s ORDER BY last_active_at DESC LIMIT %s",
        (user_id, limit)
    ).fetchall()


def insert_chat_message(conversation_id, role, content, prompt_tokens=None, completion_tokens=None, metadata=None):
    """Insert a chat message. Returns the created row as dict."""
    import json as _json
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """INSERT INTO chat_message
           (conversation_id, role, content, prompt_tokens, completion_tokens, metadata)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
        (conversation_id, role, content, prompt_tokens, completion_tokens,
         _json.dumps(metadata or {}))
    )
    result = cur.fetchone()
    conn.commit()
    return result


def get_recent_messages(conversation_id, limit=20):
    """Fetch last N messages for context window. limit=20 = last 10 turns (SEC-07)."""
    rows = _execute(
        """SELECT role, content FROM chat_message
           WHERE conversation_id = %s
           ORDER BY created_at DESC LIMIT %s""",
        (conversation_id, limit)
    ).fetchall()
    return list(reversed(rows))


def get_rider_privacy_flag(rider_id):
    """Check if rider has strava_data_private set. Returns True if private, False otherwise."""
    row = _execute(
        "SELECT strava_data_private FROM rider_profile WHERE rider_id = %s",
        (rider_id,)
    ).fetchone()
    if row is None:
        return False
    return bool(row.get('strava_data_private'))


def touch_conversation(conversation_id):
    """Update last_active_at timestamp on each message exchange."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE conversation SET last_active_at = NOW() WHERE id = %s",
        (conversation_id,)
    )
    conn.commit()


# ========== WIND DATA ==========

def get_ride_wind_data(ride_id):
    """Return stored wind rows for a ride, ordered by stop_order.

    Returns an empty list if no wind data has been saved for this ride.
    Used by the route handler to avoid re-fetching from the archive API.
    """
    rows = _execute(
        "SELECT * FROM ride_wind_data WHERE ride_id = %s ORDER BY stop_order",
        (ride_id,)
    ).fetchall()
    return list(rows)


def save_ride_wind_data(ride_id, wind_rows):
    """Persist per-stop wind data for a ride.

    Inserts each row with ON CONFLICT (ride_id, stop_order) DO NOTHING so
    calling this function a second time for the same ride is safe — existing
    rows are left unchanged and no error is raised.

    Args:
        ride_id: Integer primary key of the ride.
        wind_rows: List of dicts, each containing stop wind data.
                   Required keys: stop_order, data_source.
                   Optional keys: stop_name, wind_speed_kmh, wind_direction_deg,
                   headwind_kmh, crosswind_kmh, wind_type, temperature_c, conditions.
    """
    if not wind_rows:
        return

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Build a single multi-row INSERT to avoid N+1 round trips (one per stop).
    values = [
        (
            ride_id,
            row.get('stop_order'),
            row.get('stop_name'),
            row.get('wind_speed_kmh'),
            row.get('wind_direction_deg'),
            row.get('headwind_kmh'),
            row.get('crosswind_kmh'),
            row.get('wind_type'),
            row.get('temperature_c'),
            row.get('conditions'),
            row.get('data_source'),
        )
        for row in wind_rows
    ]
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO ride_wind_data (
            ride_id, stop_order, stop_name,
            wind_speed_kmh, wind_direction_deg,
            headwind_kmh, crosswind_kmh,
            wind_type, temperature_c, conditions, data_source
        ) VALUES %s
        ON CONFLICT (ride_id, stop_order) DO UPDATE SET
            stop_name = EXCLUDED.stop_name,
            wind_speed_kmh = EXCLUDED.wind_speed_kmh,
            wind_direction_deg = EXCLUDED.wind_direction_deg,
            headwind_kmh = EXCLUDED.headwind_kmh,
            crosswind_kmh = EXCLUDED.crosswind_kmh,
            wind_type = EXCLUDED.wind_type,
            temperature_c = EXCLUDED.temperature_c,
            conditions = EXCLUDED.conditions,
            data_source = EXCLUDED.data_source,
            fetched_at = NOW()
        """,
        values,
    )
    conn.commit()


# ========== PERSONALITY & COACHING ==========
# CRUD functions for personality_profile, gear_preference, coach_assignment, coaching_guardrail.
# NOT cached — admin edits must be immediately visible.
# All SELECTs include WHERE deleted_at IS NULL.
# All writes call conn.commit().


def get_personality_profile(rider_id, profile_type='coach'):
    """Get active personality profile for a rider. Returns dict or None."""
    return _execute(
        """SELECT * FROM personality_profile
           WHERE rider_id = %s AND profile_type = %s AND deleted_at IS NULL""",
        (rider_id, profile_type)
    ).fetchone()


def get_all_personality_profiles(profile_type=None):
    """Get all active personality profiles, optionally filtered by type."""
    if profile_type:
        return _execute(
            """SELECT * FROM personality_profile
               WHERE profile_type = %s AND deleted_at IS NULL
               ORDER BY rider_id""",
            (profile_type,)
        ).fetchall()
    return _execute(
        """SELECT * FROM personality_profile
           WHERE deleted_at IS NULL ORDER BY rider_id"""
    ).fetchall()


def upsert_personality_profile(rider_id, profile_type, fields, updated_by='system'):
    """Insert or update personality profile. fields dict maps column names to values."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    col_names = list(fields.keys())
    col_values = list(fields.values())
    # Build INSERT columns and placeholders
    all_cols = ['rider_id', 'profile_type', 'updated_by'] + col_names
    all_placeholders = ['%s', '%s', '%s'] + ['%s'] * len(col_names)
    all_values = [rider_id, profile_type, updated_by] + col_values
    # Build ON CONFLICT SET clause
    set_parts = [f"{c} = EXCLUDED.{c}" for c in col_names]
    set_parts.append("updated_by = EXCLUDED.updated_by")
    set_parts.append("updated_at = NOW()")
    cur.execute(
        f"""INSERT INTO personality_profile ({', '.join(all_cols)})
            VALUES ({', '.join(all_placeholders)})
            ON CONFLICT (rider_id, profile_type, extraction_source) DO UPDATE SET
            {', '.join(set_parts)}
            RETURNING *""",
        all_values
    )
    result = cur.fetchone()
    conn.commit()
    return result


def soft_delete_personality_profile(profile_id, updated_by='system'):
    """Soft-delete a personality profile by setting deleted_at."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE personality_profile SET deleted_at = NOW(), updated_by = %s WHERE id = %s",
        (updated_by, profile_id)
    )
    conn.commit()


def get_gear_preference(rider_id, label=None):
    """Get active gear preference for a rider. Returns primary bike or specific label."""
    if label:
        return _execute(
            "SELECT * FROM gear_preference WHERE rider_id = %s AND label = %s AND deleted_at IS NULL",
            (rider_id, label)
        ).fetchone()
    return _execute(
        """SELECT * FROM gear_preference WHERE rider_id = %s AND deleted_at IS NULL
           ORDER BY CASE WHEN label = 'Primary' THEN 0 ELSE 1 END, id LIMIT 1""",
        (rider_id,)
    ).fetchone()


def get_all_gear_for_rider(rider_id):
    """Get all active gear rows (multiple bikes) for a rider."""
    return _execute(
        """SELECT * FROM gear_preference WHERE rider_id = %s AND deleted_at IS NULL
           ORDER BY CASE WHEN label = 'Primary' THEN 0 ELSE 1 END, id""",
        (rider_id,)
    ).fetchall()


def upsert_gear_preference(rider_id, fields, updated_by='system', label='Primary'):
    """Insert or update gear preference. Uses (rider_id, label) for multi-bike support."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    col_names = list(fields.keys())
    col_values = list(fields.values())
    all_cols = ['rider_id', 'label', 'updated_by'] + col_names
    all_placeholders = ['%s', '%s', '%s'] + ['%s'] * len(col_names)
    all_values = [rider_id, label, updated_by] + col_values
    set_parts = [f"{c} = EXCLUDED.{c}" for c in col_names]
    set_parts.append("updated_by = EXCLUDED.updated_by")
    set_parts.append("updated_at = NOW()")
    cur.execute(
        f"""INSERT INTO gear_preference ({', '.join(all_cols)})
            VALUES ({', '.join(all_placeholders)})
            ON CONFLICT (rider_id, label) DO UPDATE SET
            {', '.join(set_parts)}
            RETURNING *""",
        all_values
    )
    result = cur.fetchone()
    conn.commit()
    return result


def get_coach_assignments(coach_rider_id=None, topic_domain=None, active_only=True):
    """Get coach assignments with optional filters. Always excludes soft-deleted."""
    conditions = ["deleted_at IS NULL"]
    params = []
    if active_only:
        conditions.append("is_active = TRUE")
    if coach_rider_id is not None:
        conditions.append("coach_rider_id = %s")
        params.append(coach_rider_id)
    if topic_domain is not None:
        conditions.append("topic_domain = %s")
        params.append(topic_domain)
    where = " AND ".join(conditions)
    return _execute(
        f"SELECT * FROM coach_assignment WHERE {where} ORDER BY topic_domain",
        tuple(params)
    ).fetchall()


def upsert_coach_assignment(coach_rider_id, topic_domain, fields, updated_by='system'):
    """Insert or update coach assignment. fields dict maps column names to values."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    col_names = list(fields.keys())
    col_values = list(fields.values())
    all_cols = ['coach_rider_id', 'topic_domain', 'updated_by'] + col_names
    all_placeholders = ['%s', '%s', '%s'] + ['%s'] * len(col_names)
    all_values = [coach_rider_id, topic_domain, updated_by] + col_values
    set_parts = [f"{c} = EXCLUDED.{c}" for c in col_names]
    set_parts.append("updated_by = EXCLUDED.updated_by")
    set_parts.append("updated_at = NOW()")
    cur.execute(
        f"""INSERT INTO coach_assignment ({', '.join(all_cols)})
            VALUES ({', '.join(all_placeholders)})
            ON CONFLICT (coach_rider_id, topic_domain) DO UPDATE SET
            {', '.join(set_parts)}
            RETURNING *""",
        all_values
    )
    result = cur.fetchone()
    conn.commit()
    return result


def get_active_guardrails(rule_type=None, applies_to=None):
    """Get active guardrails with optional filters. Excludes soft-deleted and inactive."""
    conditions = ["is_active = TRUE", "deleted_at IS NULL"]
    params = []
    if rule_type is not None:
        conditions.append("rule_type = %s")
        params.append(rule_type)
    if applies_to is not None:
        conditions.append("applies_to = %s")
        params.append(applies_to)
    where = " AND ".join(conditions)
    return _execute(
        f"SELECT * FROM coaching_guardrail WHERE {where} ORDER BY rule_type",
        tuple(params)
    ).fetchall()


def insert_guardrail(rule_type, rule_value, applies_to='all', updated_by='system'):
    """Insert a new guardrail row. Returns the inserted row."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """INSERT INTO coaching_guardrail (rule_type, rule_value, applies_to, updated_by)
           VALUES (%s, %s, %s, %s)
           RETURNING *""",
        (rule_type, rule_value, applies_to, updated_by)
    )
    result = cur.fetchone()
    conn.commit()
    return result


def update_guardrail(guardrail_id, fields, updated_by='system'):
    """Update guardrail fields. DB trigger handles rule_version increment."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    set_parts = [f"{k} = %s" for k in fields.keys()]
    set_parts.append("updated_by = %s")
    values = list(fields.values()) + [updated_by, guardrail_id]
    cur.execute(
        f"""UPDATE coaching_guardrail
            SET {', '.join(set_parts)}
            WHERE id = %s
            RETURNING *""",
        values
    )
    result = cur.fetchone()
    conn.commit()
    return result


def get_trait_evidence(rider_id, extraction_source=None):
    """Get personality trait evidence quotes for a rider. Returns list of dicts."""
    if extraction_source:
        return _execute(
            """SELECT * FROM personality_trait_evidence
               WHERE rider_id = %s AND extraction_source = %s
               ORDER BY trait_name, created_at DESC""",
            (rider_id, extraction_source)
        ).fetchall()
    return _execute(
        """SELECT * FROM personality_trait_evidence
           WHERE rider_id = %s
           ORDER BY trait_name, created_at DESC""",
        (rider_id,)
    ).fetchall()


def get_all_guardrails(rule_type=None):
    """Get all non-deleted guardrails (active AND inactive) for admin display."""
    if rule_type:
        return _execute(
            """SELECT * FROM coaching_guardrail
               WHERE deleted_at IS NULL AND rule_type = %s
               ORDER BY rule_type, id""",
            (rule_type,)
        ).fetchall()
    return _execute(
        """SELECT * FROM coaching_guardrail
           WHERE deleted_at IS NULL
           ORDER BY rule_type, id"""
    ).fetchall()


def soft_delete_guardrail(guardrail_id, updated_by='system'):
    """Soft-delete a guardrail by setting deleted_at."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE coaching_guardrail SET deleted_at = NOW(), updated_by = %s WHERE id = %s",
        (updated_by, guardrail_id)
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Knowledge Base (Phase 12)
# ---------------------------------------------------------------------------

def get_knowledge_sources():
    """Return web_* sources with chunk count and embed dates from whatsapp_chunk."""
    return _execute("""
        SELECT source,
               COUNT(*) AS chunk_count,
               MIN(created_at) AS first_embedded,
               MAX(created_at) AS last_embedded
        FROM whatsapp_chunk
        WHERE source LIKE 'web_%%'
        GROUP BY source
        ORDER BY last_embedded DESC
    """).fetchall()


def delete_knowledge_source(source):
    """Delete all chunks for a web_* source. Returns deleted count.

    Raises ValueError if source does not start with 'web_'.
    """
    if not source or not source.startswith('web_'):
        raise ValueError("Can only delete web_ sources, got: " + repr(source))
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "DELETE FROM whatsapp_chunk WHERE source = %s RETURNING id",
        (source,)
    )
    deleted = len(cur.fetchall())
    conn.commit()
    return deleted
