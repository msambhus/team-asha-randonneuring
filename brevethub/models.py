"""BrevetHub data model — rp_* tables only.

Every SQL statement in this module targets a `rp_`-prefixed tenant table. The
app never reads or writes any Team Asha table; `tests/brevethub/test_rp_only.py`
scans this file and fails the build if a non-`rp_` table name ever appears.
"""
import secrets
from enum import Enum

import psycopg2.extras
from psycopg2 import Binary
from psycopg2.extras import Json

from brevethub import db


class RideStatus(str, Enum):
    """BrevetHub own ride-status enum — defined here so BrevetHub shares no code
    with the parent web app models. Kept as a str-Enum for direct SQL binding.

    Pre-ride:  interested / maybe / going / withdraw.
    Post-ride: finished / dnf / dns / otl (a result, set once the event date passed).

    The helper classmethods below mirror the parent web app state machine so the
    routes can gate transitions without re-deriving the rules. BrevetHub stays
    LOWERCASE where the parent web app uses uppercase status strings — a deliberate,
    documented divergence (see the web-parity notes in the PR).
    """
    # Pre-ride statuses
    INTERESTED = 'interested'
    MAYBE = 'maybe'
    GOING = 'going'
    WITHDRAW = 'withdraw'
    # Post-ride result statuses (the event date has passed)
    FINISHED = 'finished'
    DNF = 'dnf'
    DNS = 'dns'
    OTL = 'otl'

    @classmethod
    def normalize(cls, value):
        """Coerce a raw status string to a RideStatus member (lowercase).

        Raises ValueError when the value is empty or not one of the eight members.
        BrevetHub has no legacy status values, so there is no legacy remapping — the
        one deliberate divergence versus the parent web app normalize, which carries
        a YES / NO / SIGNED_UP legacy table BrevetHub never had.
        """
        if value is None or not str(value).strip():
            raise ValueError('Status cannot be empty')
        val = str(value).strip().lower()
        try:
            return cls(val)
        except ValueError:
            raise ValueError('Invalid status: ' + str(value))

    @classmethod
    def is_pre_ride(cls, status):
        """True when the status is a pre-ride intent (interested / maybe / going).

        Mirrors the parent web app: withdraw is deliberately NOT pre-ride here, so a
        withdrawn row is never cleared or auto-finalized like an active intent.
        """
        return status in (cls.INTERESTED, cls.MAYBE, cls.GOING)

    @classmethod
    def is_post_ride(cls, status):
        """True when the status is a post-ride result (finished / dnf / dns / otl)."""
        return status in (cls.FINISHED, cls.DNF, cls.DNS, cls.OTL)

    @classmethod
    def is_successful(cls, status):
        """True only when the status is a successful finish (finished)."""
        return status == cls.FINISHED

    @classmethod
    def can_remove(cls, status):
        """True when a sign-up in this status may be cleared by the rider.

        Only a pre-ride intent (interested / maybe / going) may be removed; a
        withdraw or any post-ride result is retained as history.
        """
        return status in (cls.INTERESTED, cls.MAYBE, cls.GOING)


# --------------------------------------------------------------------------- #
# Clubs (rp_club) — the tenant directory riders pick from at signup.
# --------------------------------------------------------------------------- #
def get_all_clubs():
    """All clubs, alphabetical, for the signup picker and /api/clubs."""
    return db.query(
        "SELECT id, name, city, state, rusa_club_id "
        "FROM rp_club ORDER BY name ASC"
    )


def get_club(club_id):
    return db.query_one(
        "SELECT id, name, city, state, rusa_club_id FROM rp_club WHERE id = %s",
        (club_id,),
    )


def club_exists(club_id):
    return get_club(club_id) is not None


# --------------------------------------------------------------------------- #
# Riders (rp_rider) — one row per authenticated BrevetHub user.
# --------------------------------------------------------------------------- #
def get_rider_by_google_id(google_id):
    return db.query_one(
        "SELECT id, email, google_id, rusa_id, club_id, "
        "       profile_completed, rusa_id_duplicate, created_at, last_login_at "
        "FROM rp_rider WHERE google_id = %s",
        (google_id,),
    )


def get_rider_by_id(rider_id):
    """The rider row for the signed-in session (the own-profile loader).

    Carries the cached Eddington columns (km + miles + calculated_at) so the own
    profile renders the number without a second query; they are NULL until the
    first compute (on Strava connect or the daily cron), which the template shows
    as a graceful prompt rather than a fabricated zero.
    """
    return db.query_one(
        "SELECT id, email, google_id, rusa_id, club_id, "
        "       profile_completed, rusa_id_duplicate, created_at, last_login_at, "
        "       eddington_km, eddington_miles, eddington_calculated_at "
        "FROM rp_rider WHERE id = %s",
        (rider_id,),
    )


def create_rider(email, google_id):
    """Create a rider on first Google sign-in. Profile is incomplete until they
    finish signup (optional RUSA ID + club)."""
    return db.execute(
        "INSERT INTO rp_rider (email, google_id, profile_completed) "
        "VALUES (%s, %s, FALSE) "
        "RETURNING id, email, google_id, rusa_id, club_id, "
        "          profile_completed, rusa_id_duplicate, created_at, last_login_at",
        (email, google_id),
        returning=True,
    )


def update_rider_login(rider_id):
    db.execute(
        "UPDATE rp_rider SET last_login_at = NOW() WHERE id = %s",
        (rider_id,),
    )


def rusa_id_already_claimed(rusa_id, exclude_rider_id=None):
    """True if another rider has already claimed this RUSA ID. Used to *soft-flag*
    duplicates at signup — v1 does no hard RUSA ownership verification."""
    if not rusa_id:
        return False
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM rp_rider "
        "WHERE rusa_id = %s AND (%s::int IS NULL OR id <> %s)",
        (rusa_id, exclude_rider_id, exclude_rider_id),
    )
    return bool(row and row['n'] > 0)


def complete_rider_profile(rider_id, rusa_id, club_id, rusa_id_duplicate=False):
    """Store the optional RUSA ID + chosen club and mark the profile complete."""
    return db.execute(
        "UPDATE rp_rider "
        "SET rusa_id = %s, club_id = %s, rusa_id_duplicate = %s, "
        "    profile_completed = TRUE "
        "WHERE id = %s "
        "RETURNING id, email, google_id, rusa_id, club_id, "
        "          profile_completed, rusa_id_duplicate, created_at, last_login_at",
        (rusa_id, club_id, rusa_id_duplicate, rider_id),
        returning=True,
    )


def set_rider_eddington(rider_id, *, eddington_km, eddington_miles):
    """Cache the computed cycling Eddington number for one rider (both units) and
    stamp the calculation time. Keyed by rider_id, so a rider only ever writes their
    OWN value; the write is rp_-only and additive. Called OFF the request path (on
    Strava connect and by the daily refresh cron), never at public-view time.
    """
    db.execute(
        "UPDATE rp_rider "
        "SET eddington_km = %s, eddington_miles = %s, "
        "    eddington_calculated_at = NOW() "
        "WHERE id = %s",
        (eddington_km, eddington_miles, rider_id),
    )


# --------------------------------------------------------------------------- #
# Public rides (rp_ride) + live positions (rp_live_position) — guest/spectator
# browse of rides opted into public tracking, and the owner-only ingestion path
# that feeds the live map. Every guest-facing query selects ONLY non-PII columns
# (name/club/distance/start/status) — never rider email — and the map/poll gates
# hard-filter on is_public = TRUE so a private or unknown ride is never viewable.
# --------------------------------------------------------------------------- #
def get_public_rides():
    """Public rides for the guest browse list, joined to their club name.

    Guest-facing: selects only what a club would publicly show (name, club,
    distance, start time, status). No rider identity (no email, no rider_id) is
    exposed on this surface.
    """
    return db.query(
        "SELECT r.id, r.name, r.distance_km, r.start_at, r.status, "
        "       c.name AS club_name "
        "FROM rp_ride r LEFT JOIN rp_club c ON c.id = r.club_id "
        "WHERE r.is_public = TRUE ORDER BY r.start_at DESC NULLS LAST"
    )


def get_public_ride(ride_id):
    """A single PUBLIC ride by id (the guest-view 404 gate), joined to its club.

    Returns None when the ride is unknown OR is_public = FALSE, so a private/
    unknown ride is indistinguishable to a guest (both 404). No rider PII.
    """
    return db.query_one(
        "SELECT r.id, r.name, r.distance_km, r.start_at, r.status, "
        "       c.name AS club_name "
        "FROM rp_ride r LEFT JOIN rp_club c ON c.id = r.club_id "
        "WHERE r.id = %s AND r.is_public = TRUE",
        (ride_id,),
    )


def get_ride(ride_id):
    """A ride by id for the OWNER check (position POST / flag-public).

    Includes rider_id so the caller can compare it to the session rider before
    allowing a write. Never rendered to a guest.
    """
    return db.query_one(
        "SELECT id, club_id, rider_id, name, distance_km, start_at, status, "
        "       is_public, rwgps_url FROM rp_ride WHERE id = %s",
        (ride_id,),
    )


def get_rider_rides(rider_id):
    """A rider's own rides, for the create/flag page listing + share links."""
    return db.query(
        "SELECT id, name, distance_km, start_at, status, is_public "
        "FROM rp_ride WHERE rider_id = %s ORDER BY start_at DESC NULLS LAST",
        (rider_id,),
    )


def create_ride(rider_id, *, name, distance_km=None, is_public=False,
                club_id=None, status=None):
    """Create a ride owned by ``rider_id`` and return its new id.

    Used by the rider-facing "share a live ride" flow: the owner names a ride and
    (optionally) flags it public+live in one step. start_at defaults to NOW() so a
    just-created live ride sorts to the top of the public list.
    """
    row = db.execute(
        "INSERT INTO rp_ride (rider_id, club_id, name, distance_km, is_public, "
        "                     status, start_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, NOW()) RETURNING id",
        (rider_id, club_id, name, distance_km, is_public, status),
        returning=True,
    )
    return row['id'] if row else None


def set_ride_public(ride_id, rider_id, is_public):
    """Flag one of the rider's OWN rides public/private. Owner-scoped: the write
    is filtered by rider_id too, so a non-owner can never flip another rider's flag.

    Returns the updated row (id) or None when the ride is not the rider's.
    """
    return db.execute(
        "UPDATE rp_ride SET is_public = %s WHERE id = %s AND rider_id = %s "
        "RETURNING id",
        (is_public, ride_id, rider_id),
        returning=True,
    )


def get_ride_positions(ride_id, limit=500):
    """The ride position breadcrumbs (oldest→newest) for the live trail.

    Guest-facing: selects ONLY lat/lng/recorded_at — never rider_id or the row id
    — so the public poll endpoint leaks no rider identity. Capped at ``limit``
    most-recent points (then re-ordered oldest→newest for the trail).
    """
    return db.query(
        "SELECT lat, lng, recorded_at FROM ("
        "  SELECT lat, lng, recorded_at FROM rp_live_position "
        "  WHERE ride_id = %s ORDER BY recorded_at DESC LIMIT %s"
        ") p ORDER BY recorded_at ASC",
        (ride_id, limit),
    )


def insert_position(ride_id, rider_id, lat, lng, recorded_at=None):
    """Append one {lat,lng,recorded_at} breadcrumb for a ride.

    ``recorded_at`` is an optional ISO-8601 string (as the device reports
    it); when omitted the DB stamps NOW(). Owner enforcement is the route job —
    this is the raw insert.
    """
    db.execute(
        "INSERT INTO rp_live_position (ride_id, rider_id, lat, lng, recorded_at) "
        "VALUES (%s, %s, %s, %s, COALESCE(%s::timestamptz, NOW()))",
        (ride_id, rider_id, lat, lng, recorded_at),
    )


# --------------------------------------------------------------------------- #
# Live tracking (rp_live_tracking + rp_live_position telemetry) — the Garmin
# ingestion + multi-rider member map (Mission 1). Distinct `_rp` names so the
# existing anonymous guest surface (insert_position / get_ride_positions) is
# untouched. Every write on rp_live_tracking is SELF-scoped: the functions take
# the SUBJECT (session) rider_id and can only ever read/modify only that rider own
# row — there is no ride-owner parameter, so one rider can never touch another rider
# tracking prefs. The named+telemetry latest-positions query is consumed ONLY by
# the @profile_required member endpoint; the anonymous poll never selects a name.
# --------------------------------------------------------------------------- #
def _coerce_num(value, cast):
    """Best-effort cast to int/float; None on failure (bad telemetry → NULL)."""
    if value is None:
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def get_live_tracking_rp(rider_id):
    """A rider own live-tracking prefs row, or None if never set."""
    return db.query_one(
        "SELECT rider_id, enabled, garmin_session_url, garmin_session_token, "
        "       active_ride_id, updated_at "
        "FROM rp_live_tracking WHERE rider_id = %s",
        (rider_id,),
    )


def upsert_rider_live_tracking_rp(rider_id, enabled):
    """Set the master opt-in flag for the SUBJECT rider, preserving any Garmin
    session. Self-scoped (keyed on the session rider_id). Returns True on success.

    The settings toggle calls this; it must not clobber a per-ride Garmin link the
    rider registered on a ride map (that link lives on active_ride_id)."""
    try:
        db.execute(
            "INSERT INTO rp_live_tracking (rider_id, enabled, updated_at) "
            "VALUES (%s, %s, NOW()) "
            "ON CONFLICT (rider_id) DO UPDATE "
            "SET enabled = EXCLUDED.enabled, updated_at = NOW()",
            (rider_id, bool(enabled)),
        )
        return True
    except Exception:
        return False


def set_ride_garmin_rp(rider_id, ride_id, session_url, session_token):
    """Register a Garmin LiveTrack link for ONE ride and opt the SUBJECT rider in.

    Self-scoped: writes only the session rider own row (rider_id), pointing
    tracking at `ride_id` (active_ride_id) and enabling it. Garmin mints a fresh
    session per activity, so the link is inherently per-ride. Points the cron
    ingests are tagged with this ride, so they only show on that ride map.
    Returns True on success."""
    try:
        db.execute(
            "INSERT INTO rp_live_tracking "
            "    (rider_id, enabled, garmin_session_url, garmin_session_token, "
            "     active_ride_id, updated_at) "
            "VALUES (%s, TRUE, %s, %s, %s, NOW()) "
            "ON CONFLICT (rider_id) DO UPDATE "
            "SET enabled = TRUE, "
            "    garmin_session_url = EXCLUDED.garmin_session_url, "
            "    garmin_session_token = EXCLUDED.garmin_session_token, "
            "    active_ride_id = EXCLUDED.active_ride_id, "
            "    updated_at = NOW()",
            (rider_id, session_url, session_token, ride_id),
        )
        return True
    except Exception:
        return False


def clear_ride_garmin_rp(rider_id, ride_id):
    """Remove the SUBJECT rider Garmin link if it is pointed at `ride_id`.

    Self-scoped and a no-op when the rider active ride is a different one (the
    WHERE clause matches nothing). Leaves the master opt-in flag alone. Returns
    True on success."""
    try:
        db.execute(
            "UPDATE rp_live_tracking "
            "SET garmin_session_url = NULL, garmin_session_token = NULL, "
            "    active_ride_id = NULL, updated_at = NOW() "
            "WHERE rider_id = %s AND active_ride_id = %s",
            (rider_id, ride_id),
        )
        return True
    except Exception:
        return False


def set_active_ride_rp(rider_id, ride_id):
    """Point the SUBJECT rider live-tracking row at ``ride_id``; when this MOVES the
    active ride, clear any registered Garmin session so it cannot be mis-polled.

    Self-scoped (keyed on rider_id). The phone beacon calls this to attach the rider
    to the ride they are streaming to. A Garmin session is registered against the
    then-active ride; the poll cron tags every fetched Garmin point with the current
    active_ride_id. So if a rider linked Garmin for ride A and then beacons ride B,
    keeping the session would let the cron poll the ride A session and attribute its
    points to ride B (cross-ride contamination). Therefore, when the active ride
    actually changes, the session URL/token are nulled; when the active ride is
    unchanged the Garmin link is preserved. A fresh row is created with tracking
    disabled — consent is set separately by the sharing toggle. Returns True on
    success."""
    try:
        db.execute(
            "INSERT INTO rp_live_tracking (rider_id, active_ride_id, updated_at) "
            "VALUES (%s, %s, NOW()) "
            "ON CONFLICT (rider_id) DO UPDATE "
            "SET active_ride_id = EXCLUDED.active_ride_id, "
            "    garmin_session_url = CASE "
            "        WHEN COALESCE(rp_live_tracking.active_ride_id, -1) <> EXCLUDED.active_ride_id "
            "        THEN NULL ELSE rp_live_tracking.garmin_session_url END, "
            "    garmin_session_token = CASE "
            "        WHEN COALESCE(rp_live_tracking.active_ride_id, -1) <> EXCLUDED.active_ride_id "
            "        THEN NULL ELSE rp_live_tracking.garmin_session_token END, "
            "    updated_at = NOW()",
            (rider_id, ride_id),
        )
        return True
    except Exception:
        return False


def get_auto_attach_ride_rp(rider_id):
    """Cold-start auto-attach: deterministically pick the accessible ride a beacon
    should stream to when neither an explicit nor an active ride is set. Returns a
    ride row (id, rider_id, is_public, start_at) or None when nothing is eligible.

    The candidate set is the accessible union — rides the rider owns, PLUS public
    rides the rider already has a stored position on (the concrete signal they
    attached to another rider public ride). A private ride the rider does not own is
    never a candidate, so this can never surface an inaccessible ride; the caller
    still re-gates the pick defensively. Ordering is deterministic: rides the rider
    is already streaming to first, then the ride whose start is nearest to now, then
    the highest id. Reads rp_ride and rp_live_position only (live tracking operates
    on rp_ride, not the calendar tables)."""
    return db.query_one(
        "SELECT r.id, r.rider_id, r.is_public, r.start_at "
        "FROM rp_ride r "
        "WHERE r.rider_id = %s "
        "   OR (r.is_public = TRUE "
        "       AND r.id IN (SELECT ride_id FROM rp_live_position "
        "                    WHERE rider_id = %s)) "
        "ORDER BY "
        "  (r.id IN (SELECT ride_id FROM rp_live_position "
        "            WHERE rider_id = %s)) DESC, "
        "  ABS(EXTRACT(EPOCH FROM (COALESCE(r.start_at, NOW()) - NOW()))) ASC, "
        "  r.id DESC "
        "LIMIT 1",
        (rider_id, rider_id, rider_id),
    )


def get_enabled_live_tracking_rp():
    """All riders opted in WITH a Garmin session pointed at a specific ride.

    The poll cron iterates these and tags ingested points with active_ride_id."""
    return db.query(
        "SELECT rider_id, garmin_session_url, garmin_session_token, active_ride_id "
        "FROM rp_live_tracking "
        "WHERE enabled = TRUE "
        "  AND garmin_session_token IS NOT NULL "
        "  AND active_ride_id IS NOT NULL"
    )


def insert_live_position_rp(rider_id, lat, lng, recorded_at, source, accuracy=None,
                            speed=None, heart_rate=None, power=None, cadence=None,
                            ride_id=None):
    """Insert one telemetry-bearing position point for a rider. Validates/clamps
    coordinates and coerces bad telemetry to NULL.

    `ride_id` tags the point to a specific ride so it only shows on that ride
    member map. `source` records how it arrived ('garmin'). Optional telemetry
    (speed m/s, heart_rate bpm, power W, cadence rpm) is stored when present.
    Returns True on success, False if coordinates are invalid (out of range) or
    the insert fails."""
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

    try:
        db.execute(
            "INSERT INTO rp_live_position "
            "    (rider_id, ride_id, lat, lng, accuracy, recorded_at, source, "
            "     speed, heart_rate, power, cadence) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (rider_id, ride_id, lat, lng, accuracy, recorded_at, source,
             speed, heart_rate, power, cadence),
        )
        return True
    except Exception:
        return False


def get_last_position_recorded_at_rp(rider_id, ride_id):
    """Most recent stored position timestamp for a rider on one ride (or None).

    The poll cron appends only points newer than this, so a re-run inserts nothing
    new (idempotent)."""
    row = db.query_one(
        "SELECT MAX(recorded_at) AS last_at "
        "FROM rp_live_position WHERE rider_id = %s AND ride_id = %s",
        (rider_id, ride_id),
    )
    return row['last_at'] if row else None


def get_live_positions_rp(ride_id, since):
    """Latest position per opted-in rider tagged to a ride, newer than `since`.

    A rider appears purely because they are currently opted in
    (rp_live_tracking.enabled), currently attached to THIS ride
    (t.active_ride_id), and have points tagged to THIS ride (p.ride_id). That
    per-ride attach is the opt-in/consent, so clearing or moving the Garmin link
    drops the rider off the live map even if recent historical points remain.
    Returns rider_id, a display `name` (email local-part — rp_rider carries no
    first/last name), lat/lng, recorded_at, and telemetry
    (speed/heart_rate/power/cadence) + source.

    Consumed ONLY by the @profile_required member endpoint — the anonymous poll
    (get_ride_positions) never selects a name."""
    return db.query(
        "SELECT DISTINCT ON (p.rider_id) "
        "       p.rider_id, "
        "       split_part(r.email, '@', 1) AS name, "
        "       p.lat, p.lng, p.recorded_at, "
        "       p.speed, p.heart_rate, p.power, p.cadence, p.source "
        "FROM rp_live_position p "
        "JOIN rp_rider r ON r.id = p.rider_id "
        "JOIN rp_live_tracking t ON t.rider_id = p.rider_id "
        "WHERE p.ride_id = %s "
        "  AND t.enabled = TRUE "
        "  AND t.active_ride_id = p.ride_id "
        "  AND p.recorded_at >= %s "
        "ORDER BY p.rider_id, p.recorded_at DESC",
        (ride_id, since),
    )


def get_rider_position_history_rp(ride_id, rider_id, since):
    """Position history for one rider on one ride, oldest to newest, for telemetry.

    Selects lat, lng, recorded_at and speed so the shared telemetry engine can
    project the trajectory onto the route and derive moving versus stopped time.
    Scoped to a single rider and ride and bounded by ``since`` (the display window),
    so a poll only reads the recent trail. Consumed ONLY by the member endpoint.
    """
    return db.query(
        "SELECT lat, lng, recorded_at, speed FROM rp_live_position "
        "WHERE ride_id = %s AND rider_id = %s AND recorded_at >= %s "
        "ORDER BY recorded_at ASC",
        (ride_id, rider_id, since),
    )


def purge_old_positions_rp(retention_days=7):
    """Delete position points older than the retention window. Returns the count
    deleted (via cursor.rowcount), or None on failure. Goes through the cursor
    directly because the db.execute helper only surfaces one RETURNING row, not a
    row count."""
    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM rp_live_position "
                "WHERE created_at < NOW() - (%s || ' days')::interval",
                (str(int(retention_days)),),
            )
            deleted = cur.rowcount
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        return None


# --------------------------------------------------------------------------- #
# RUSA brevet cache (rp_rider.rusa_cache) — the rider's scraped RUSA history,
# stored as a JSON-safe array so every dashboard load does not re-scrape.
# --------------------------------------------------------------------------- #
def get_rider_rusa_cache(rider_id):
    """Return the cached RUSA scrape + its fetch time for a rider.

    Yields ``{'rusa_cache': <list|None>, 'rusa_fetched_at': <datetime|None>}``.
    ``rusa_cache`` is decoded by psycopg2 straight to a Python list; a NULL
    (never fetched) comes back as None.
    """
    return db.query_one(
        "SELECT rusa_cache, rusa_fetched_at FROM rp_rider WHERE id = %s",
        (rider_id,),
    )


def update_rider_rusa_cache(rider_id, brevets):
    """Store a fresh RUSA scrape (a JSON-safe list of brevet dicts) and stamp
    the fetch time. Callers pass results already normalized to JSON-safe values
    (dates as ISO strings)."""
    db.execute(
        "UPDATE rp_rider SET rusa_cache = %s, rusa_fetched_at = NOW() WHERE id = %s",
        (Json(brevets), rider_id),
    )


# --------------------------------------------------------------------------- #
# Club roster (rp_rider) — read-only, club-scoped community surfaces (directory,
# leaderboard, season rosters, public rider profile). Every query here is
# parameterized by the viewer club_id, so no rider outside that club is ever
# returned; the cached RUSA history rides along, letting career numbers reuse the
# same engine the self-profile page uses instead of a second computation.
# --------------------------------------------------------------------------- #
def get_club_riders_with_rusa(club_id):
    """Completed-profile riders in one club, each with the cached RUSA history.

    Club-scoped by the club_id bind, so a caller can only ever see members of the
    club it passes. Rows are ordered by email for a deterministic list; the caller
    derives the public display name and career numbers and drops the raw email
    before rendering, so no full address or google id reaches another rider.
    """
    return db.query(
        "SELECT id, email, rusa_id, rusa_cache "
        "FROM rp_rider "
        "WHERE club_id = %s AND profile_completed = TRUE "
        "ORDER BY email ASC",
        (club_id,),
    )


def get_club_rider(club_id, rider_id):
    """One club-scoped rider by primary key, with the cached RUSA history.

    Keyed on the unique rider id, never the RUSA id: BrevetHub allows two riders to
    claim the same RUSA id (soft-flagged, not rejected), so a RUSA id can be
    ambiguous within a club. The row is returned only when it belongs to the given
    club, so a viewer can never resolve a rider outside their own club (the
    public-profile access gate). Returns None when the club has no such
    completed-profile rider.
    """
    return db.query_one(
        "SELECT id, email, rusa_id, club_id, created_at, rusa_cache, "
        "       eddington_km, eddington_miles "
        "FROM rp_rider "
        "WHERE club_id = %s AND id = %s AND profile_completed = TRUE",
        (club_id, rider_id),
    )


# --------------------------------------------------------------------------- #
# Strava connection (rp_strava_connection) — per-rider OAuth link + cached
# activity summary.
#
# This layer owns the epoch<->TIMESTAMPTZ conversion for expires_at: the shared
# Strava code is epoch-native (Unix integers, as Strava returns them), while the
# column is TIMESTAMPTZ. Writes convert with to_timestamp(%s); the getter reads
# the tz-aware datetimes back and returns them as epoch floats, so every consumer
# (staleness / refresh decisions) stays epoch-native and comparable to time.time().
# --------------------------------------------------------------------------- #
def get_strava_connection(rider_id):
    """Return the rider's Strava connection, or None.

    ``expires_at`` and ``stats_fetched_at`` are returned as epoch floats (never
    bare datetimes) so callers compare them directly against ``time.time()``.
    """
    row = db.query_one(
        "SELECT id, rider_id, strava_athlete_id, access_token, refresh_token, "
        "       expires_at, scope, stats_cache, stats_fetched_at, created_at "
        "FROM rp_strava_connection WHERE rider_id = %s",
        (rider_id,),
    )
    if row is None:
        return None
    row = dict(row)
    row['expires_at'] = row['expires_at'].timestamp() if row.get('expires_at') else None
    row['stats_fetched_at'] = (
        row['stats_fetched_at'].timestamp() if row.get('stats_fetched_at') else None
    )
    return row


def get_strava_connections_for_eddington():
    """Every rider Strava connection, for the owner-context Eddington refresh cron.

    Returns the same per-connection shape as get_strava_connection (expires_at as an
    epoch float so the token-refresh decision stays numeric), one row per connected
    rider ordered by rider_id. The cron recomputes each rider OWN Eddington with
    their OWN token; it reads only rp_strava_connection here and never a parent-app
    table.
    """
    rows = db.query(
        "SELECT id, rider_id, strava_athlete_id, access_token, refresh_token, "
        "       expires_at, scope, stats_cache, stats_fetched_at, created_at "
        "FROM rp_strava_connection ORDER BY rider_id ASC",
    )
    result = []
    for row in rows or []:
        row = dict(row)
        row['expires_at'] = row['expires_at'].timestamp() if row.get('expires_at') else None
        row['stats_fetched_at'] = (
            row['stats_fetched_at'].timestamp() if row.get('stats_fetched_at') else None
        )
        result.append(row)
    return result


def upsert_strava_connection(rider_id, *, strava_athlete_id, access_token,
                             refresh_token, expires_at, scope=None):
    """Create or replace the rider's Strava connection.

    ``expires_at`` is a Unix epoch integer (as Strava returns it) and is written
    to the TIMESTAMPTZ column via ``to_timestamp(%s)``.
    """
    if get_strava_connection(rider_id):
        db.execute(
            "UPDATE rp_strava_connection "
            "SET strava_athlete_id = %s, access_token = %s, refresh_token = %s, "
            "    expires_at = to_timestamp(%s), scope = %s "
            "WHERE rider_id = %s",
            (strava_athlete_id, access_token, refresh_token, expires_at, scope, rider_id),
        )
    else:
        db.execute(
            "INSERT INTO rp_strava_connection "
            "  (rider_id, strava_athlete_id, access_token, refresh_token, expires_at, scope) "
            "VALUES (%s, %s, %s, %s, to_timestamp(%s), %s)",
            (rider_id, strava_athlete_id, access_token, refresh_token, expires_at, scope),
        )


def update_strava_tokens(rider_id, *, access_token, refresh_token, expires_at):
    """Persist refreshed Strava tokens. ``expires_at`` is a Unix epoch integer,
    stored via ``to_timestamp(%s)``."""
    db.execute(
        "UPDATE rp_strava_connection "
        "SET access_token = %s, refresh_token = %s, expires_at = to_timestamp(%s) "
        "WHERE rider_id = %s",
        (access_token, refresh_token, expires_at, rider_id),
    )


def update_strava_stats(rider_id, stats):
    """Cache the computed per-rider activity summary (a JSON-safe dict) and stamp
    the fetch time."""
    db.execute(
        "UPDATE rp_strava_connection "
        "SET stats_cache = %s, stats_fetched_at = NOW() "
        "WHERE rider_id = %s",
        (Json(stats), rider_id),
    )


def delete_strava_connection(rider_id):
    """Remove the rider's Strava connection (disconnect)."""
    db.execute(
        "DELETE FROM rp_strava_connection WHERE rider_id = %s",
        (rider_id,),
    )


# --------------------------------------------------------------------------- #
# Strava OAuth broker (rp_strava_broker_state + rp_strava_broker_handoff) —
# BrevetHub serving a Strava connect on behalf of Team Asha. BrevetHub is the
# sole writer here; Team Asha only reads+deletes the handoff row (see the note in
# migration 035). Both tables are rp_-prefixed, so the rp-only isolation invariant
# holds.
# --------------------------------------------------------------------------- #
def claim_broker_state(nonce, *, state_ttl_seconds=600):
    """Phase 1 (at /connect): atomically claim a broker-state nonce for single use.

    Returns the claim row on first use, or ``None`` if the nonce was already
    claimed — the durable replay guard that makes a signed state single-use across
    stateless serverless invocations (an HMAC + TTL check alone cannot). The
    caller hard-rejects the connect when this returns ``None``. The claim is left
    unconsumed (``consumed_at IS NULL``) until :func:`consume_broker_state` marks
    it at the matching /callback.
    """
    return db.execute(
        "INSERT INTO rp_strava_broker_state (nonce, state_expires_at) "
        "VALUES (%s, NOW() + make_interval(secs => %s)) "
        "ON CONFLICT (nonce) DO NOTHING "
        "RETURNING nonce",
        (nonce, state_ttl_seconds),
        returning=True,
    )


def consume_broker_state(nonce):
    """Phase 2 (at /callback): atomically consume a previously-claimed nonce.

    Returns the row only if the nonce was claimed at /connect AND has not already
    been consumed by an earlier callback. ``None`` means the state either skipped
    /connect entirely (a direct-to-Strava bypass that never passed the claim) or is
    being replayed through /callback — either way the caller hard-rejects before any
    token exchange or handoff. This is what makes the single-use guarantee hold end
    to end, not just at the first hop.
    """
    return db.execute(
        "UPDATE rp_strava_broker_state SET consumed_at = NOW() "
        "WHERE nonce = %s AND consumed_at IS NULL "
        "RETURNING nonce",
        (nonce,),
        returning=True,
    )


def create_broker_handoff(*, ta_rider_id, strava_athlete_id, access_token,
                          refresh_token, strava_token_expires_at, scope=None,
                          handoff_ttl_seconds=300):
    """Insert a one-time Strava-token handoff row and return its opaque code.

    ``strava_token_expires_at`` is a Unix epoch integer (Strava's access-token
    lifetime) stored via ``to_timestamp()``; the separate ``handoff_expires_at``
    is the short single-use TTL Team Asha's consume gate reads. The code is a
    high-entropy random token — the only thing put in the return URL, never a
    Strava token.
    """
    code = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO rp_strava_broker_handoff "
        "  (code, ta_rider_id, strava_athlete_id, access_token, refresh_token, "
        "   strava_token_expires_at, scope, handoff_expires_at) "
        "VALUES (%s, %s, %s, %s, %s, to_timestamp(%s), %s, "
        "        NOW() + make_interval(secs => %s))",
        (code, ta_rider_id, strava_athlete_id, access_token, refresh_token,
         strava_token_expires_at, scope, handoff_ttl_seconds),
    )
    return code


# --------------------------------------------------------------------------- #
# Brevet calendar (rp_brevet_event) — a cache of upcoming RUSA brevets parsed by
# shared/rusa_calendar.py, plus the rider-participation table (rp_event_signup).
#
# The national feed carries no start location/time, so those columns are NULL for
# national-feed events; the calendar renders an honest placeholder and never
# fabricates them. Every query below targets an rp_* table only.
# --------------------------------------------------------------------------- #
def get_events_cache_freshness():
    """The newest ``scraped_at`` across cached events, or None when empty.

    The calendar route uses ``None`` to identify the first-deploy seed path. A
    present timestamp, even an old one, is served from cache; age only controls the
    soft stale banner.
    """
    row = db.query_one("SELECT MAX(scraped_at) AS latest FROM rp_brevet_event")
    return row['latest'] if row else None


def get_upcoming_events(state=None, limit=200):
    """Upcoming brevets (date >= today), soonest first, with an aggregate signup count.

    ``state`` optionally narrows to one US state by matching the RUSA region
    label ``"<STATE>: ..."`` prefix — an honest, documented narrowing a generic
    multi-club app can do without the Team Asha hardcoded region->club map. None
    returns every upcoming brevet (the general RUSA calendar).

    ``signup_count`` is the number of riders who are actively participating
    (interested / maybe / going) — an AGGREGATE only, so the guest calendar can show
    interest without exposing any rider identity. WITHDRAW rows are excluded so a
    withdrawn rider drops off the count. The count comes from a pre-aggregated
    sub-select LEFT-joined on the event id, so an event with zero sign-ups still
    returns (coalesced to 0) — both the sub-select and the outer query touch only
    rp_* tables (rp_event_signup / rp_brevet_event).
    """
    like = (state + ': %') if state else None
    return db.query(
        "SELECT e.id, e.rusa_route_id, e.name, e.date, e.distance_km, e.region, "
        "       e.ride_type, e.elevation_ft, e.rwgps_url, e.start_location, "
        "       e.start_time, e.time_limit_hours, "
        "       COALESCE(sc.signup_count, 0) AS signup_count "
        "FROM rp_brevet_event e "
        "LEFT JOIN ("
        "  SELECT event_id, COUNT(*) AS signup_count "
        "  FROM rp_event_signup WHERE status IN (%s, %s, %s) GROUP BY event_id"
        ") sc ON sc.event_id = e.id "
        "WHERE e.date >= CURRENT_DATE AND (%s::text IS NULL OR e.region ILIKE %s) "
        "ORDER BY e.date ASC, e.distance_km ASC LIMIT %s",
        (RideStatus.INTERESTED.value, RideStatus.MAYBE.value, RideStatus.GOING.value,
         state, like, limit),
    )


def get_brevet_event(event_id):
    """A single cached brevet by id (the sign-up existence gate)."""
    return db.query_one(
        "SELECT id, name, date, distance_km, region FROM rp_brevet_event WHERE id = %s",
        (event_id,),
    )


def upsert_brevet_event(event):
    """Insert or refresh one cached brevet, keyed on (date, name, distance_km).

    A single atomic INSERT ... ON CONFLICT ... DO UPDATE (not SELECT-then-INSERT):
    the natural key has a UNIQUE constraint, so two concurrent /calendar refreshes
    past the TTL can both miss the row — the conflict target makes the loser refresh
    the row in place instead of raising a unique-violation (which would 500 the
    calendar). COALESCE(EXCLUDED.col, existing) keeps the Team Asha upsert semantics
    so a sparser repeat scrape never wipes richer data already cached. ``event`` is a
    dict from shared.rusa_calendar.get_rusa_events.

    NOTE: the SQL literal is split right at ``DO UPDATE`` / ``SET`` on purpose — the
    rp-only scanner (test_rp_only) captures the identifier after an ``UPDATE``
    keyword, and keeping ``UPDATE`` at a literal boundary prevents it from reading
    ``SET`` as a bogus (non-rp_) table name.
    """
    db.execute(
        "INSERT INTO rp_brevet_event "
        "  (rusa_route_id, name, date, distance_km, region, ride_type, "
        "   elevation_ft, rwgps_url, start_location, start_time, time_limit_hours) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (date, name, distance_km) DO UPDATE "
        "SET rusa_route_id = COALESCE(EXCLUDED.rusa_route_id, rp_brevet_event.rusa_route_id), "
        "    region = COALESCE(EXCLUDED.region, rp_brevet_event.region), "
        "    ride_type = COALESCE(EXCLUDED.ride_type, rp_brevet_event.ride_type), "
        "    elevation_ft = COALESCE(EXCLUDED.elevation_ft, rp_brevet_event.elevation_ft), "
        "    rwgps_url = COALESCE(EXCLUDED.rwgps_url, rp_brevet_event.rwgps_url), "
        "    start_location = COALESCE(EXCLUDED.start_location, rp_brevet_event.start_location), "
        "    start_time = COALESCE(EXCLUDED.start_time, rp_brevet_event.start_time), "
        "    time_limit_hours = COALESCE(EXCLUDED.time_limit_hours, rp_brevet_event.time_limit_hours), "
        "    scraped_at = NOW()",
        (event.get('route_id'), event['name'], event['date'], event['distance_km'],
         event.get('region'), event.get('ride_type'), event.get('elevation_ft'),
         event.get('rwgps_url'), event.get('start_location'),
         event.get('start_time'), event.get('time_limit_hours')),
    )


def get_rider_signup_statuses(rider_id):
    """(event_id, status) for every sign-up belonging to THIS rider.

    Used to annotate the calendar with the signed-in rider own status per event —
    never a different rider, so the guest/other-rider surface stays PII-free.
    """
    return db.query(
        "SELECT event_id, status FROM rp_event_signup WHERE rider_id = %s",
        (rider_id,),
    )


def set_rider_signup(rider_id, event_id, status):
    """Create or transition a rider pre-ride sign-up on an event (one row per pair).

    A single atomic INSERT ... ON CONFLICT ... DO UPDATE keyed on the
    UNIQUE(event_id, rider_id) constraint: the calendar UI POSTs several status
    buttons to this same endpoint, so rapid/duplicate requests for one rider-event
    pair can race — the conflict target makes them transition the status cleanly
    (last write wins) instead of one hitting a unique-violation and returning a 500.

    Guarded so a pre-ride intent can NEVER clobber a post-ride result: the guarded
    write carries a WHERE that skips any existing finished / dnf / dns / otl row, and
    RETURNING lets the caller tell an applied write apart from a blocked one. Returns
    a sentinel the route maps to an HTTP code:
      applied      a new or pre-ride row was written -> 200
      has_result   an existing post-ride result was left intact -> 409
    A fresh INSERT and a pre-ride write both yield an id; a blocked write yields none.
    Without this guard a rider could POST interested / maybe / going onto a past
    finished ride and erase the result (and, via interested / maybe, block the
    auto-finalize sweep so it can never restore the result).

    (The literal is split at ``DO UPDATE`` / ``SET`` for the same rp-only-scanner
    reason documented on :func:`upsert_brevet_event`.)
    """
    row = db.execute(
        "INSERT INTO rp_event_signup (rider_id, event_id, status) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (event_id, rider_id) DO UPDATE "
        "SET status = EXCLUDED.status, updated_at = NOW() "
        "WHERE rp_event_signup.status NOT IN (%s, %s, %s, %s) "
        "RETURNING id",
        (rider_id, event_id, status,
         RideStatus.FINISHED.value, RideStatus.DNF.value,
         RideStatus.DNS.value, RideStatus.OTL.value),
        returning=True,
    )
    # A None row means the conflict hit an existing post-ride result the WHERE
    # excluded (a fresh INSERT or a pre-ride write both yield an id row).
    return 'applied' if row is not None else 'has_result'


def withdraw_rider_signup(rider_id, event_id):
    """Transition an EXISTING pre-ride sign-up to withdraw; return a route sentinel.

    Read-then-guarded-update, mirroring the parent web app withdraw guard and the
    sibling :func:`clear_rider_signup`:
      not_found    no sign-up for this rider on this event -> 404
      has_result   the current status is a post-ride result, left intact -> 409
      withdrawn    a pre-ride row was transitioned to withdraw -> 200
    A withdraw with no prior sign-up changes nothing, so a guest cannot manufacture a
    withdraw row; a withdraw over a finished / dnf / dns / otl result is refused so it
    cannot erase the result. Scoped by rider_id throughout, so a rider can only ever
    withdraw their OWN row; the guarded write re-asserts the pre-ride predicate so a
    concurrent transition cannot slip a post-ride row through.
    """
    current = db.query_one(
        "SELECT status FROM rp_event_signup WHERE rider_id = %s AND event_id = %s",
        (rider_id, event_id),
    )
    if not current:
        return 'not_found'
    if RideStatus.is_post_ride(RideStatus.normalize(current['status'])):
        return 'has_result'
    db.execute(
        "UPDATE rp_event_signup "
        "SET status = %s, updated_at = NOW() "
        "WHERE rider_id = %s AND event_id = %s "
        "  AND status NOT IN (%s, %s, %s, %s)",
        (RideStatus.WITHDRAW.value, rider_id, event_id,
         RideStatus.FINISHED.value, RideStatus.DNF.value,
         RideStatus.DNS.value, RideStatus.OTL.value),
    )
    return 'withdrawn'


def clear_rider_signup(rider_id, event_id):
    """Remove a rider OWN pre-ride sign-up; return a sentinel the route maps to HTTP.

    Read-then-guarded-delete, mirroring the parent web app remove_signup:
      not_found  no sign-up for this rider on this event -> 404
      post_ride  the current status may not be cleared (a result, or withdraw) -> 400
      deleted    a pre-ride row (interested / maybe / going) was removed -> 200
    Scoped by rider_id throughout, so a rider can only ever clear their OWN row; the
    DELETE re-asserts the pre-ride predicate so a concurrent transition cannot slip a
    non-clearable row through.
    """
    row = db.query_one(
        "SELECT status FROM rp_event_signup WHERE rider_id = %s AND event_id = %s",
        (rider_id, event_id),
    )
    if not row:
        return 'not_found'
    if not RideStatus.can_remove(RideStatus.normalize(row['status'])):
        return 'post_ride'
    db.execute(
        "DELETE FROM rp_event_signup "
        "WHERE rider_id = %s AND event_id = %s AND status IN (%s, %s, %s)",
        (rider_id, event_id, RideStatus.INTERESTED.value, RideStatus.MAYBE.value,
         RideStatus.GOING.value),
    )
    return 'deleted'


def set_signup_result(rider_id, event_id, status):
    """Set a post-ride result on a rider OWN PAST sign-up (status-only).

    Read, then a guarded write on a three-part predicate, all three required:
    ownership (rider_id bind — the tenant-safety gate), a past event date, and a
    current status eligible for a result (going, or an existing post-ride result).
    Returns a (sentinel, finish_time) tuple the route maps to an HTTP code:
      (not_found, None)     no sign-up for this rider on this event -> 404
      (not_past, None)      the event date has not passed -> 409
      (ineligible, None)    a non-convertible pre-ride current status -> 409
      (ok, <time-or-None>)  the result was written -> 200

    finish_time is NEVER given a rider value here — the RUSA-sync cron is the sole
    real writer. A correction to a non-successful result (dnf / dns / otl) clears any
    stale finish_time to NULL as a status side effect; a correction to or among
    finished preserves an existing RUSA value. The guarded write re-asserts the
    eligibility predicate so a concurrent transition cannot slip a non-eligible row
    through.
    """
    row = db.query_one(
        "SELECT s.status, s.finish_time, (e.date < CURRENT_DATE) AS is_past "
        "FROM rp_event_signup s "
        "JOIN rp_brevet_event e ON e.id = s.event_id "
        "WHERE s.rider_id = %s AND s.event_id = %s",
        (rider_id, event_id),
    )
    if not row:
        return ('not_found', None)
    if not row['is_past']:
        return ('not_past', None)
    current = RideStatus.normalize(row['status'])
    if not (current == RideStatus.GOING or RideStatus.is_post_ride(current)):
        return ('ineligible', None)

    new_status = RideStatus.normalize(status)
    # The eligibility set re-asserted by the guarded write: a going row or any
    # post-ride result. Kept identical to the read-time predicate above.
    eligible = (RideStatus.GOING.value, RideStatus.FINISHED.value,
                RideStatus.DNF.value, RideStatus.DNS.value, RideStatus.OTL.value)
    if RideStatus.is_successful(new_status):
        # Preserve any existing RUSA finish time; only flip the status.
        updated = db.execute(
            "UPDATE rp_event_signup "
            "SET status = %s, updated_at = NOW() "
            "WHERE rider_id = %s AND event_id = %s "
            "  AND status IN (%s, %s, %s, %s, %s) "
            "RETURNING finish_time",
            (new_status.value, rider_id, event_id) + eligible,
            returning=True,
        )
    else:
        # A non-finish has no official time: clear any stale finish_time to NULL.
        updated = db.execute(
            "UPDATE rp_event_signup "
            "SET status = %s, finish_time = NULL, updated_at = NOW() "
            "WHERE rider_id = %s AND event_id = %s "
            "  AND status IN (%s, %s, %s, %s, %s) "
            "RETURNING finish_time",
            (new_status.value, rider_id, event_id) + eligible,
            returning=True,
        )
    if updated is None:
        # A concurrent transition moved the row out of an eligible status.
        return ('ineligible', None)
    return ('ok', updated.get('finish_time'))


def auto_finalize_past_signups():
    """Promote every past-date going sign-up to finished; return the count changed.

    Tenant-agnostic: keyed on the event date and the going status only, so it needs
    no club scoping. Mirrors the parent web app auto-finalize. ONLY a going row on a
    past-date event is promoted (interested / maybe / withdraw and any future row are
    untouched, and a row already resolved is left as-is). The CTE returns the affected
    ids so db.execute can report a COUNT (it yields the first row, not a rowcount).
    """
    row = db.execute(
        "WITH rp_finalized AS ("
        "  UPDATE rp_event_signup "
        "  SET status = %s, updated_at = NOW() "
        "  WHERE status = %s "
        "    AND event_id IN (SELECT id FROM rp_brevet_event WHERE date < CURRENT_DATE) "
        "  RETURNING id"
        ") SELECT COUNT(*) AS n FROM rp_finalized",
        (RideStatus.FINISHED.value, RideStatus.GOING.value),
        returning=True,
    )
    return row['n'] if row else 0


def get_event_going_riders(event_id):
    """The pre-ride roster for a brevet plan page — riders who are interested / maybe
    / going, exposed as EMAIL LOCAL-PART ONLY.

    Guest-safety: the plan page is public, so this must never leak a full email address,
    google_id, or rider_id. Only ``split_part(email, '@', 1)`` (the part before the '@')
    and the pre-ride status are selected — the same local-part-only idiom the live map
    uses. Ordered going-first, then interested / maybe, then by local-part. rp_* only.
    """
    return db.query(
        "SELECT split_part(r.email, '@', 1) AS name, s.status "
        "FROM rp_event_signup s "
        "JOIN rp_rider r ON r.id = s.rider_id "
        "WHERE s.event_id = %s AND s.status IN (%s, %s, %s) "
        "ORDER BY CASE s.status WHEN %s THEN 0 WHEN %s THEN 1 ELSE 2 END, name ASC",
        (event_id, RideStatus.GOING.value, RideStatus.INTERESTED.value,
         RideStatus.MAYBE.value, RideStatus.GOING.value, RideStatus.INTERESTED.value),
    )


def get_rider_signups(rider_id):
    """The active upcoming sign-ups (interested / maybe / going) for a rider, linked
    to the event, soonest first — for the dashboard "My upcoming sign-ups" section.
    WITHDRAW rows are excluded so a withdrawn brevet drops off the list."""
    return db.query(
        "SELECT s.event_id, s.status, e.name, e.date, e.distance_km, e.region, "
        "       e.start_location, e.start_time "
        "FROM rp_event_signup s "
        "JOIN rp_brevet_event e ON e.id = s.event_id "
        "WHERE s.rider_id = %s AND s.status IN (%s, %s, %s) AND e.date >= CURRENT_DATE "
        "ORDER BY e.date ASC, e.distance_km ASC",
        (rider_id, RideStatus.INTERESTED.value, RideStatus.MAYBE.value,
         RideStatus.GOING.value),
    )


def get_rider_past_results(rider_id):
    """Past-event results (finished / dnf / dns / otl) for one rider, most recent
    first, for the dashboard "My past results" card. Linked to the event for name /
    date / distance; carries the official finish_time (NULL until the RUSA-sync cron
    fills it). rp_ tables only, rider_id-scoped."""
    return db.query(
        "SELECT s.event_id, s.status, s.finish_time, "
        "       e.name, e.date, e.distance_km, e.region "
        "FROM rp_event_signup s "
        "JOIN rp_brevet_event e ON e.id = s.event_id "
        "WHERE s.rider_id = %s AND s.status IN (%s, %s, %s, %s) "
        "  AND e.date < CURRENT_DATE "
        "ORDER BY e.date DESC, e.distance_km DESC",
        (rider_id, RideStatus.FINISHED.value, RideStatus.DNF.value,
         RideStatus.DNS.value, RideStatus.OTL.value),
    )


def get_signups_needing_finish_time():
    """Finished sign-ups still missing an official finish_time, for the RUSA sync.

    One row per finished rp_event_signup whose rider has a rusa_id and whose
    finish_time is NULL or blank, carrying the event date + distance the matcher
    needs and the rider rusa_id + rusa_cache (so the cron reuses the cached RUSA
    history before any live fetch). Ordered by rider so the cron can batch per rider.
    Touches only rp_event_signup / rp_rider / rp_brevet_event; scoped per rider
    downstream by the cron.
    """
    return db.query(
        "SELECT s.id, s.rider_id, s.event_id, "
        "       r.rusa_id, r.rusa_cache, "
        "       e.date, e.distance_km, e.name "
        "FROM rp_event_signup s "
        "JOIN rp_rider r ON r.id = s.rider_id "
        "JOIN rp_brevet_event e ON e.id = s.event_id "
        "WHERE s.status = %s "
        "  AND (s.finish_time IS NULL OR s.finish_time = '') "
        "  AND r.rusa_id IS NOT NULL "
        "ORDER BY r.id, e.date",
        (RideStatus.FINISHED.value,),
    )


def set_signup_finish_time(signup_id, finish_time):
    """Write an official RUSA finish_time onto one finished sign-up (by row id).

    The SOLE real-value writer of finish_time — the self-service result endpoint only
    ever clears it. Re-asserts status = finished AND a still-empty finish_time, so a
    row that left finished, or was already filled, is never overwritten. Returns True
    when a row changed (RETURNING id, since db.execute yields the first row not a
    rowcount). rp_ tables only.
    """
    row = db.execute(
        "UPDATE rp_event_signup "
        "SET finish_time = %s, updated_at = NOW() "
        "WHERE id = %s AND status = %s "
        "  AND (finish_time IS NULL OR finish_time = '') "
        "RETURNING id",
        (finish_time, signup_id, RideStatus.FINISHED.value),
        returning=True,
    )
    return row is not None


# --------------------------------------------------------------------------- #
# Brevet weather cache (rp_brevet_weather) — one raw Open-Meteo point forecast per
# (event, date), warmed OFF the request path by the weather cron and READ (never
# fetched) by the calendar. Every query below targets an rp_* table only.
# --------------------------------------------------------------------------- #
def get_weather_forecast_targets(horizon_days=16):
    """Near-term upcoming brevets the weather cron should fetch a forecast for.

    Returns ``[{id, date, region}, ...]`` for events whose date is between today
    and ``today + horizon_days`` (Open-Meteo's forecast horizon) AND that have a
    non-NULL region label (so the cron can resolve an approximate start coordinate).
    Events further out — or without a region — are skipped: there is nothing honest
    to forecast, so no cache row is created and the calendar shows the "not
    available yet" state. Touches only rp_brevet_event.
    """
    return db.query(
        "SELECT id, date, region FROM rp_brevet_event "
        "WHERE date >= CURRENT_DATE "
        "  AND date <= CURRENT_DATE + make_interval(days => %s) "
        "  AND region IS NOT NULL "
        "ORDER BY date ASC",
        (horizon_days,),
    )


def upsert_brevet_weather(event_id, forecast_date, weather_data):
    """Insert or refresh one cached point forecast, keyed on (event_id, forecast_date).

    A single atomic upsert on the UNIQUE(event_id, forecast_date) constraint, so a
    repeated cron run refreshes the row in place (idempotent) instead of raising a
    unique-violation. ``weather_data``
    is the raw Open-Meteo JSON dict, JSON-adapted with psycopg2's ``Json``. Only ever
    called by the cron with a successful fetch, so a transient failure never overwrites
    a last-good row.

    (The literal is split at ``DO UPDATE`` / ``SET`` for the same rp-only-scanner
    reason documented on :func:`upsert_brevet_event`.)
    """
    db.execute(
        "INSERT INTO rp_brevet_weather (event_id, forecast_date, weather_data) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (event_id, forecast_date) DO UPDATE "
        "SET weather_data = EXCLUDED.weather_data, fetched_at = NOW()",
        (event_id, forecast_date, Json(weather_data)),
    )


def get_brevet_weather_for_events(event_ids):
    """Cached forecasts for a list of event ids, as ``{event_id: {weather_data,
    forecast_date, fetched_at}}``.

    The calendar's cache-read-only lookup: one query for every event on the page,
    then the route summarizes each raw payload in-process. Returns ``{}`` immediately
    for an empty id list (no query, no error). Touches only rp_brevet_weather.
    """
    if not event_ids:
        return {}
    rows = db.query(
        "SELECT event_id, forecast_date, weather_data, fetched_at "
        "FROM rp_brevet_weather WHERE event_id = ANY(%s)",
        (list(event_ids),),
    )
    return {row['event_id']: {
        'weather_data': row['weather_data'],
        'forecast_date': row['forecast_date'],
        'fetched_at': row['fetched_at'],
    } for row in rows}


# --------------------------------------------------------------------------- #
# Brevet pacing plans (rp_brevet_plan) — a rider's saved target speed / finish
# time per cached brevet, plus the server-computed pacing schedule. The pacing
# math is the reused shared/pacing.py engine; this layer only persists the
# rider's inputs and the computed schedule. Every query targets an rp_* table.
# --------------------------------------------------------------------------- #
def get_brevet_event_full(event_id):
    """A single cached brevet by id including ``time_limit_hours`` — the row the
    pacing planner needs (the sign-up gate's :func:`get_brevet_event` omits it).

    Returns None for an unknown event so the plan route can 404. ``club_id`` is
    included so the admin plan-generation gate can enforce that a club owner only
    generates plans for events belonging to the club they own (NULL for
    national-feed events, which stay first-owner-wins claimable). Touches only
    rp_brevet_event.
    """
    return db.query_one(
        "SELECT id, rusa_route_id, name, date, distance_km, region, ride_type, "
        "       elevation_ft, rwgps_url, start_location, start_time, time_limit_hours, "
        "       club_id "
        "FROM rp_brevet_event WHERE id = %s",
        (event_id,),
    )


def get_rider_brevet_plan(rider_id, event_id):
    """The saved pacing plan for a rider's brevet, or None. One row per pair.

    Widened for the Strategies tab to also return ``strategy_pace`` (the chosen pace
    card id, NULL until one is picked) and ``is_public`` (the community share flag), so
    the render context can show the saved/shared state without a second query.
    """
    return db.query_one(
        "SELECT rider_id, event_id, target_speed_kmh, target_finish_min, plan_data, "
        "       strategy_pace, is_public "
        "FROM rp_brevet_plan WHERE rider_id = %s AND event_id = %s",
        (rider_id, event_id),
    )


def upsert_rider_brevet_plan(rider_id, event_id, *, target_speed_kmh=None,
                             target_finish_min=None, plan_data=None):
    """Create or replace the rider's saved pacing plan for a brevet.

    A single atomic INSERT ... ON CONFLICT ... DO UPDATE keyed on the
    UNIQUE(rider_id, event_id) constraint, so a rider re-saving with a new target
    transitions the row in place (last write wins) instead of hitting a unique
    violation. ``plan_data`` is the SERVER-computed schedule (never a client-posted
    one), JSON-adapted with psycopg2's ``Json``.

    (The literal is split at ``DO UPDATE`` / ``SET`` for the same rp-only-scanner
    reason documented on :func:`upsert_brevet_event`.)
    """
    db.execute(
        "INSERT INTO rp_brevet_plan "
        "  (rider_id, event_id, target_speed_kmh, target_finish_min, plan_data) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (rider_id, event_id) DO UPDATE "
        "SET target_speed_kmh = EXCLUDED.target_speed_kmh, "
        "    target_finish_min = EXCLUDED.target_finish_min, "
        "    plan_data = EXCLUDED.plan_data, updated_at = NOW()",
        (rider_id, event_id, target_speed_kmh, target_finish_min, Json(plan_data)),
    )


def upsert_rider_brevet_strategy(rider_id, event_id, pace_id, is_public=None):
    """Save the selected rider pace card + community share flag for a brevet.

    Writes only ``strategy_pace`` (comfort | standard | push) and the tri-state
    ``is_public`` onto the existing ``(rider_id, event_id)`` row, leaving any saved
    ``target_speed_kmh`` / ``plan_data`` untouched. A single atomic upsert on the
    UNIQUE(rider_id, event_id) constraint, so a re-pick transitions the row in place
    (last write wins) instead of raising a unique-violation.

    Tri-state ``is_public``: None means preserve the existing flag (COALESCE keeps the
    stored value on update, and a brand-new row falls back to FALSE = private, matching
    the NOT NULL DEFAULT); True publishes; False unpublishes. The upsert RETURNS the
    ``is_public`` it actually persisted, so a re-pick that omits the flag reports the
    preserved value rather than a guessed literal and the client share toggle stays in
    sync. Returns that resolved boolean.

    (The literal is split at ``DO UPDATE`` / ``SET`` for the same rp-only-scanner reason
    documented on :func:`upsert_brevet_event`.)
    """
    row = db.execute(
        "INSERT INTO rp_brevet_plan "
        "  (rider_id, event_id, strategy_pace, is_public) "
        "VALUES (%s, %s, %s, COALESCE(%s, FALSE)) "
        "ON CONFLICT (rider_id, event_id) DO UPDATE "
        "SET strategy_pace = EXCLUDED.strategy_pace, "
        "    is_public = COALESCE(%s, rp_brevet_plan.is_public), "
        "    updated_at = NOW() "
        "RETURNING is_public",
        (rider_id, event_id, pace_id, is_public, is_public),
        returning=True,
    )
    return bool(row['is_public']) if row else False


def get_public_strategies(event_id, club_id):
    """Other publicly-shared saved pace strategies for a brevet, scoped to one
    club and exposed as EMAIL LOCAL-PART ONLY.

    Guest-safety mirrors :func:`get_event_going_riders`: the plan page is public, so this
    must never leak a full email address, google_id, or rider_id. Only the local-part of
    the email (via split_part on the at-sign) and the chosen pace are selected. Scoped by
    ``club_id`` so a viewer only ever sees co-club strategies; a NULL scope (a guest, or a
    rider with no club) returns an empty list without touching the DB. rp_* only.
    """
    if club_id is None:
        return []
    return db.query(
        "SELECT split_part(r.email, '@', 1) AS name, p.strategy_pace "
        "FROM rp_brevet_plan p "
        "JOIN rp_rider r ON r.id = p.rider_id "
        "WHERE p.event_id = %s AND p.is_public = TRUE "
        "  AND p.strategy_pace IS NOT NULL AND r.club_id = %s "
        "ORDER BY name ASC",
        (event_id, club_id),
    )


# --------------------------------------------------------------------------- #
# Per-ride analysis (rp_ride_analysis) — a rider's cached, computed breakdown of
# one of their OWN Strava activities (M9). The heavy Strava stream fetch + the
# reused shared/strava_analysis.py engine run only on an explicit rider action
# (POST /analysis/<id>/compute); every read is served from this cache. Both the
# analysis (JSONB) and the compressed raw streams (BYTEA) live here, keyed per
# (rider, activity). Every query targets an rp_* table only and is scoped by
# rider_id, so a rider only ever reads their own analysis.
# --------------------------------------------------------------------------- #
def get_ride_analysis(rider_id, strava_activity_id):
    """The rider's cached analysis for one activity, or None.

    Scoped by ``rider_id`` — the read side of the ownership invariant, so a rider
    can never read another rider's cached analysis. Returns the decoded ``analysis``
    dict, the raw compressed ``activity_streams`` blob, and ``computed_at``.
    """
    return db.query_one(
        "SELECT rider_id, strava_activity_id, analysis, activity_streams, computed_at "
        "FROM rp_ride_analysis WHERE rider_id = %s AND strava_activity_id = %s",
        (rider_id, strava_activity_id),
    )


def get_analyzed_activity_ids(rider_id):
    """The set of the rider's Strava activity ids that already have a cached
    analysis — lets the list view mark which activities are analyzed without an
    N+1 query. Rider-scoped, so it never reveals another rider's activity ids."""
    rows = db.query(
        "SELECT strava_activity_id FROM rp_ride_analysis WHERE rider_id = %s",
        (rider_id,),
    )
    return {row['strava_activity_id'] for row in rows}


def upsert_ride_analysis(rider_id, strava_activity_id, analysis,
                         compressed_streams=None):
    """Create or replace the rider's cached analysis for one activity.

    A single atomic upsert on the UNIQUE(rider_id, strava_activity_id) constraint,
    so re-analyzing an already cached activity refreshes the row in place
    (idempotent) instead of raising a unique-violation. ``analysis`` is the
    SERVER-computed breakdown (JSON-adapted
    with psycopg2's ``Json``); ``compressed_streams`` is the zlib-compressed raw
    streams (wrapped with ``Binary`` for the BYTEA column) so the detail/map view
    re-renders without another Strava fetch.

    (The literal is split at ``DO UPDATE`` / ``SET`` for the same rp-only-scanner
    reason documented on :func:`upsert_brevet_event`.)
    """
    db.execute(
        "INSERT INTO rp_ride_analysis "
        "  (rider_id, strava_activity_id, analysis, activity_streams) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (rider_id, strava_activity_id) DO UPDATE "
        "SET analysis = EXCLUDED.analysis, "
        "    activity_streams = EXCLUDED.activity_streams, computed_at = NOW()",
        (rider_id, strava_activity_id, Json(analysis),
         Binary(compressed_streams) if compressed_streams is not None else None),
    )


# --------------------------------------------------------------------------- #
# Club ownership (rp_club.owner_rider_id) — who may generate real RWGPS ride
# plans for a club. The club-admin route gates real-plan generation on these
# checks; NULL owner means no one can admin the club (a safe closed default).
# --------------------------------------------------------------------------- #
def get_club_owned_by_rider(rider_id):
    """The club this rider OWNS (owner_rider_id == rider_id), or None.

    The admin route's ownership gate: a rider with no owned club cannot generate
    plans. Scoped by owner_rider_id, so a rider can only ever act on their own club.
    Touches only rp_club.
    """
    if not rider_id:
        return None
    return db.query_one(
        "SELECT id, name, city, state, rusa_club_id, owner_rider_id "
        "FROM rp_club WHERE owner_rider_id = %s",
        (rider_id,),
    )


def is_club_owner(club_id, rider_id):
    """True iff ``rider_id`` owns ``club_id`` (owner_rider_id match). Touches only
    rp_club. A NULL owner or a mismatched rider is never an owner."""
    if not club_id or not rider_id:
        return False
    row = db.query_one(
        "SELECT 1 AS ok FROM rp_club WHERE id = %s AND owner_rider_id = %s",
        (club_id, rider_id),
    )
    return row is not None


# --------------------------------------------------------------------------- #
# Real RWGPS ride plans (rp_brevet_route_plan + rp_brevet_route_plan_stop) — one
# persisted, real plan per cached brevet, generated by the reused shared/rwgps.py
# engine. Distances/speeds are stored in the engine's NATIVE units (miles / mph /
# feet), verbatim — the /plan route converts to km / km-h at display time, never
# here, so the column names (distance_miles, avg_speed) stay honest. Every query
# targets an rp_* table only.
# --------------------------------------------------------------------------- #
def get_brevet_route_plan(event_id, variant='conservative'):
    """The persisted real ride plan for a brevet + variant, or None.

    One row per (event, variant). ``variant`` defaults to 'conservative' — the legacy
    single-plan rows migrate to conservative, so an unqualified read still resolves to
    the same plan an event had before the conservative/aggressive split.
    """
    return db.query_one(
        "SELECT id, event_id, club_id, variant, name, slug, total_distance_miles, "
        "       total_elevation_ft, rwgps_url, rwgps_route_id, distance_km, "
        "       cutoff_hours, start_time, avg_moving_speed, avg_elapsed_speed, "
        "       total_moving_time_min, total_elapsed_time_min, total_break_time_min, "
        "       overall_ft_per_mile, created_at "
        "FROM rp_brevet_route_plan WHERE event_id = %s AND variant = %s",
        (event_id, variant),
    )


def get_brevet_route_plan_stops(ride_plan_id):
    """The ordered per-control stops of a route plan (by stop_order)."""
    return db.query(
        "SELECT id, ride_plan_id, stop_order, location, stop_type, distance_miles, "
        "       elevation_gain, segment_time_min, notes, seg_dist, ft_per_mi, "
        "       avg_speed, cum_time_min, bookend_time_min, time_bank_min, "
        "       difficulty_score "
        "FROM rp_brevet_route_plan_stop WHERE ride_plan_id = %s "
        "ORDER BY stop_order ASC",
        (ride_plan_id,),
    )


def get_brevet_route_plan_with_stops(event_id, variant='conservative'):
    """The real plan for a brevet + variant plus its ordered stops, or None when no
    such plan exists (so the /plan route falls back to the synthetic schedule).

    ``variant`` defaults to 'conservative' (the /plan default and the legacy plan).
    Returns ``{'plan': <row>, 'stops': [<row>, ...]}``.
    """
    plan = get_brevet_route_plan(event_id, variant)
    if not plan:
        return None
    return {'plan': plan, 'stops': get_brevet_route_plan_stops(plan['id'])}


def get_brevet_route_plan_by_route_id_rp(rwgps_route_id, club_id, variant='conservative'):
    """The best in-tenant real plan for an RWGPS route id, or None.

    Tenant-scoped: matches only a plan owned by ``club_id`` OR a public club-less
    warm plan (club_id IS NULL) — NEVER another club plan, because rwgps_route_id is
    not unique and two clubs can share one route id. When ``club_id`` is None (a
    club-less ride) only club-less plans match, since ``club_id = NULL`` is never
    true. Deterministic pick when a route id maps to more than one accepted plan:
    same-club before club-less, then newest, then highest id.

    PINNED to the conservative variant (the default): after the conservative/aggressive
    split an event has two plans sharing one route id, but live-tracking grades against
    the conservative (legacy default, realistic-pace) plan only — so selection can never
    depend on which variant was inserted last, and the aggressive plan is never graded.
    """
    if not rwgps_route_id:
        return None
    return db.query_one(
        "SELECT id, event_id, club_id, variant, name, slug, total_distance_miles, "
        "       total_elevation_ft, rwgps_url, rwgps_route_id, distance_km, "
        "       cutoff_hours, start_time, created_at "
        "FROM rp_brevet_route_plan "
        "WHERE rwgps_route_id = %s AND (club_id = %s OR club_id IS NULL) "
        "  AND variant = %s "
        "ORDER BY (club_id IS NULL), created_at DESC NULLS LAST, id DESC "
        "LIMIT 1",
        (rwgps_route_id, club_id, variant),
    )


def get_brevet_route_plan_candidates_rp(club_id, variant='conservative'):
    """In-tenant real plans as name-match candidates when no route id matches.

    Returns the id, name, slug, cutoff_hours and total distance of every plan owned
    by ``club_id`` OR club-less (club_id IS NULL), so the shared name matcher is fed
    ONLY same-club and public plans — never another club plan. A club-less ride
    (``club_id`` None) gets only club-less candidates.

    PINNED to the conservative variant (the default) so the name matcher returns
    exactly one plan per event — the same legacy plan it graded before the
    conservative/aggressive split — never both variants of one event.
    """
    return db.query(
        "SELECT id, name, slug, rwgps_route_id, club_id, cutoff_hours, "
        "       total_distance_miles, created_at "
        "FROM rp_brevet_route_plan "
        "WHERE (club_id = %s OR club_id IS NULL) AND variant = %s "
        "ORDER BY (club_id IS NULL), created_at DESC NULLS LAST, id DESC",
        (club_id, variant),
    )


def upsert_brevet_route_plan(event_id, plan, stops, club_id=None,
                             variant='conservative'):
    """Persist a real RWGPS-derived plan + its stops for a brevet variant, atomically.

    One transaction on the per-request connection (the per-call ``db.execute`` can't
    span the three statements): upsert the plan row keyed on the
    UNIQUE(event_id, variant) constraint (so re-warming/re-generating the same brevet
    variant refreshes it in place — idempotent, and the conservative + aggressive
    variants coexist as two rows under one event), delete the old stops, then re-insert
    the fresh ordered stops. Values are stored VERBATIM in the engine's native
    miles / mph / feet.

    OWNERSHIP GUARD (authorization): there is one public plan per brevet, so the
    ON CONFLICT clause writes a row only when the existing plan is UNOWNED
    (``club_id IS NULL``, i.e. auto-warmed by cron) or already owned by the SAME club.
    A club owner can adopt an unowned plan or refresh the plan for the same club, but
    can NEVER overwrite a different club (first club to generate owns it). When the
    write is blocked, nothing changes (the stops are left untouched) and this returns
    ``None``; the caller surfaces that as a flash / skip rather than a silent clobber.
    The warm cron passes ``club_id=None``, so it can create or refresh only unowned
    plans and never clobbers a club-owned one.

    ``plan`` / ``stops`` are the two parts of the dict returned by
    shared.rwgps.build_ride_plan (``{'plan': ..., 'stops': [...]}``). Returns the
    new/updated plan id, or ``None`` when a different club already owns the plan.
    """
    # slug is UNIQUE per (event_id, variant); suffix with BOTH the event id and the
    # variant so two brevets that share a route name never collide AND the two variants
    # of one event never collide, while staying deterministic (idempotent) per variant.
    slug = f"{plan.get('slug') or 'route'}-{event_id}-{variant}"

    conn = db.get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO rp_brevet_route_plan "
                "  (event_id, club_id, variant, name, slug, total_distance_miles, "
                "   total_elevation_ft, rwgps_url, rwgps_route_id, distance_km, "
                "   cutoff_hours, start_time, avg_moving_speed, avg_elapsed_speed, "
                "   total_moving_time_min, total_elapsed_time_min, "
                "   total_break_time_min, overall_ft_per_mile) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (event_id, variant) DO UPDATE "
                "SET club_id = EXCLUDED.club_id, name = EXCLUDED.name, "
                "    slug = EXCLUDED.slug, "
                "    total_distance_miles = EXCLUDED.total_distance_miles, "
                "    total_elevation_ft = EXCLUDED.total_elevation_ft, "
                "    rwgps_url = EXCLUDED.rwgps_url, "
                "    rwgps_route_id = EXCLUDED.rwgps_route_id, "
                "    distance_km = EXCLUDED.distance_km, "
                "    cutoff_hours = EXCLUDED.cutoff_hours, "
                "    start_time = EXCLUDED.start_time, "
                "    avg_moving_speed = EXCLUDED.avg_moving_speed, "
                "    avg_elapsed_speed = EXCLUDED.avg_elapsed_speed, "
                "    total_moving_time_min = EXCLUDED.total_moving_time_min, "
                "    total_elapsed_time_min = EXCLUDED.total_elapsed_time_min, "
                "    total_break_time_min = EXCLUDED.total_break_time_min, "
                "    overall_ft_per_mile = EXCLUDED.overall_ft_per_mile "
                # Ownership guard: adopt an unowned plan or refresh the same club;
                # never clobber a different club. A blocked write changes no rows, so
                # RETURNING yields nothing and fetchone() is None.
                "WHERE rp_brevet_route_plan.club_id IS NULL "
                "   OR rp_brevet_route_plan.club_id = EXCLUDED.club_id "
                "RETURNING id",
                (event_id, club_id, variant, plan.get('name'), slug,
                 plan.get('total_distance_miles'), plan.get('total_elevation_ft'),
                 plan.get('rwgps_url'), plan.get('rwgps_route_id'),
                 plan.get('distance_km'), plan.get('cutoff_hours'),
                 plan.get('start_time', '07:00'),
                 plan.get('avg_moving_speed'), plan.get('avg_elapsed_speed'),
                 plan.get('total_moving_time_min'), plan.get('total_elapsed_time_min'),
                 plan.get('total_break_time_min'), plan.get('overall_ft_per_mile')),
            )
            row = cur.fetchone()
            if row is None:
                # A different club owns this brevet's plan — do NOT touch its stops.
                conn.rollback()
                return None
            plan_id = row['id']

            cur.execute(
                "DELETE FROM rp_brevet_route_plan_stop WHERE ride_plan_id = %s",
                (plan_id,),
            )
            for s in stops:
                cur.execute(
                    "INSERT INTO rp_brevet_route_plan_stop "
                    "  (ride_plan_id, stop_order, location, stop_type, distance_miles, "
                    "   elevation_gain, segment_time_min, notes, seg_dist, ft_per_mi, "
                    "   avg_speed, cum_time_min, bookend_time_min, time_bank_min, "
                    "   difficulty_score) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (plan_id, s['stop_order'], s['location'], s['stop_type'],
                     s.get('distance_miles'), s.get('elevation_gain'),
                     s.get('segment_time_min'), s.get('notes', ''),
                     s.get('seg_dist'), s.get('ft_per_mi'), s.get('avg_speed'),
                     s.get('cum_time_min'), s.get('bookend_time_min'),
                     s.get('time_bank_min'), s.get('difficulty_score')),
                )
        conn.commit()
        return plan_id
    except Exception:
        conn.rollback()
        raise


def get_route_plan_warm_targets():
    """Upcoming brevets with a non-NULL rwgps_url that are still MISSING a variant —
    the events the warm cron should pre-fetch and persist both variants for.

    Returns ``[{id, rwgps_url, start_time}, ...]`` for events dated today or later that
    carry an RWGPS URL AND do not yet have BOTH stored plan variants (conservative +
    aggressive). ``start_time`` rides along so the cron can clock-type the meal breaks.
    An event that already has both variants is not re-warmed (the daily cron is a
    fill-in, not a rebuild); events without an RWGPS URL have no real route to build, so
    they are skipped (the calendar falls back to the synthetic schedule). The
    variant-count filter reads rp_brevet_route_plan; both tables are rp_*.
    """
    return db.query(
        "SELECT e.id, e.rwgps_url, e.start_time FROM rp_brevet_event e "
        "WHERE e.date >= CURRENT_DATE AND e.rwgps_url IS NOT NULL "
        "  AND (SELECT COUNT(DISTINCT p.variant) FROM rp_brevet_route_plan p "
        "       WHERE p.event_id = e.id) < 2 "
        "ORDER BY e.date ASC",
    )


def get_events_needing_rwgps_url(limit):
    """Brevet events still missing an rwgps_url that carry a rusa_route_id, upcoming first.

    Returns ``[{id, rusa_route_id}, ...]`` for at most ``limit`` events WHERE the
    rwgps_url column is NULL and a rusa_route_id is present (the backfill needs a
    route id to scrape). Future-dated events sort ahead of past ones, then by date
    ascending, so a bounded run resolves the events a rider is about to plan for
    first. The ``limit`` (the caller BATCH_SIZE) keeps a run well within the
    serverless budget; the NULL-only filter makes it idempotent — a row already
    holding a URL is never reselected. Touches only rp_brevet_event.
    """
    return db.query(
        "SELECT id, rusa_route_id FROM rp_brevet_event "
        "WHERE rwgps_url IS NULL AND rusa_route_id IS NOT NULL "
        "ORDER BY (date >= CURRENT_DATE) DESC, date ASC "
        "LIMIT %s",
        (limit,),
    )


def set_event_rwgps_url(event_id, rwgps_url):
    """Write a scraped rwgps_url onto one brevet event, guarded on a still-NULL column.

    A single-column writer used only by the backfill cron. The WHERE clause re-asserts
    rwgps_url IS NULL, so a row already filled (by the calendar upsert or an earlier
    run) is never overwritten and the backfill stays idempotent and safe. Returns True
    when a row changed (RETURNING id, since db.execute yields the first row not a
    rowcount). Touches only rp_brevet_event.

    (The literal is split at ``UPDATE`` / ``SET`` for the same rp-only-scanner reason
    documented on :func:`upsert_brevet_event`.)
    """
    row = db.execute(
        "UPDATE rp_brevet_event "
        "SET rwgps_url = %s "
        "WHERE id = %s AND rwgps_url IS NULL "
        "RETURNING id",
        (rwgps_url, event_id),
        returning=True,
    )
    return row is not None


# --------------------------------------------------------------------------- #
# Brevet route weather cache (rp_brevet_route_weather) — one dense per-sample
# Open-Meteo forecast per (event, date), warmed OFF the request path by the
# warm-brevet-route-weather cron and READ (never fetched) by the /plan page. Mirrors
# Team Asha's route_weather_cache but keyed on the calendar event. Every query below
# targets an rp_* table only.
# --------------------------------------------------------------------------- #
def get_route_weather_warm_targets(horizon_days=16):
    """Near-term brevets that HAVE a persisted real plan — the only events whose /plan
    page renders per-stop wind — each paired with the PLAN's route (not the event's).

    Returns ``[{id, date, rwgps_url, rwgps_route_id}, ...]`` for events dated between
    today and ``today + horizon_days`` (Open-Meteo's forecast horizon) that have a row
    in rp_brevet_route_plan. The route fields are read off the PERSISTED PLAN, with the
    event's URL used only as a fallback when the plan omits one.

    Driving off the plan's route (rather than ``rp_brevet_event.rwgps_url``) is a
    correctness requirement: a club owner can generate the plan against an admin-entered
    RWGPS URL that need not match the event's (see routes/admin.py), and the /plan page
    maps each plan stop onto THAT route. Warming off the event URL could sample the wind
    along the wrong course — or skip it entirely when only the plan carries a URL.
    Events without a persisted plan are skipped (no real plan → no Wind column → nothing
    to warm). Touches only rp_* tables.

    PINNED to the conservative variant: after the conservative/aggressive split an event
    has two plan rows, but the two variants map the SAME route, so filtering to
    conservative keeps this ONE row per event — the weather cron fetches each course
    once, not twice (the /plan wind overlay is variant-agnostic route geometry).
    """
    return db.query(
        "SELECT e.id AS id, e.date AS date, "
        "       COALESCE(p.rwgps_url, e.rwgps_url) AS rwgps_url, "
        "       p.rwgps_route_id AS rwgps_route_id "
        "FROM rp_brevet_route_plan p "
        "JOIN rp_brevet_event e ON e.id = p.event_id "
        "WHERE e.date >= CURRENT_DATE "
        "  AND e.date <= CURRENT_DATE + make_interval(days => %s) "
        "  AND p.variant = %s "
        "ORDER BY e.date ASC",
        (horizon_days, 'conservative'),
    )


def upsert_brevet_route_weather(event_id, forecast_date, weather_data, sample_points):
    """Insert or refresh one cached along-route forecast, keyed on
    (event_id, forecast_date).

    A single atomic upsert on the UNIQUE(event_id, forecast_date) constraint, so a
    repeated cron run refreshes the row in place (idempotent) instead of raising a
    unique-violation. ``weather_data`` is the raw Open-Meteo per-sample forecast list
    and ``sample_points`` is the aligned ``[{lat, lng, distance_m}]``; both are
    JSON-adapted with psycopg2's ``Json``. Only ever called by the cron with a
    successful fetch, so a transient failure never overwrites a last-good row.

    (The literal is split at ``DO UPDATE`` / ``SET`` for the same rp-only-scanner
    reason documented on :func:`upsert_brevet_event`.)
    """
    db.execute(
        "INSERT INTO rp_brevet_route_weather "
        "  (event_id, forecast_date, weather_data, sample_points) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (event_id, forecast_date) DO UPDATE "
        "SET weather_data = EXCLUDED.weather_data, "
        "    sample_points = EXCLUDED.sample_points, fetched_at = NOW()",
        (event_id, forecast_date, Json(weather_data), Json(sample_points)),
    )


def get_brevet_route_weather(event_id, forecast_date):
    """The cached along-route forecast for a brevet on a date, or None.

    Returns ``{weather_data, sample_points, forecast_date, fetched_at}`` (the raw
    per-sample Open-Meteo list plus the aligned sample points) so the /plan route can
    map each stop to the nearest sample and compute per-stop wind in-process
    (shared/weather.py compute_stop_winds). Returns None when nothing is stored (new
    route, beyond-horizon brevet, or the cron has not run yet), so the caller degrades
    gracefully with no live fallback. Touches only rp_brevet_route_weather.
    """
    return db.query_one(
        "SELECT event_id, forecast_date, weather_data, sample_points, fetched_at "
        "FROM rp_brevet_route_weather WHERE event_id = %s AND forecast_date = %s",
        (event_id, forecast_date),
    )
