"""Data access layer — all SQL queries live here (PostgreSQL via psycopg2)."""
import json
import secrets
from datetime import datetime, date, timedelta
from enum import Enum
import psycopg2.extras
from db import get_db
from cache import cache, CACHE_TIMEOUT
from services.email_normalize import normalize_email


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
    """Get all rides for a season with club info.

    When a ride is linked to a ride_plan, prefers plan name/distance/elevation
    over ride-level values (avoids stale RUSA-scraped data).
    """
    return _execute("""
        SELECT ri.*,
               COALESCE(rp.name, ri.name) as name,
               COALESCE(rp.distance_km, ri.distance_km) as distance_km,
               COALESCE(rp.total_elevation_ft, ri.elevation_ft) as elevation_ft,
               COALESCE(rp.total_distance_miles, ri.distance_miles) as distance_miles,
               c.code as club_code,
               c.name as club_name,
               c.region as region,
               rp.slug as plan_slug,
               ri.start_time as plan_start_time,
               (c.code = 'TA') as is_team_ride
        FROM ride ri
        INNER JOIN club c ON ri.club_id = c.id
        LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
        WHERE ri.season_id = %s
        ORDER BY ri.date
    """, (season_id,)).fetchall()

@cache.memoize(CACHE_TIMEOUT)
def get_ride_by_id(ride_id):
    """Get a single ride by ID with club info.

    Prefers ride_plan name/distance/elevation when linked.
    """
    return _execute("""
        SELECT ri.*,
               COALESCE(rp.name, ri.name) as name,
               COALESCE(rp.distance_km, ri.distance_km) as distance_km,
               COALESCE(rp.total_elevation_ft, ri.elevation_ft) as elevation_ft,
               COALESCE(rp.total_distance_miles, ri.distance_miles) as distance_miles,
               c.code as club_code,
               c.name as club_name,
               c.region as region,
               rp.slug as plan_slug,
               rp.start_time as plan_start_time,
               (c.code = 'TA') as is_team_ride
        FROM ride ri
        INNER JOIN club c ON ri.club_id = c.id
        LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
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


def get_ride_participants(ride_id):
    """Public ride-result rows ordered with official finishers first."""
    return _execute("""
        SELECT r.id as rider_id, r.first_name, r.last_name, r.rusa_id,
               rr.status, rr.finish_time
        FROM rider_ride rr
        JOIN rider r ON r.id = rr.rider_id
        WHERE rr.ride_id = %s
        ORDER BY
            CASE rr.status
                WHEN 'FINISHED' THEN 1 WHEN 'DNF' THEN 2
                WHEN 'DNS' THEN 3 WHEN 'OTL' THEN 4 ELSE 5
            END,
            rr.finish_time NULLS LAST, r.first_name
    """, (ride_id,)).fetchall()

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


# NOT CACHED - rider-specific data should not be cached in serverless environments
def get_rider_season_elevation_ft(rider_id, season_id):
    """Total climbed elevation (ft) for a rider's finished rides in a season.

    Same FINISHED-ride definition as get_rider_season_stats; kept separate so the
    web season-stats query is untouched. Returns an int (0 when no data)."""
    row = _execute("""
        SELECT COALESCE(SUM(ri.elevation_ft), 0) as elevation_ft
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE rr.rider_id = %s AND ri.season_id = %s AND rr.status = %s
          AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
    """, (rider_id, season_id, RideStatus.FINISHED.value)).fetchone()
    return int(row['elevation_ft']) if row and row['elevation_ft'] else 0


# NOT CACHED - rider-specific data should not be cached in serverless environments
def get_rider_finished_rides_for_season(rider_id, season_id):
    """The rider's finished rides this season (newest first) — id/name/date/distance.

    Same FINISHED-ride definition as get_rider_season_stats, so the list length
    matches the season `rides` count. Powers the app's "rides done" list."""
    rows = _execute("""
        SELECT ri.id, ri.name, ri.date, ri.distance_km
        FROM rider_ride rr
        JOIN ride ri ON rr.ride_id = ri.id
        WHERE rr.rider_id = %s AND ri.season_id = %s AND rr.status = %s
          AND (ri.event_status = 'COMPLETED' OR ri.date < CURRENT_DATE)
        ORDER BY ri.date DESC
    """, (rider_id, season_id, RideStatus.FINISHED.value)).fetchall()
    return [dict(r) for r in rows]


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


@cache.memoize(CACHE_TIMEOUT)
def get_all_riders_with_career_stats(current_season_id=None):
    """Get all riders with career and current season stats for the riders directory page."""
    return _execute("""
        SELECT r.id, r.first_name, r.last_name, r.rusa_id,
               rp.photo_filename,
               sc.eddington_number_miles,
               COUNT(DISTINCT rr.ride_id) FILTER (WHERE rr.status = %s) as total_rides,
               COALESCE(SUM(ri.distance_km) FILTER (WHERE rr.status = %s), 0) as total_kms,
               MAX(ri.date) FILTER (WHERE rr.status = %s) as last_brevet_date,
               -- Current season stats
               COUNT(DISTINCT rr.ride_id) FILTER (
                   WHERE rr.status = %s AND ri.season_id = %s
               ) as season_rides,
               COALESCE(SUM(ri.distance_km) FILTER (
                   WHERE rr.status = %s AND ri.season_id = %s
               ), 0) as season_kms,
               -- SR progress: count of each distance completed this season
               COUNT(DISTINCT rr.ride_id) FILTER (WHERE ri.distance_km >= 200 AND ri.distance_km < 300 AND rr.status = %s AND ri.season_id = %s) as sr_200,
               COUNT(DISTINCT rr.ride_id) FILTER (WHERE ri.distance_km >= 300 AND ri.distance_km < 400 AND rr.status = %s AND ri.season_id = %s) as sr_300,
               COUNT(DISTINCT rr.ride_id) FILTER (WHERE ri.distance_km >= 400 AND ri.distance_km < 600 AND rr.status = %s AND ri.season_id = %s) as sr_400,
               COUNT(DISTINCT rr.ride_id) FILTER (WHERE ri.distance_km >= 600 AND rr.status = %s AND ri.season_id = %s) as sr_600
        FROM rider r
        LEFT JOIN rider_profile rp ON r.id = rp.rider_id
        LEFT JOIN strava_connection sc ON r.id = sc.rider_id
        LEFT JOIN rider_ride rr ON r.id = rr.rider_id
        LEFT JOIN ride ri ON rr.ride_id = ri.id
        GROUP BY r.id, r.first_name, r.last_name, r.rusa_id,
                 rp.photo_filename, sc.eddington_number_miles
        HAVING COUNT(DISTINCT rr.ride_id) FILTER (WHERE rr.status = %s) > 0
        ORDER BY r.first_name, r.last_name
    """, (
        RideStatus.FINISHED.value,  # total_rides
        RideStatus.FINISHED.value,  # total_kms
        RideStatus.FINISHED.value,  # last_brevet_date
        RideStatus.FINISHED.value, current_season_id,  # season_rides
        RideStatus.FINISHED.value, current_season_id,  # season_kms
        RideStatus.FINISHED.value, current_season_id,  # sr_200
        RideStatus.FINISHED.value, current_season_id,  # sr_300
        RideStatus.FINISHED.value, current_season_id,  # sr_400
        RideStatus.FINISHED.value, current_season_id,  # sr_600
        RideStatus.FINISHED.value,  # HAVING
    )).fetchall()


@cache.memoize(CACHE_TIMEOUT)
def get_completed_events_for_season(season_id):
    """Get completed/past events (Team Asha + external) for a season."""
    today = date.today()
    return _execute("""
        SELECT ri.*, c.code as club_code, c.name as club_name, c.region,
               rp.slug as plan_slug,
               rp.rwgps_url as plan_rwgps_url, rp.rwgps_url_team as plan_rwgps_url_team,
               COUNT(rr.id) FILTER (WHERE rr.status = %s) as finisher_count,
               COUNT(rr.id) FILTER (WHERE rr.status IS NOT NULL) as signup_count
        FROM ride ri
        JOIN club c ON ri.club_id = c.id
        LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
        LEFT JOIN rider_ride rr ON rr.ride_id = ri.id
        WHERE ri.season_id = %s AND ri.date < %s
        GROUP BY ri.id, c.code, c.name, c.region, rp.slug, rp.rwgps_url, rp.rwgps_url_team
        ORDER BY ri.date DESC
    """, (RideStatus.FINISHED.value, season_id, today)).fetchall()


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

# NOT CACHED - rider-specific data should not be cached in serverless environments
def get_sr_distances_done(rider_id, season_id, date_filter=False):
    """Which SR distance tiers (200/300/400/600) the rider has at least one
    finished ride in this season. Returns a sorted list, e.g. [200, 400].

    Uses the same query + bucket thresholds as detect_sr_for_rider_season so the
    canonical SR definition stays single-sourced — this just exposes per-tier
    completion for progress display instead of the min-across-buckets count."""
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

    done = set()
    for row in rows:
        d = row['distance_km']
        if 200 <= d < 300:
            done.add(200)
        elif 300 <= d < 400:
            done.add(300)
        elif 400 <= d < 600:
            done.add(400)
        elif d >= 600:
            done.add(600)
    return sorted(done)


# NOT CACHED - rider-specific data should not be cached in serverless environments
def get_sr_counts_by_tier(rider_id, season_id, date_filter=False):
    """How many finished rides the rider has in each SR distance tier this season.

    Returns {200: n, 300: n, 400: n, 600: n}. Same query + bucket thresholds as
    detect_sr_for_rider_season / get_sr_distances_done so the SR definition stays
    single-sourced — this exposes the per-tier *counts* for progress display."""
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

    counts = {200: 0, 300: 0, 400: 0, 600: 0}
    for row in rows:
        d = row['distance_km']
        if 200 <= d < 300:
            counts[200] += 1
        elif 300 <= d < 400:
            counts[300] += 1
        elif 400 <= d < 600:
            counts[400] += 1
        elif d >= 600:
            counts[600] += 1
    return counts


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


# NOT CACHED - rider-specific + date-dependent ('active' tracks the current month)
def get_r12_current_streak(rider_id):
    """The rider's *current* R-12 streak: consecutive recent months each with a
    finished 200+km ride, ending at the most recent qualifying month.

    Reuses the same monthly-qualification rule as detect_r12_awards (200+km,
    finished). Returns {'months': int, 'active': bool}. ``active`` means the chain
    is still alive — its last qualifying month is the current or previous calendar
    month, so the rider can keep it going. A completed-but-stale streak is inactive."""
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
        return {'months': 0, 'active': False}

    def parse_ym(ym_str):
        y, m = ym_str.split('-')
        return int(y), int(m)

    def month_diff(ym1, ym2):
        return (ym2[0] - ym1[0]) * 12 + (ym2[1] - ym1[1])

    parsed = [parse_ym(r['ride_month']) for r in rows]

    # Length of the trailing run of consecutive months (ending at the latest month).
    streak = 1
    for i in range(len(parsed) - 1, 0, -1):
        if month_diff(parsed[i - 1], parsed[i]) == 1:
            streak += 1
        else:
            break

    # Active if the latest qualifying month is this month or last month.
    today = date.today()
    last = parsed[-1]
    months_since_last = month_diff(last, (today.year, today.month))
    active = months_since_last <= 1

    return {'months': streak, 'active': active}


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
               COALESCE(rp.name, ri.name) as route_name,
               COALESCE(rp.distance_km, ri.distance_km) as distance_km,
               COALESCE(rp.total_elevation_ft, ri.elevation_ft) as elevation_ft,
               COALESCE(rp.total_distance_miles, ri.distance_miles) as distance_miles,
               c.code as club_code,
               c.name as club_name,
               c.region as region,
               rp.slug as plan_slug,
               rp.rwgps_url_team as plan_rwgps_url_team,
               rp.start_time as plan_start_time,
               rp.avg_elapsed_speed as plan_avg_speed,
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
    # Shift existing stops: negate first to avoid unique constraint, then set final values
    cur.execute(
        "UPDATE ride_plan_stop SET stop_order = -stop_order WHERE ride_plan_id = %s AND stop_order >= %s",
        (ride_plan_id, stop_order)
    )
    cur.execute(
        "UPDATE ride_plan_stop SET stop_order = -stop_order + 1 WHERE ride_plan_id = %s AND stop_order < 0",
        (ride_plan_id,)
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
    # Reorder remaining stops: negate first to avoid unique constraint, then set final values
    cur.execute(
        "UPDATE ride_plan_stop SET stop_order = -stop_order WHERE ride_plan_id = %s AND stop_order > %s",
        (stop['ride_plan_id'], stop['stop_order'])
    )
    cur.execute(
        "UPDATE ride_plan_stop SET stop_order = -stop_order - 1 WHERE ride_plan_id = %s AND stop_order < 0",
        (stop['ride_plan_id'],)
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


def get_ride_plan_warm_targets(limit=25):
    """Upcoming rides with a route but no linked plan, oldest first."""
    return _execute("""
        SELECT id, name, date, start_time,
               COALESCE(rwgps_url_team, rwgps_url) AS rwgps_url
        FROM ride
        WHERE date >= CURRENT_DATE
          AND ride_plan_id IS NULL
          AND COALESCE(rwgps_url_team, rwgps_url) IS NOT NULL
        ORDER BY date, id
        LIMIT %s
    """, (limit,)).fetchall()


def get_route_plan_operations_status():
    """Upcoming Team Asha route and plan coverage for the admin dashboard."""
    return _execute("""
        SELECT
            COUNT(*) AS upcoming_events,
            COUNT(*) FILTER (
                WHERE COALESCE(rwgps_url_team, rwgps_url) IS NULL
            ) AS missing_routes,
            COUNT(*) FILTER (
                WHERE COALESCE(rwgps_url_team, rwgps_url) IS NOT NULL
                  AND ride_plan_id IS NULL
            ) AS routes_missing_plans,
            COUNT(*) FILTER (WHERE ride_plan_id IS NOT NULL) AS plans_ready
        FROM ride
        WHERE date >= CURRENT_DATE
    """).fetchone()


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
                  elevation_ft, distance_miles, ft_per_mile, rwgps_url,
                  ride_plan_id)
                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    Matches RUSA results to rides using date ±10 days and distance ±20km.

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
               ri.date AS ride_date, ri.distance_km, ri.name AS ride_name
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
        matched_rides = []

        for ride_row in info['rides']:
            ride_date = ride_row['ride_date']
            if not ride_date:
                continue
            if hasattr(ride_date, 'date'):
                ride_date = ride_date.date()

            distance_km = ride_row['distance_km'] or 0

            for rr in rusa_results:
                date_diff = abs((ride_date - rr['date']).days)
                dist_diff = abs(distance_km - rr['distance_km'])
                if date_diff <= 10 and (dist_diff <= 20 or (distance_km >= 1000 and rr['distance_km'] >= 1000)):
                    cur.execute(
                        "UPDATE rider_ride SET finish_time = %s WHERE id = %s",
                        (rr['finish_time'], ride_row['rr_id'])
                    )
                    matched += 1
                    matched_rides.append({
                        'ride': ride_row.get('ride_name', ''),
                        'time': rr['finish_time'],
                    })
                    break

        results.append({
            'rider_name': info['name'],
            'rusa_id': info['rusa_id'],
            'rides_checked': len(info['rides']),
            'results_found': matched,
            'matched_rides': matched_rides,
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
                       start_location=None, time_limit_hours=None,
                       start_time=None, rwgps_url_team=None):
    """Update ride details (route, location, time limit, start time, team route)."""
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

    if start_time is not None:
        updates.append("start_time = %s")
        params.append(start_time if start_time.strip() else None)

    if rwgps_url_team is not None:
        updates.append("rwgps_url_team = %s")
        params.append(rwgps_url_team if rwgps_url_team.strip() else None)

    if updates:
        params.append(ride_id)
        sql = f"UPDATE ride SET {', '.join(updates)} WHERE id = %s"
        cur.execute(sql, params)
        conn.commit()
        return True
    return False

# ========== RIDE PLANS ==========

def update_ride_plan_info(plan_id, name, rwgps_url):
    """Update ride plan template metadata (name and canonical route URL only)."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        UPDATE ride_plan SET name=%s, rwgps_url=%s
        WHERE id=%s
    """, (name or None, rwgps_url or None, plan_id))
    conn.commit()
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

def get_latest_ride_for_plan(plan_id):
    """Get the most recent ride linked to a plan, for deriving defaults."""
    return _execute("""
        SELECT start_time, rwgps_url_team, rwgps_url, time_limit_hours, date
        FROM ride
        WHERE ride_plan_id = %s
        ORDER BY date DESC
        LIMIT 1
    """, (plan_id,)).fetchone()


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
    """Get user by email, CASE-INSENSITIVELY (emails are case-insensitive; the
    Google/Apple paths may store mixed case). NOT CACHED (serverless)."""
    return _execute("SELECT * FROM app_user WHERE lower(email) = lower(%s)",
                    (email,)).fetchone()

def get_user_by_normalized_email(email):
    """Get the account matching an email's canonical form (see
    services/email_normalize), so Gmail dot/+tag variants resolve to ONE account.
    When variants have somehow produced duplicates, prefers a profile-completed
    row (the real account) over an empty one, then the oldest. NOT CACHED."""
    return _execute("""SELECT * FROM app_user WHERE email_normalized = %s
                       ORDER BY (profile_completed IS TRUE) DESC, id ASC LIMIT 1""",
                    (normalize_email(email),)).fetchone()

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
    cur.execute("""INSERT INTO app_user (email, email_normalized, google_id, profile_completed, last_login)
                  VALUES (%s, %s, %s, FALSE, CURRENT_TIMESTAMP)
                  RETURNING id, email, google_id, profile_completed, rider_id""",
               (email, normalize_email(email), google_id))
    user = cur.fetchone()
    conn.commit()
    return dict(user) if user else None

def create_user_password(email, password_hash):
    """Create a new user with an email + password (mobile's 3rd login option).

    ``password_hash`` is a werkzeug hash string (never the plaintext). google_id
    and apple_sub stay NULL — this is a first-party credential. Mirrors
    create_user / create_user_apple.
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""INSERT INTO app_user (email, email_normalized, password_hash, profile_completed, last_login)
                      VALUES (%s, %s, %s, FALSE, CURRENT_TIMESTAMP)
                      RETURNING id, email, password_hash, profile_completed, rider_id""",
                   (email, normalize_email(email), password_hash))
        user = cur.fetchone()
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        # Lost a race against a concurrent signup for the same email (the
        # unique lower(email) index caught it). Surface it so the route → 409.
        conn.rollback()
        raise
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

def get_user_by_apple_sub(apple_sub):
    """Get user by Sign in with Apple subject id. NOT CACHED (serverless)."""
    return _execute("SELECT * FROM app_user WHERE apple_sub = %s", (apple_sub,)).fetchone()

def create_user_apple(email, apple_sub):
    """Create a new user from a Sign in with Apple identity (no google_id)."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""INSERT INTO app_user (email, email_normalized, apple_sub, profile_completed, last_login)
                  VALUES (%s, %s, %s, FALSE, CURRENT_TIMESTAMP)
                  RETURNING id, email, google_id, apple_sub, profile_completed, rider_id""",
               (email, normalize_email(email), apple_sub))
    user = cur.fetchone()
    conn.commit()
    return dict(user) if user else None

def link_apple_sub(user_id, apple_sub):
    """Attach an Apple sub to an EXISTING app_user (account linking by email).

    Lets a member who set up their profile on the web (Google/email) keep their
    rider profile when they first use Sign in with Apple. The ``apple_sub IS
    NULL`` guard makes this a safe no-op if the row already has an Apple id
    (never overwrites a different one). Returns the number of rows updated.
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE app_user SET apple_sub = %s WHERE id = %s AND apple_sub IS NULL",
                (apple_sub, user_id))
    conn.commit()
    return cur.rowcount

def create_user_email_otp(email, phone=None):
    """Create a passwordless user from a verified email OTP signup.

    The email is proven (they received & entered the code), so google_id,
    apple_sub and password_hash all stay NULL. Optional ``phone`` is stored
    UNVERIFIED for a future SMS OTP. Mirrors create_user / create_user_password;
    raises UniqueViolation on a concurrent same-email signup so the route → 409.
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""INSERT INTO app_user (email, email_normalized, phone, profile_completed, last_login)
                      VALUES (%s, %s, %s, FALSE, CURRENT_TIMESTAMP)
                      RETURNING id, email, phone, profile_completed, rider_id""",
                   (email, normalize_email(email), phone))
        user = cur.fetchone()
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise
    return dict(user) if user else None


def set_user_phone(user_id, phone):
    """Store/replace a user's phone number (always UNVERIFIED until an SMS OTP).

    Used when an existing account supplies a phone during an OTP login so a
    future SMS OTP can reach them. No-op (returns 0) when the phone is unchanged.
    Returns the number of rows updated.
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""UPDATE app_user SET phone = %s, phone_verified = FALSE
                   WHERE id = %s AND phone IS DISTINCT FROM %s""",
                (phone, user_id, phone))
    conn.commit()
    return cur.rowcount


# ========== EMAIL OTP (passwordless login) ==========

def create_otp(identifier, code_hash, link_hash, expires_at, channel='email', request_ip=None):
    """Insert an OTP row and return its id.

    ``code_hash`` is a salted werkzeug hash of the 6-digit code; ``link_hash`` is
    sha256 hex of the magic-link token (see services/otp_service.py). Neither
    plaintext is ever stored. ``request_ip`` is kept for per-IP rate limiting.
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""INSERT INTO auth_otp (identifier, channel, code_hash, link_hash, request_ip, expires_at)
                  VALUES (lower(%s), %s, %s, %s, %s, %s) RETURNING id""",
               (identifier, channel, code_hash, link_hash, request_ip, expires_at))
    row = cur.fetchone()
    conn.commit()
    return row['id'] if row else None


def invalidate_active_otps(identifier):
    """Consume any still-live OTPs for ``identifier`` (called before issuing a new
    one) so only the newest code/link is ever valid. This makes the per-code
    attempts cap an effective per-identifier lockout instead of one-per-row.
    Returns the number of rows invalidated.
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""UPDATE auth_otp SET consumed_at = CURRENT_TIMESTAMP
                   WHERE identifier = lower(%s) AND consumed_at IS NULL""", (identifier,))
    conn.commit()
    return cur.rowcount


def count_recent_otps(identifier, since):
    """How many OTPs were issued to ``identifier`` at/after ``since`` (rate limiting)."""
    return _execute("""SELECT COUNT(*) AS n FROM auth_otp
                       WHERE identifier = lower(%s) AND created_at >= %s""",
                    (identifier, since)).fetchone()['n']


def count_recent_otps_by_ip(request_ip, since):
    """How many OTPs were requested from ``request_ip`` at/after ``since``. Guards
    against email-bombing / cross-identifier brute force from one source. Returns
    0 when the IP is unknown (None) so a missing IP never blocks a legit login."""
    if not request_ip:
        return 0
    return _execute("""SELECT COUNT(*) AS n FROM auth_otp
                       WHERE request_ip = %s AND created_at >= %s""",
                    (request_ip, since)).fetchone()['n']


def get_active_otp_by_identifier(identifier):
    """Newest live (unconsumed, unexpired) OTP for ``identifier``, or None."""
    return _execute("""SELECT * FROM auth_otp
                       WHERE identifier = lower(%s) AND consumed_at IS NULL
                         AND expires_at > CURRENT_TIMESTAMP
                       ORDER BY created_at DESC LIMIT 1""", (identifier,)).fetchone()


def get_active_otp_by_link_hash(link_hash):
    """Newest live OTP matching a magic-link token's sha256 hash, or None."""
    return _execute("""SELECT * FROM auth_otp
                       WHERE link_hash = %s AND consumed_at IS NULL
                         AND expires_at > CURRENT_TIMESTAMP
                       ORDER BY created_at DESC LIMIT 1""", (link_hash,)).fetchone()


def increment_otp_attempts(otp_id):
    """Bump the wrong-attempt counter; return the new count (or None if gone)."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("UPDATE auth_otp SET attempts = attempts + 1 WHERE id = %s RETURNING attempts",
                (otp_id,))
    row = cur.fetchone()
    conn.commit()
    return row['attempts'] if row else None


def consume_otp(otp_id):
    """Atomically mark an OTP consumed. Returns True iff THIS call consumed it.

    The ``consumed_at IS NULL`` guard makes redemption single-use even under a
    concurrent double-submit — the loser sees rowcount 0.
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""UPDATE auth_otp SET consumed_at = CURRENT_TIMESTAMP
                   WHERE id = %s AND consumed_at IS NULL""", (otp_id,))
    conn.commit()
    return cur.rowcount == 1


def delete_account(user_id, preserve_rider=False):
    """Permanently delete an account for App Store Guideline 5.1.1(v).

    Removes the app_user (login identity) and, unless ``preserve_rider`` is set,
    the linked rider and ALL rider-scoped data. Most rider-child tables are
    ON DELETE CASCADE (strava_*, rider_live_*, rider_ride, custom_ride_plan,
    gear_preference, personality_*, ...) so deleting the rider removes them; the
    two NO ACTION references (app_user.rider_id and rider_profile.rider_id) and
    app_user's NO ACTION referrer (access_request.reviewed_by_user_id) are
    detached/deleted explicitly, in order.

    ``preserve_rider`` keeps a shared rider intact when only the login should go
    (the demo/reviewer account — its rider is re-linked on the next demo login).

    Returns True if a user was deleted, False if no such user. Raises (after
    rollback) on any DB error so the caller can fail cleanly rather than
    half-delete.
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT rider_id FROM app_user WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return False
        rider_id = row['rider_id']

        # Detach the one NO ACTION referrer of app_user, then delete the login
        # account (conversation rows cascade off app_user).
        cur.execute("UPDATE access_request SET reviewed_by_user_id = NULL WHERE reviewed_by_user_id = %s", (user_id,))
        cur.execute("DELETE FROM app_user WHERE id = %s", (user_id,))

        if rider_id and not preserve_rider:
            # rider_profile is NO ACTION → delete before the rider; deleting the
            # rider cascades the rest of the rider-scoped data.
            cur.execute("DELETE FROM rider_profile WHERE rider_id = %s", (rider_id,))
            cur.execute("DELETE FROM rider WHERE id = %s", (rider_id,))

        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise

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


def update_strava_athlete_metrics(rider_id, ftp=None):
    """Persist provider-supplied athlete metrics for one connected rider."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE strava_connection SET ftp = %s WHERE rider_id = %s",
        (ftp, rider_id),
    )
    conn.commit()
    cache.delete_memoized(get_strava_connection, rider_id)


def delete_strava_connection(rider_id):
    """Atomically delete one rider's complete private Strava footprint."""
    conn = get_db()
    cur = conn.cursor()
    try:
        # Analyses cascade from matches. Delete matches before activities so no
        # cached comparison can survive removal of its source activity.
        cur.execute(
            "DELETE FROM strava_ride_match WHERE rider_id = %s",
            (rider_id,),
        )
        matches = cur.rowcount
        cur.execute(
            "DELETE FROM strava_activity WHERE rider_id = %s",
            (rider_id,),
        )
        activities = cur.rowcount
        cur.execute(
            "DELETE FROM strava_connection WHERE rider_id = %s",
            (rider_id,),
        )
        connections = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "connections": connections,
        "activities": activities,
        "matches": matches,
    }


# ========== GARMIN CONNECT ==========

def get_garmin_connection(rider_id, include_tokens=False):
    """Return one rider's Garmin connection; ciphertext is opt-in for sync only."""
    columns = (
        "rider_id, token_ciphertext, display_name, status, connected_at, "
        "last_sync_at, last_error, updated_at, activity_sync_cursor, "
        "activity_sync_since, activity_history_complete"
        if include_tokens else
        "rider_id, display_name, status, connected_at, last_sync_at, "
        "last_error, updated_at, activity_sync_cursor, activity_sync_since, "
        "activity_history_complete"
    )
    row = _execute(
        f"SELECT {columns} FROM garmin_connection WHERE rider_id = %s",
        (rider_id,),
    ).fetchone()
    return dict(row) if row else None


def update_garmin_activity_sync_state(
        rider_id, *, cursor, since, complete):
    """Persist one rider's bounded Garmin history continuation state."""
    _execute(
        "UPDATE garmin_connection SET activity_sync_cursor=%s, "
        "activity_sync_since=%s, activity_history_complete=%s, "
        "updated_at=NOW() WHERE rider_id=%s",
        (cursor, since, complete, rider_id),
    )
    get_db().commit()


def upsert_garmin_connection(rider_id, token_ciphertext, display_name=None):
    """Persist encrypted Garmin tokens for the owning rider."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "INSERT INTO garmin_connection "
        "  (rider_id, token_ciphertext, display_name, status) "
        "VALUES (%s, %s, %s, 'connected') "
        "ON CONFLICT (rider_id) DO UPDATE SET "
        "  token_ciphertext = EXCLUDED.token_ciphertext, "
        "  display_name = EXCLUDED.display_name, status = 'connected', "
        "  last_error = NULL, updated_at = NOW() "
        "RETURNING rider_id, display_name, status, connected_at, updated_at",
        (rider_id, token_ciphertext, display_name),
    )
    row = cur.fetchone()
    conn.commit()
    return dict(row) if row else None


def delete_garmin_connection(rider_id):
    """Atomically delete one rider's complete private Garmin footprint."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM garmin_mfa_challenge WHERE rider_id = %s",
            (rider_id,),
        )
        challenges = cur.rowcount
        cur.execute(
            "DELETE FROM garmin_activity WHERE rider_id = %s",
            (rider_id,),
        )
        activities = cur.rowcount
        cur.execute(
            "DELETE FROM garmin_performance_snapshot WHERE rider_id = %s",
            (rider_id,),
        )
        snapshots = cur.rowcount
        cur.execute(
            "DELETE FROM garmin_connection WHERE rider_id = %s",
            (rider_id,),
        )
        connections = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "connections": connections,
        "challenges": challenges,
        "activities": activities,
        "snapshots": snapshots,
    }


def save_garmin_mfa_challenge(rider_id, state_ciphertext):
    """Replace a rider's MFA challenge with a fresh ten-minute challenge."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO garmin_mfa_challenge "
        "  (rider_id, state_ciphertext, expires_at, attempts) "
        "VALUES (%s, %s, NOW() + INTERVAL '10 minutes', 0) "
        "ON CONFLICT (rider_id) DO UPDATE SET "
        "  state_ciphertext = EXCLUDED.state_ciphertext, "
        "  expires_at = EXCLUDED.expires_at, attempts = 0, created_at = NOW()",
        (rider_id, state_ciphertext),
    )
    conn.commit()


def take_garmin_mfa_attempt(rider_id):
    """Atomically count and return one valid rider-owned MFA attempt."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "UPDATE garmin_mfa_challenge SET attempts = attempts + 1 "
        "WHERE rider_id = %s AND expires_at > NOW() AND attempts < 5 "
        "RETURNING state_ciphertext, expires_at, attempts",
        (rider_id,),
    )
    row = cur.fetchone()
    conn.commit()
    return dict(row) if row else None


def delete_garmin_mfa_challenge(rider_id):
    """Delete only the specified rider's pending MFA state."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM garmin_mfa_challenge WHERE rider_id = %s",
                (rider_id,))
    conn.commit()


def upsert_garmin_performance_snapshot(rider_id, snapshot, raw_ciphertext,
                                       token_ciphertext):
    """Persist one private daily snapshot and any refreshed Garmin tokens."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO garmin_performance_snapshot "
        " (rider_id, snapshot_date, resting_heart_rate, hrv_status, "
        "  sleep_score, body_battery, training_readiness, vo2_max_cycling, "
        "  training_status, readiness_level, readiness_feedback, "
        "  recovery_time_minutes, sleep_factor_percent, acwr_factor_percent, "
        "  hrv_factor_percent, endurance_score, acute_training_load, "
        "  load_level_trend, raw_ciphertext) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (rider_id, snapshot_date) DO UPDATE SET "
        " resting_heart_rate=EXCLUDED.resting_heart_rate, "
        " hrv_status=EXCLUDED.hrv_status, sleep_score=EXCLUDED.sleep_score, "
        " body_battery=EXCLUDED.body_battery, "
        " training_readiness=EXCLUDED.training_readiness, "
        " vo2_max_cycling=EXCLUDED.vo2_max_cycling, "
        " training_status=EXCLUDED.training_status, "
        " readiness_level=EXCLUDED.readiness_level, "
        " readiness_feedback=EXCLUDED.readiness_feedback, "
        " recovery_time_minutes=EXCLUDED.recovery_time_minutes, "
        " sleep_factor_percent=EXCLUDED.sleep_factor_percent, "
        " acwr_factor_percent=EXCLUDED.acwr_factor_percent, "
        " hrv_factor_percent=EXCLUDED.hrv_factor_percent, "
        " endurance_score=EXCLUDED.endurance_score, "
        " acute_training_load=EXCLUDED.acute_training_load, "
        " load_level_trend=EXCLUDED.load_level_trend, "
        " raw_ciphertext=EXCLUDED.raw_ciphertext, synced_at=NOW()",
        (rider_id, snapshot["date"], snapshot.get("resting_heart_rate"),
         snapshot.get("hrv_status"), snapshot.get("sleep_score"),
         snapshot.get("body_battery"), snapshot.get("training_readiness"),
         snapshot.get("vo2_max_cycling"), snapshot.get("training_status"),
         snapshot.get("readiness_level"), snapshot.get("readiness_feedback"),
         snapshot.get("recovery_time_minutes"),
         snapshot.get("sleep_factor_percent"),
         snapshot.get("acwr_factor_percent"),
         snapshot.get("hrv_factor_percent"),
         snapshot.get("endurance_score"),
         snapshot.get("acute_training_load"),
         snapshot.get("load_level_trend"),
         raw_ciphertext),
    )
    cur.execute(
        "UPDATE garmin_connection SET token_ciphertext=%s, status='connected', "
        "last_sync_at=NOW(), last_error=NULL, updated_at=NOW() WHERE rider_id=%s",
        (token_ciphertext, rider_id),
    )
    conn.commit()


def get_latest_garmin_performance_snapshot(rider_id):
    """Return only the owning rider's latest normalized Garmin headlines."""
    row = _execute(
        "SELECT snapshot_date, resting_heart_rate, hrv_status, sleep_score, "
        "body_battery, training_readiness, vo2_max_cycling, training_status, "
        "readiness_level, readiness_feedback, recovery_time_minutes, "
        "sleep_factor_percent, acwr_factor_percent, hrv_factor_percent, "
        "endurance_score, acute_training_load, load_level_trend, "
        "synced_at FROM garmin_performance_snapshot WHERE rider_id=%s "
        "ORDER BY snapshot_date DESC LIMIT 1",
        (rider_id,),
    ).fetchone()
    return dict(row) if row else None


def upsert_garmin_activities(rider_id, activities):
    """Upsert normalized, encrypted Garmin activities for one owning rider."""
    conn = get_db()
    cur = conn.cursor()
    for activity, raw_ciphertext in activities:
        cur.execute(
            "INSERT INTO garmin_activity "
            "(rider_id, garmin_activity_id, activity_name, activity_type, "
            " started_at, distance_m, duration_s, moving_duration_s, "
            " elevation_gain_m, average_hr, max_hr, average_power, max_power, "
            " normalized_power, aerobic_training_effect, "
            " anaerobic_training_effect, calories, average_cadence, "
            " device_name, raw_ciphertext) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (rider_id, garmin_activity_id) DO UPDATE SET "
            "activity_name=EXCLUDED.activity_name, "
            "activity_type=EXCLUDED.activity_type, started_at=EXCLUDED.started_at, "
            "distance_m=EXCLUDED.distance_m, duration_s=EXCLUDED.duration_s, "
            "moving_duration_s=EXCLUDED.moving_duration_s, "
            "elevation_gain_m=EXCLUDED.elevation_gain_m, "
            "average_hr=EXCLUDED.average_hr, max_hr=EXCLUDED.max_hr, "
            "average_power=EXCLUDED.average_power, max_power=EXCLUDED.max_power, "
            "normalized_power=EXCLUDED.normalized_power, "
            "aerobic_training_effect=EXCLUDED.aerobic_training_effect, "
            "anaerobic_training_effect=EXCLUDED.anaerobic_training_effect, "
            "calories=EXCLUDED.calories, average_cadence=EXCLUDED.average_cadence, "
            "device_name=EXCLUDED.device_name, "
            "raw_ciphertext=EXCLUDED.raw_ciphertext, synced_at=NOW()",
            (rider_id, activity["garmin_activity_id"],
             activity.get("activity_name"), activity.get("activity_type"),
             activity.get("started_at"), activity.get("distance_m"),
             activity.get("duration_s"), activity.get("moving_duration_s"),
             activity.get("elevation_gain_m"), activity.get("average_hr"),
             activity.get("max_hr"), activity.get("average_power"),
             activity.get("max_power"), activity.get("normalized_power"),
             activity.get("aerobic_training_effect"),
             activity.get("anaerobic_training_effect"),
             activity.get("calories"), activity.get("average_cadence"),
             activity.get("device_name"), raw_ciphertext),
        )
    conn.commit()


def get_recent_garmin_activities(rider_id, limit=10):
    """Return private normalized activity rows for the owning rider."""
    limit = max(1, min(int(limit), 50))
    rows = _execute(
        "SELECT garmin_activity_id, activity_name, activity_type, started_at, "
        "distance_m, duration_s, moving_duration_s, elevation_gain_m, "
        "average_hr, max_hr, average_power, max_power, normalized_power, "
        "aerobic_training_effect, anaerobic_training_effect, calories, "
        "average_cadence, device_name, synced_at "
        "FROM garmin_activity WHERE rider_id=%s "
        "ORDER BY started_at DESC NULLS LAST LIMIT %s",
        (rider_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def get_garmin_activities_for_matching(rider_id, limit=2000):
    """Return normalized Garmin fields needed by the private matcher."""
    limit = max(1, min(int(limit), 2000))
    rows = _execute(
        "SELECT rider_id, garmin_activity_id, activity_name, activity_type, "
        "started_at, distance_m, duration_s, moving_duration_s "
        "FROM garmin_activity WHERE rider_id=%s "
        "ORDER BY started_at DESC LIMIT %s",
        (rider_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def get_strava_activities_for_matching(rider_id, limit=2000):
    """Return normalized Strava fields needed by the private matcher."""
    limit = max(1, min(int(limit), 2000))
    rows = _execute(
        "SELECT rider_id, strava_activity_id, name, activity_type, start_date, "
        "start_date_local, distance, elapsed_time, moving_time "
        "FROM strava_activity WHERE rider_id=%s "
        "ORDER BY start_date DESC LIMIT %s",
        (rider_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def upsert_activity_source_matches(rider_id, matches):
    """Replace derived matches while preserving rider-reviewed decisions."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM activity_source_match "
            "WHERE rider_id=%s AND match_status='auto'",
            (rider_id,),
        )
        for match in matches:
            if match["rider_id"] != rider_id:
                raise ValueError("Activity match does not belong to rider")
            cur.execute(
                "INSERT INTO activity_source_match "
                "(rider_id, garmin_activity_id, strava_activity_id, "
                " confidence, reasons, match_status) "
                "VALUES (%s, %s, %s, %s, %s, 'auto') "
                "ON CONFLICT DO NOTHING",
                (rider_id, match["garmin_activity_id"],
                 match["strava_activity_id"], match["confidence"],
                 psycopg2.extras.Json(match["reasons"])),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_authoritative_brevet_source_links(rider_id):
    """Reuse existing Strava-to-brevet decisions and attach Garmin provenance."""
    rows = _execute(
        "SELECT srm.ride_id, srm.rider_id, srm.strava_activity_id, "
        "asm.id AS source_match_id, asm.garmin_activity_id "
        "FROM strava_ride_match srm "
        "JOIN rider_ride rr ON rr.rider_id=srm.rider_id "
        " AND rr.ride_id=srm.ride_id AND rr.status=%s "
        "LEFT JOIN activity_source_match asm ON asm.rider_id=srm.rider_id "
        " AND asm.strava_activity_id=srm.strava_activity_id "
        "WHERE srm.rider_id=%s",
        (RideStatus.FINISHED.value, rider_id),
    ).fetchall()
    return [{
        **dict(row),
        "confidence": 1.0,
        "match_status": "authoritative",
        "reasons": {"existing_strava_brevet_match": True},
    } for row in rows]


def get_finished_brevets_for_matching(rider_id):
    """Return only finished brevet candidates owned by the rider."""
    rows = _execute(
        "SELECT r.id AS ride_id, r.name AS ride_name, r.date, "
        "r.distance_km, r.start_time, r.ride_type "
        "FROM rider_ride rr JOIN ride r ON r.id=rr.ride_id "
        "WHERE rr.rider_id=%s AND rr.status=%s "
        "ORDER BY r.date DESC",
        (rider_id, RideStatus.FINISHED.value),
    ).fetchall()
    return [dict(row) for row in rows]


def replace_activity_brevet_matches(rider_id, matches):
    """Replace derived brevet links while preserving rider-reviewed decisions."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM activity_brevet_match WHERE rider_id=%s "
            "AND match_status IN ('auto', 'authoritative')",
            (rider_id,),
        )
        for match in matches:
            if match["rider_id"] != rider_id:
                raise ValueError("Brevet match does not belong to rider")
            cur.execute(
                "INSERT INTO activity_brevet_match "
                "(rider_id, ride_id, source_match_id, garmin_activity_id, "
                "strava_activity_id, confidence, reasons, match_status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (rider_id, match["ride_id"], match.get("source_match_id"),
                 match.get("garmin_activity_id"),
                 match.get("strava_activity_id"), match["confidence"],
                 psycopg2.extras.Json(match["reasons"]),
                 match["match_status"]),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_garmin_recordings_for_brevet(rider_id, ride_id):
    """Return every owned Garmin recording associated with one brevet."""
    rows = _execute(
        "SELECT ga.garmin_activity_id, ga.activity_name, ga.started_at, "
        "ga.distance_m, ga.duration_s, ga.moving_duration_s, "
        "ga.elevation_gain_m, ga.average_hr, ga.max_hr, ga.average_power, "
        "ga.max_power, ga.normalized_power, ga.aerobic_training_effect, "
        "ga.anaerobic_training_effect, ga.calories, ga.average_cadence, "
        "ga.device_name, abm.confidence AS source_confidence, "
        "abm.reasons AS match_reasons "
        "FROM activity_brevet_match abm "
        "JOIN garmin_activity ga ON ga.rider_id=abm.rider_id "
        " AND ga.garmin_activity_id=abm.garmin_activity_id "
        "WHERE abm.rider_id=%s AND abm.ride_id=%s "
        "AND abm.match_status <> 'rejected' "
        "ORDER BY ga.started_at, ga.garmin_activity_id",
        (rider_id, ride_id),
    ).fetchall()
    if rows:
        return [dict(row) for row in rows]

    # Compatibility for a reviewed Strava match before derived brevet
    # provenance has refreshed.
    rows = _execute(
        "SELECT ga.garmin_activity_id, ga.activity_name, ga.started_at, "
        "ga.distance_m, ga.duration_s, ga.moving_duration_s, "
        "ga.elevation_gain_m, ga.average_hr, ga.max_hr, ga.average_power, "
        "ga.max_power, ga.normalized_power, ga.aerobic_training_effect, "
        "ga.anaerobic_training_effect, ga.calories, ga.average_cadence, "
        "ga.device_name, asm.confidence AS source_confidence "
        "FROM strava_ride_match srm "
        "JOIN activity_source_match asm ON asm.rider_id=srm.rider_id "
        " AND asm.strava_activity_id=srm.strava_activity_id "
        "JOIN garmin_activity ga ON ga.rider_id=asm.rider_id "
        " AND ga.garmin_activity_id=asm.garmin_activity_id "
        "WHERE srm.rider_id=%s AND srm.ride_id=%s "
        "ORDER BY ga.started_at, ga.garmin_activity_id",
        (rider_id, ride_id),
    ).fetchall()
    return [dict(row) for row in rows]


def get_strava_recordings_for_brevet(rider_id, ride_id):
    """Return all private Strava parts linked through derived provenance."""
    rows = _execute(
        "SELECT sa.strava_activity_id, sa.name, sa.start_date, "
        "sa.distance, sa.elapsed_time, sa.moving_time, "
        "sa.total_elevation_gain, sa.average_heartrate, sa.max_heartrate, "
        "sa.average_watts, sa.max_watts, sa.weighted_average_watts, "
        "sa.average_cadence, sa.kilojoules, sa.suffer_score, sa.strava_url, "
        "abm.confidence, abm.reasons "
        "FROM activity_brevet_match abm "
        "JOIN strava_activity sa ON sa.rider_id=abm.rider_id "
        " AND sa.strava_activity_id=abm.strava_activity_id "
        "WHERE abm.rider_id=%s AND abm.ride_id=%s "
        "AND abm.match_status <> 'rejected' "
        "ORDER BY sa.start_date, sa.strava_activity_id",
        (rider_id, ride_id),
    ).fetchall()
    return [dict(row) for row in rows]


def get_garmin_metrics_for_brevet(rider_id, ride_id):
    """Return duration-weighted metrics across every matched Garmin part."""
    from services.activity_matching import aggregate_garmin_recordings
    return aggregate_garmin_recordings(
        get_garmin_recordings_for_brevet(rider_id, ride_id))


def get_garmin_brevet_match_review(rider_id, limit=50):
    """Return private Garmin rides with Strava and brevet match provenance."""
    limit = max(1, min(int(limit), 100))
    rows = _execute(
        "SELECT ga.garmin_activity_id, ga.activity_name, ga.started_at, "
        "ga.distance_m, ga.duration_s, ga.moving_duration_s, "
        "ga.elevation_gain_m, ga.device_name, "
        "asm.id AS source_match_id, asm.strava_activity_id, "
        "asm.confidence AS source_confidence, "
        "asm.match_status AS source_match_status, "
        "asm.reasons AS source_reasons, "
        "sa.name AS strava_name, sa.start_date AS strava_started_at, "
        "sa.distance AS strava_distance_m, "
        "sa.elapsed_time AS strava_elapsed_s, "
        "sa.moving_time AS strava_moving_s, "
        "sa.total_elevation_gain AS strava_elevation_gain_m, "
        "abm.id AS match_id, abm.ride_id, abm.confidence, "
        "abm.match_status, abm.reasons, r.name AS ride_name, "
        "r.date AS ride_date, r.distance_km AS ride_distance_km "
        "FROM garmin_activity ga "
        "LEFT JOIN activity_source_match asm "
        " ON asm.rider_id=ga.rider_id "
        " AND asm.garmin_activity_id=ga.garmin_activity_id "
        " AND asm.match_status <> 'rejected' "
        "LEFT JOIN strava_activity sa "
        " ON sa.rider_id=asm.rider_id "
        " AND sa.strava_activity_id=asm.strava_activity_id "
        "LEFT JOIN activity_brevet_match abm "
        " ON abm.rider_id=ga.rider_id "
        " AND abm.garmin_activity_id=ga.garmin_activity_id "
        "LEFT JOIN ride r ON r.id=abm.ride_id "
        "WHERE ga.rider_id=%s "
        "ORDER BY ga.started_at DESC NULLS LAST LIMIT %s",
        (rider_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def set_manual_garmin_brevet_match(rider_id, garmin_activity_id, ride_id):
    """Link one owned Garmin activity to one owned finished brevet."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM garmin_activity "
            "WHERE rider_id=%s AND garmin_activity_id=%s",
            (rider_id, garmin_activity_id),
        )
        if not cur.fetchone():
            raise ValueError("Garmin activity does not belong to rider")
        cur.execute(
            "SELECT 1 FROM rider_ride "
            "WHERE rider_id=%s AND ride_id=%s AND status=%s",
            (rider_id, ride_id, RideStatus.FINISHED.value),
        )
        if not cur.fetchone():
            raise ValueError("Brevet does not belong to rider")

        cur.execute(
            "SELECT id, strava_activity_id FROM activity_source_match "
            "WHERE rider_id=%s AND garmin_activity_id=%s "
            "AND match_status <> 'rejected' LIMIT 1",
            (rider_id, garmin_activity_id),
        )
        source = cur.fetchone()
        source_match_id = source["id"] if source else None
        strava_activity_id = source["strava_activity_id"] if source else None

        cur.execute(
            "DELETE FROM activity_brevet_match WHERE rider_id=%s "
            "AND (garmin_activity_id=%s "
            " OR (%s IS NOT NULL AND source_match_id=%s) "
            " OR (%s IS NOT NULL AND strava_activity_id=%s))",
            (rider_id, garmin_activity_id, source_match_id, source_match_id,
             strava_activity_id, strava_activity_id),
        )
        cur.execute(
            "INSERT INTO activity_brevet_match "
            "(rider_id, ride_id, source_match_id, garmin_activity_id, "
            "strava_activity_id, confidence, reasons, match_status) "
            "VALUES (%s,%s,%s,%s,%s,1.0,%s,'manual')",
            (rider_id, ride_id, source_match_id, garmin_activity_id,
             strava_activity_id,
             psycopg2.extras.Json({"rider_selected": True})),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def reject_garmin_brevet_match(rider_id, garmin_activity_id):
    """Reject an existing owned Garmin-to-brevet link persistently."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE activity_brevet_match SET match_status='rejected', "
            "reasons=reasons || %s::jsonb, updated_at=NOW() "
            "WHERE rider_id=%s AND garmin_activity_id=%s "
            "AND match_status <> 'rejected'",
            (psycopg2.extras.Json({"rider_rejected": True}),
             rider_id, garmin_activity_id),
        )
        changed = cur.rowcount
        conn.commit()
        return bool(changed)
    except Exception:
        conn.rollback()
        raise


def mark_garmin_reauth_required(rider_id):
    """Record an auth failure without exposing its details to other riders."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE garmin_connection SET status='reauth_required', "
        "last_error='Garmin authentication expired', updated_at=NOW() "
        "WHERE rider_id=%s",
        (rider_id,),
    )
    conn.commit()

def consume_strava_broker_handoff(code):
    """Atomically consume a one-time Strava broker handoff row (delete-on-read).

    A single ``DELETE ... RETURNING`` enforces BOTH invariants at once:
      - single-use: a second consume of the same code deletes nothing (zero rows).
      - freshness: the TTL gate reads ONLY ``handoff_expires_at`` (the short
        one-time-code window), never the ~6h Strava-token column — so an expired
        code can never be accepted while the underlying token is still alive.

    Returns the row (with ``expires_at`` as the Strava token's epoch integer, ready
    for ``create_strava_connection``) or ``None`` if the code is unknown, already
    consumed, or expired. The handoff row is written by BrevetHub into the neutral
    ``rp_strava_broker_handoff`` broker table (see migration 035); Team Asha only
    reads+deletes it here.
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        DELETE FROM rp_strava_broker_handoff
        WHERE code = %s AND handoff_expires_at > NOW()
        RETURNING ta_rider_id, strava_athlete_id, access_token, refresh_token,
                  EXTRACT(EPOCH FROM strava_token_expires_at)::bigint AS expires_at,
                  scope
    """, (code,))
    row = cur.fetchone()
    conn.commit()
    return row

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
            device_watts, average_speed, max_speed, suffer_score, strava_url,
            average_cadence, average_temp, calories, pr_count, achievement_count,
            gear_id, elev_high, elev_low, trainer, commute, workout_type,
            map_summary_polyline, start_latlng, end_latlng
        ) VALUES (
            %(rider_id)s, %(strava_activity_id)s, %(name)s, %(activity_type)s,
            %(distance)s, %(moving_time)s, %(elapsed_time)s, %(total_elevation_gain)s,
            %(start_date)s, %(start_date_local)s, %(average_heartrate)s,
            %(max_heartrate)s, %(has_heartrate)s, %(average_watts)s, %(max_watts)s,
            %(weighted_average_watts)s, %(kilojoules)s, %(device_watts)s,
            %(average_speed)s, %(max_speed)s, %(suffer_score)s, %(strava_url)s,
            %(average_cadence)s, %(average_temp)s, %(calories)s, %(pr_count)s,
            %(achievement_count)s, %(gear_id)s, %(elev_high)s, %(elev_low)s,
            %(trainer)s, %(commute)s, %(workout_type)s,
            %(map_summary_polyline)s, %(start_latlng)s, %(end_latlng)s
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
            average_cadence = EXCLUDED.average_cadence,
            average_temp = EXCLUDED.average_temp,
            calories = EXCLUDED.calories,
            pr_count = EXCLUDED.pr_count,
            achievement_count = EXCLUDED.achievement_count,
            gear_id = EXCLUDED.gear_id,
            elev_high = EXCLUDED.elev_high,
            elev_low = EXCLUDED.elev_low,
            trainer = EXCLUDED.trainer,
            commute = EXCLUDED.commute,
            workout_type = EXCLUDED.workout_type,
            map_summary_polyline = EXCLUDED.map_summary_polyline,
            start_latlng = EXCLUDED.start_latlng,
            end_latlng = EXCLUDED.end_latlng,
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


# NOT CACHED - rider-specific data (serverless SimpleCache convention)
def get_rider_activity_baseline(rider_id, days=365, min_distance_km=180):
    """Compute a rider's own historical norms over ~1 year of long Strava rides.

    Used as the baseline that the rich ride analysis compares a single ride
    against. Only cycling activities at or above `min_distance_km` within the
    last `days` are considered. Returns {} if fewer than 3 rides qualify.

    Returns a plain dict:
      {
        'n_rides': int,
        'avg_speed_mph': float,        # mean of average_speed (m/s) * 2.23694
        'median_speed_mph': float,
        'avg_watts': int|None,         # mean average_watts over rides w/ power
        'avg_np_watts': int|None,      # mean weighted_average_watts (Strava NP)
        'avg_hr': int|None,            # mean average_heartrate
        'avg_cadence': int|None,       # mean average_cadence
        'avg_suffer': int|None,        # mean suffer_score
        'avg_elev_per_mile_ft': float|None,
      }
    """
    rows = _execute("""
        SELECT distance, total_elevation_gain, average_speed,
               average_watts, weighted_average_watts, average_heartrate,
               average_cadence, suffer_score
        FROM strava_activity
        WHERE rider_id = %s
          AND activity_type IN (
              'Ride', 'VirtualRide', 'MountainBikeRide', 'GravelRide',
              'EBikeRide', 'Handcycle', 'Velomobile'
          )
          AND distance >= %s
          AND start_date >= NOW() - MAKE_INTERVAL(days => %s)
        ORDER BY start_date_local DESC
    """, (rider_id, min_distance_km * 1000, days)).fetchall()

    if len(rows) < 3:
        return {}

    def _mean(vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    def _median(vals):
        vals = sorted(v for v in vals if v is not None)
        if not vals:
            return None
        mid = len(vals) // 2
        if len(vals) % 2:
            return vals[mid]
        return (vals[mid - 1] + vals[mid]) / 2

    speeds_mph = [r['average_speed'] * 2.23694 for r in rows
                  if r['average_speed'] is not None]

    elev_per_mile = []
    for r in rows:
        dist_m = r['distance']
        gain_m = r['total_elevation_gain']
        if dist_m and gain_m is not None and dist_m > 0:
            elev_per_mile.append((gain_m * 3.28084) / (dist_m / 1609.34))

    avg_speed = _mean(speeds_mph)
    median_speed = _median(speeds_mph)
    avg_watts = _mean([r['average_watts'] for r in rows])
    avg_np_watts = _mean([r['weighted_average_watts'] for r in rows])
    avg_hr = _mean([r['average_heartrate'] for r in rows])
    avg_cadence = _mean([r['average_cadence'] for r in rows])
    avg_suffer = _mean([r['suffer_score'] for r in rows])
    avg_elev = _mean(elev_per_mile)

    return {
        'n_rides': len(rows),
        'avg_speed_mph': round(avg_speed, 1) if avg_speed is not None else 0.0,
        'median_speed_mph': round(median_speed, 1) if median_speed is not None else 0.0,
        'avg_watts': round(avg_watts) if avg_watts is not None else None,
        'avg_np_watts': round(avg_np_watts) if avg_np_watts is not None else None,
        'avg_hr': round(avg_hr) if avg_hr is not None else None,
        'avg_cadence': round(avg_cadence) if avg_cadence is not None else None,
        'avg_suffer': round(avg_suffer) if avg_suffer is not None else None,
        'avg_elev_per_mile_ft': round(avg_elev, 1) if avg_elev is not None else None,
    }


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
               ri.start_time as start_time,
               rp.rwgps_url as plan_rwgps_url, ri.rwgps_url_team as plan_rwgps_url_team,
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


def _set_or_remove_rider_note(match_id, path, note):
    """Set (or, for an empty note, remove) one rider_notes value, in place.

    ``path`` is a JSONB path as a Python list (e.g. ``['overall']`` or
    ``['segments', location]``); it is bound as a ``text[]`` param so an
    arbitrary key string can never be interpolated into SQL. A non-empty
    ``note`` is written with jsonb_set, creating any missing intermediate
    object; an empty/blank note REMOVES the key (``#-``) so cleared notes leave
    no residue and read back as absent.

    Returns the number of rows updated (0 when no analysis row exists).
    """
    conn = get_db()
    cur = conn.cursor()
    if (note or '').strip():
        # Ensure the parent object exists, then set the leaf. For a 2-element
        # path the parent is path[:-1]; a 1-element path has no parent object.
        if len(path) > 1:
            parent = path[:-1]
            cur.execute("""
                UPDATE strava_ride_analysis
                SET rider_notes = jsonb_set(
                        jsonb_set(
                            COALESCE(rider_notes, '{}'::jsonb),
                            %s::text[],
                            COALESCE(rider_notes #> %s::text[], '{}'::jsonb),
                            true),
                        %s::text[],
                        %s::jsonb,
                        true)
                WHERE match_id = %s
            """, (parent, parent, path, json.dumps(note), match_id))
        else:
            cur.execute("""
                UPDATE strava_ride_analysis
                SET rider_notes = jsonb_set(
                        COALESCE(rider_notes, '{}'::jsonb),
                        %s::text[], %s::jsonb, true)
                WHERE match_id = %s
            """, (path, json.dumps(note), match_id))
    else:
        cur.execute("""
            UPDATE strava_ride_analysis
            SET rider_notes = rider_notes #- %s::text[]
            WHERE match_id = %s
        """, (path, match_id))
    conn.commit()
    return cur.rowcount


def update_overall_note(match_id, note):
    """Set/clear the rider's note about the whole ride (rider_notes.overall)."""
    return _set_or_remove_rider_note(match_id, ['overall'], note)


def update_segment_note(match_id, location, note):
    """Set/clear a rider's note on one planned segment (rider_notes.segments.<location>).

    Segments are keyed by their (stable, human-meaningful) ``location`` — the
    same key ``ride_strava_analysis`` uses for ``segment_eval`` and ``stop_wind``
    — so notes stay attached across re-analysis. (Caveat inherited from that
    keying scheme: on a loop route where two segments share a ``location`` name,
    a note maps to both; unplanned stops avoid this by keying on distance.)
    """
    return _set_or_remove_rider_note(match_id, ['segments', location], note)


def update_stop_note(match_id, key, note):
    """Set/clear a rider's note on one UNPLANNED stop (rider_notes.stops.<key>).

    Unplanned (is_extra) stops have no clean location, so they are keyed by
    their rounded cumulative distance (a stable, unique per-stop string),
    avoiding the label collisions bare ``location`` would cause.
    """
    return _set_or_remove_rider_note(match_id, ['stops', key], note)


def get_rider_rides_with_cached_streams(rider_id):
    """Get all finished rides for a rider that have cached Strava stream data.

    Returns ride metadata plus the compressed activity_streams blob for each ride.
    Only includes rides where stream analysis completed successfully.
    """
    return _execute("""
        SELECT r.id AS ride_id, r.name AS ride_name, r.ride_plan_id,
               r.date, r.distance_km,
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


def get_rider_rides_metadata_for_comparison(rider_id):
    """Get ride metadata (no streams) for the brevet comparison selector list."""
    return _execute("""
        SELECT r.id AS ride_id, r.name AS ride_name, r.ride_plan_id,
               r.date, r.distance_km,
               r.elevation_ft,
               s.name AS season_name,
               sa.elapsed_time, sa.moving_time, sa.distance AS strava_distance_m,
               sa.total_elevation_gain, sa.average_speed,
               sa.average_heartrate, sa.max_heartrate, sa.has_heartrate,
               sa.average_watts, sa.weighted_average_watts, sa.device_watts,
               sa.suffer_score, sa.strava_url
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


def get_rider_rides_with_cached_streams_by_ids(rider_id, ride_ids):
    """Get finished rides with cached streams for specific ride IDs."""
    return _execute("""
        SELECT r.id AS ride_id, r.name AS ride_name, r.ride_plan_id,
               r.date, r.distance_km,
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
          AND r.id = ANY(%s)
          AND sra.activity_streams IS NOT NULL
          AND sra.strava_api_error IS NULL
        ORDER BY r.date DESC
    """, (rider_id, RideStatus.FINISHED.value, ride_ids)).fetchall()


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
               ri.start_time as plan_start_time
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
            sa.average_cadence,
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
                   headwind_kmh, crosswind_kmh, wind_type, temperature_c, conditions,
                   wind_gust_kmh, temp_min_c, temp_max_c.
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
            row.get('wind_gust_kmh'),
            row.get('temp_min_c'),
            row.get('temp_max_c'),
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
            wind_type, temperature_c, conditions, data_source,
            wind_gust_kmh, temp_min_c, temp_max_c
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
            wind_gust_kmh = EXCLUDED.wind_gust_kmh,
            temp_min_c = EXCLUDED.temp_min_c,
            temp_max_c = EXCLUDED.temp_max_c,
            fetched_at = NOW()
        """,
        values,
    )
    conn.commit()


# ========== ROUTE WEATHER CACHE (async forecast cron — TA-237) ==========
# Weather is pre-fetched hourly by /api/cron/fetch-route-weather and READ from
# route_weather_cache on every request path (no live Open-Meteo on the request path).

def get_route_weather_cache(route_id, forecast_date):
    """Return the stored Open-Meteo forecast for a route on a date, or None.

    Row shape: {'route_id', 'forecast_date', 'weather_data' (list of per-sample
    forecast dicts), 'sample_points' (list of {lat,lng,distance_m}), 'fetched_at'}.
    Populated hourly by the fetch-route-weather cron; every request path READS from
    here instead of calling Open-Meteo live (TA-237).
    """
    return _execute(
        "SELECT route_id, forecast_date, weather_data, sample_points, fetched_at "
        "FROM route_weather_cache WHERE route_id = %s AND forecast_date = %s",
        (route_id, forecast_date),
    ).fetchone()


def save_route_weather_cache(route_id, forecast_date, weather_data, sample_points,
                             elevation_track=None):
    """Upsert one route's forecast for a date (idempotent on (route_id, forecast_date)).

    Overwrites the payload + sample points (+ the elevation track for the rpv2 gradient
    elevation profile) and bumps fetched_at, so each hourly cron run refreshes the
    last-good row in place. ``elevation_track`` is the downsampled
    ``[{lat, lng, dist_m, e_m}, ...]`` route track (optional — None on a route with no
    usable points; the plan render then draws an empty profile). Only called from the
    fetch-route-weather cron — never on a request path. On a cron fetch failure the
    caller simply skips this upsert, leaving the previous last-good row untouched.
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        INSERT INTO route_weather_cache
            (route_id, forecast_date, weather_data, sample_points, elevation_track,
             fetched_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (route_id, forecast_date) DO UPDATE SET
            weather_data = EXCLUDED.weather_data,
            sample_points = EXCLUDED.sample_points,
            elevation_track = EXCLUDED.elevation_track,
            fetched_at = NOW()
        """,
        (route_id, forecast_date,
         psycopg2.extras.Json(weather_data), psycopg2.extras.Json(sample_points),
         psycopg2.extras.Json(elevation_track) if elevation_track is not None else None),
    )
    conn.commit()


def get_route_elevation_track(route_id):
    """The cron-warmed elevation track for a route, or None.

    Returns the ``[{lat, lng, dist_m, e_m}, ...]`` track cached in the route-keyed
    route_geometry_cache (route geometry is date-invariant), for the rpv2 plan-page
    gradient elevation profile to read from cache instead of fetching RWGPS live on the
    request path (the TA-237 guest-safety invariant). The warm-plan-elevation cron
    populates it for every route referenced by a ride_plan (past and upcoming), so any
    plan's profile is served once warmed. None when the route has no cached track yet
    (new route, or the cron has not run) — the render then degrades to an empty profile.
    """
    row = _execute(
        "SELECT elevation_track FROM route_geometry_cache "
        "WHERE route_id = %s AND elevation_track IS NOT NULL",
        (route_id,),
    ).fetchone()
    return row['elevation_track'] if row else None


def upsert_route_geometry(route_id, elevation_track):
    """Insert or refresh one route's cached elevation track (idempotent on route_id).

    Route geometry is date-invariant, so this is keyed on the RWGPS route id alone.
    Only called by the warm-plan-elevation cron with a successful fetch, so a transient
    RWGPS failure never overwrites a last-good row (the caller skips the upsert). The
    track is the downsampled ``[{lat, lng, dist_m, e_m}, ...]`` shared.live_radial
    output that build_elevation_profile consumes; None on a route with no usable points.
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        INSERT INTO route_geometry_cache (route_id, elevation_track, fetched_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (route_id) DO UPDATE SET
            elevation_track = EXCLUDED.elevation_track,
            fetched_at = NOW()
        """,
        (route_id, psycopg2.extras.Json(elevation_track) if elevation_track is not None else None),
    )
    conn.commit()


def get_route_geometry_freshness(route_id):
    """The fetched_at of a route's cached geometry, or None — for the cron fresh-skip.

    Only counts a row that actually has a track: a NULL-track row (a fetch that yielded
    no usable points) returns None so the cron re-warms it rather than pinning an empty
    profile for the whole freshness window.
    """
    row = _execute(
        "SELECT fetched_at FROM route_geometry_cache "
        "WHERE route_id = %s AND elevation_track IS NOT NULL",
        (route_id,),
    ).fetchone()
    return row['fetched_at'] if row else None


def get_upcoming_weather_targets(within_days=28):
    """Rides needing pre-fetched weather: upcoming rides within `within_days` OR
    live/active rides, that have a date.

    One row per ride: ride_id, forecast_date (the ride date), name, the RWGPS url, and
    plan_id. The fetch-route-weather cron extracts the route id, gates on the 16-day
    forecast horizon, and upserts route_weather_cache. NOT cached — the cron needs fresh
    dates each run. "Live/active" = a ride currently pointed at by rider_live_tracking,
    so a multi-day ride that started before the window is still covered.
    """
    today = date.today()
    cutoff = today + timedelta(days=within_days)
    rows = _execute("""
        SELECT ri.id AS ride_id, ri.date AS forecast_date, ri.name AS name,
               COALESCE(rp.rwgps_url_team, ri.rwgps_url_team, ri.rwgps_url) AS rwgps_url,
               rp.id AS plan_id
        FROM ride ri
        LEFT JOIN ride_plan rp ON ri.ride_plan_id = rp.id
        WHERE ri.date IS NOT NULL
          AND (
                (ri.date >= %s AND ri.date <= %s)
             OR ri.id IN (
                    SELECT active_ride_id FROM rider_live_tracking
                    WHERE enabled = TRUE AND active_ride_id IS NOT NULL
                )
          )
        ORDER BY ri.date
    """, (today, cutoff)).fetchall()
    return [dict(r) for r in rows]


def get_upcoming_ride_date_for_plan(plan_id):
    """Return the date of this plan's next upcoming ride (>= today), or None.

    The ride-plan-detail pages render a route class (not a dated event), so they key the
    stored forecast on the plan's next scheduled running of the route. None when the plan
    has no upcoming dated ride — the page then shows no wind (a stored-cache miss), the
    same graceful degradation as any route without a stored forecast.
    """
    row = _execute("""
        SELECT MIN(date) AS next_date
        FROM ride
        WHERE ride_plan_id = %s AND date >= CURRENT_DATE
    """, (plan_id,)).fetchone()
    return row['next_date'] if row else None


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


# ========== LIVE TRACKING (rider location) ==========

def get_live_tracking(rider_id):
    """Get a rider's live-tracking prefs row, or None if never set."""
    return _execute(
        "SELECT * FROM rider_live_tracking WHERE rider_id = %s",
        (rider_id,)
    ).fetchone()


def set_live_tracking_enabled(rider_id, enabled):
    """Set the master opt-in flag for a rider, preserving any Garmin session.

    Used by the global settings toggle and the one-tap beacon switch — neither
    should clobber the per-ride Garmin link registered from a ride's live map.
    Returns True on success.
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO rider_live_tracking (rider_id, enabled, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (rider_id) DO UPDATE
              SET enabled = EXCLUDED.enabled, updated_at = now()
        """, (rider_id, bool(enabled)))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def set_ride_garmin(rider_id, ride_id, session_url, session_token):
    """Register a Garmin LiveTrack link for ONE specific ride and opt the rider in.

    Garmin mints a fresh session per activity, so the link is inherently
    per-ride: saving one points the rider's tracking at `ride_id` (active_ride_id)
    and enables tracking. Positions the cron ingests are then tagged with this
    ride, so they only show on that ride's map. Returns True on success.
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO rider_live_tracking
                (rider_id, enabled, garmin_session_url, garmin_session_token,
                 active_ride_id, updated_at)
            VALUES (%s, TRUE, %s, %s, %s, now())
            ON CONFLICT (rider_id) DO UPDATE
              SET enabled = TRUE,
                  garmin_session_url = EXCLUDED.garmin_session_url,
                  garmin_session_token = EXCLUDED.garmin_session_token,
                  active_ride_id = EXCLUDED.active_ride_id,
                  updated_at = now()
        """, (rider_id, session_url, session_token, ride_id))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def clear_ride_garmin(rider_id, ride_id):
    """Remove the rider's Garmin link if it's currently pointed at `ride_id`.

    Leaves the master opt-in flag alone (they may still beacon). No-op if the
    active ride is a different one. Returns True on success.
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            UPDATE rider_live_tracking
               SET garmin_session_url = NULL, garmin_session_token = NULL,
                   active_ride_id = NULL, updated_at = now()
             WHERE rider_id = %s AND active_ride_id = %s
        """, (rider_id, ride_id))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def get_enabled_live_tracking():
    """All riders opted in WITH a Garmin session pointed at a specific ride.

    The poll cron iterates these and tags ingested points with active_ride_id.
    """
    return _execute("""
        SELECT rider_id, garmin_session_url, garmin_session_token, active_ride_id
        FROM rider_live_tracking
        WHERE enabled = TRUE
          AND garmin_session_token IS NOT NULL
          AND active_ride_id IS NOT NULL
    """).fetchall()


def _coerce_num(value, cast):
    """Best-effort cast to int/float; None on failure."""
    if value is None:
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def insert_live_position(rider_id, lat, lng, recorded_at, source, accuracy=None,
                         speed=None, heart_rate=None, power=None, cadence=None,
                         ride_id=None):
    """Insert one position point. Validates/clamps coordinates.

    `ride_id` tags the point to a specific ride so it only shows on that ride's
    map (NULL points are not shown on any per-ride map). Optional telemetry
    fields (speed m/s, heart_rate bpm, power W, cadence rpm) are stored when the
    source provides them; bad values are coerced to NULL. Returns True on
    success, False if coordinates are invalid (out of range).
    """
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return False
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
        return False

    accuracy = _coerce_num(accuracy, float)
    speed = _coerce_num(speed, float)
    heart_rate = _coerce_num(heart_rate, int)
    power = _coerce_num(power, int)
    cadence = _coerce_num(cadence, int)

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO rider_live_position
                (rider_id, lat, lng, accuracy, recorded_at, source,
                 speed, heart_rate, power, cadence, ride_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (rider_id, lat, lng, accuracy, recorded_at, source,
              speed, heart_rate, power, cadence, ride_id))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False


def get_positions_for_rider_since(rider_id, since, ride_id=None):
    """Ordered position history for a rider since `since` (oldest → newest).

    Scoped to one ride when `ride_id` is given, so elapsed/moving/stopped time
    and recent speed never mix points from a different ride. Used by the live
    telemetry block.
    """
    if ride_id is not None:
        return _execute("""
            SELECT lat, lng, accuracy, recorded_at, source, speed, heart_rate, power, cadence
            FROM rider_live_position
            WHERE rider_id = %s AND ride_id = %s AND recorded_at >= %s
            ORDER BY recorded_at ASC
        """, (rider_id, ride_id, since)).fetchall()
    return _execute("""
        SELECT lat, lng, accuracy, recorded_at, source, speed, heart_rate, power, cadence
        FROM rider_live_position
        WHERE rider_id = %s AND recorded_at >= %s
        ORDER BY recorded_at ASC
    """, (rider_id, since)).fetchall()


def get_last_position_recorded_at(rider_id, ride_id=None):
    """Most recent stored position timestamp for a rider (optionally one ride)."""
    if ride_id is not None:
        row = _execute("""
            SELECT MAX(recorded_at) AS last_at
            FROM rider_live_position WHERE rider_id = %s AND ride_id = %s
        """, (rider_id, ride_id)).fetchone()
    else:
        row = _execute("""
            SELECT MAX(recorded_at) AS last_at
            FROM rider_live_position WHERE rider_id = %s
        """, (rider_id,)).fetchone()
    return row['last_at'] if row else None


def get_latest_positions_for_ride(ride_id, since):
    """Latest position per opted-in rider sharing for a ride, newer than `since`.

    A rider shows up purely because they opted in (tracking enabled) AND have
    points tagged to THIS ride (p.ride_id) — i.e. they set up Garmin or started
    the beacon FROM this ride's map. That per-ride share is the opt-in, so signup
    status is irrelevant: riders appear whether or not they're marked Going, or
    even signed up at all. rider_ride is LEFT-joined only to colour the dot by
    signup status when one happens to exist (NULL → default colour).
    `since` is the display-window cutoff (a datetime). Returns rows with
    rider_id, name, lat, lng, recorded_at, source, status (status may be NULL).
    """
    return _execute("""
        SELECT DISTINCT ON (p.rider_id)
               p.rider_id,
               r.first_name || ' ' || COALESCE(r.last_name, '') AS name,
               p.lat, p.lng, p.recorded_at,
               p.speed, p.heart_rate, p.power, p.cadence, p.source,
               rr.status
        FROM rider_live_position p
        JOIN rider r ON r.id = p.rider_id
        JOIN rider_live_tracking t ON t.rider_id = p.rider_id
        LEFT JOIN rider_ride rr ON rr.rider_id = p.rider_id AND rr.ride_id = %s
        WHERE p.ride_id = %s
          AND t.enabled = TRUE
          AND p.recorded_at >= %s
        ORDER BY p.rider_id, p.recorded_at DESC
    -- params: rr.ride_id (LEFT JOIN, for the status colour), p.ride_id (the ride
    -- gate — points are tagged to this ride only), recency cutoff.
    """, (ride_id, ride_id, since)).fetchall()


def purge_old_positions(retention_days=7):
    """Delete position points older than the retention window. Returns count."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        DELETE FROM rider_live_position
        WHERE created_at < now() - (%s || ' days')::interval
        RETURNING id
    """, (str(int(retention_days)),))
    deleted = len(cur.fetchall())
    conn.commit()
    return deleted


# ========== LIVE INVITE CODES (public per-ride map access) ==========

# Unambiguous alphabet — no I/O/0/1 so a code is easy to read aloud / type.
_INVITE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def _generate_invite_code():
    """A short, typeable code like 'ABCD-2K9P' (8 chars, ~40 bits)."""
    raw = ''.join(secrets.choice(_INVITE_ALPHABET) for _ in range(8))
    return f'{raw[:4]}-{raw[4:]}'


def get_or_create_ride_invite(ride_id, created_by, expires_at):
    """Return an existing unexpired invite code for the ride, or mint one.

    One shared code per ride keeps things simple — any club member who opens
    the ride gets the same code to share. Returns the code, or None on failure.
    """
    existing = _execute(
        "SELECT code FROM live_invite_code WHERE ride_id = %s "
        "AND (expires_at IS NULL OR expires_at > now()) "
        "ORDER BY created_at DESC LIMIT 1", (ride_id,)).fetchone()
    if existing:
        return existing['code']

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    for _ in range(5):                      # retry on the rare code collision
        code = _generate_invite_code()
        try:
            cur.execute(
                "INSERT INTO live_invite_code (code, ride_id, created_by, expires_at) "
                "VALUES (%s, %s, %s, %s)", (code, ride_id, created_by, expires_at))
            conn.commit()
            return code
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            continue
        except Exception:
            conn.rollback()
            return None
    return None


def get_valid_ride_invite(code):
    """Return {code, ride_id, expires_at} for a non-expired code, else None.

    Normalizes case/whitespace so a guest can type it casually.
    """
    if not code:
        return None
    norm = str(code).strip().upper()
    return _execute(
        "SELECT code, ride_id, expires_at FROM live_invite_code "
        "WHERE code = %s AND (expires_at IS NULL OR expires_at > now())",
        (norm,)).fetchone()
