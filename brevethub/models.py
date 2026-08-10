"""BrevetHub data model — rp_* tables only.

Every SQL statement in this module targets a `rp_`-prefixed tenant table. The
app never reads or writes any Team Asha table; `tests/brevethub/test_rp_only.py`
scans this file and fails the build if a non-`rp_` table name ever appears.
"""
import secrets
from datetime import date, datetime, time, timedelta
from enum import Enum

import psycopg2.extras
from psycopg2 import Binary
from psycopg2.extras import Json

from brevethub import db


class RideStatus(str, Enum):
    """BrevetHub own ride-status enum — defined here so BrevetHub shares no code
    with the parent web app models. Kept as a str-Enum for direct SQL binding.

    Pre-ride:  interested / maybe / registered / withdraw / withdrawal_requested / rejected.
    Post-ride: finished / dnf / dns / otl (a result, set once ride start + 1 minute).

    The helper classmethods below mirror the parent web app state machine so the
    routes can gate transitions without re-deriving the rules. BrevetHub stays
    LOWERCASE where the parent web app uses uppercase status strings — a deliberate,
    documented divergence (see the web-parity notes in the PR).
    """
    # Pre-ride statuses
    INTERESTED = 'interested'
    MAYBE = 'maybe'
    REGISTERED = 'registered'
    WITHDRAW = 'withdraw'
    WITHDRAWAL_REQUESTED = 'withdrawal_requested'
    REJECTED = 'rejected'
    # Post-ride result statuses (available after ride start + 1 minute)
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
        if val == 'going':
            return cls.REGISTERED
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
        return status in (cls.INTERESTED, cls.MAYBE, cls.REGISTERED)

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
        return status in (cls.INTERESTED, cls.MAYBE, cls.REGISTERED)

    @classmethod
    def is_final_for_close(cls, status):
        """True when a roster rider's status allows the event to be closed."""
        return status in (cls.FINISHED, cls.DNF, cls.DNS, cls.OTL, cls.WITHDRAW)


def event_post_ride_open(event):
    """True when local wall-clock time is at least one minute after event start."""
    if not event or not event.get('date'):
        return False
    event_date = event['date']
    if isinstance(event_date, str):
        event_date = date.fromisoformat(event_date[:10])
    start_raw = event.get('start_time') or '06:00'
    if isinstance(start_raw, time):
        start_t = start_raw
    else:
        parts = str(start_raw).split(':')
        start_t = time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    start_dt = datetime.combine(event_date, start_t)
    return datetime.now() >= start_dt + timedelta(minutes=1)


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
_RIDER_PROFILE_COLS = (
    "id, email, google_id, rusa_id, club_id, "
    "profile_completed, rusa_id_duplicate, created_at, last_login_at, "
    "first_name, last_name, phone, city, emergency_name, emergency_phone, "
    "sfr_member_year, rusa_membership_expires, rusa_membership_checked_at, "
    "eddington_km, eddington_miles, eddington_calculated_at"
)


def get_rider_by_google_id(google_id):
    return db.query_one(
        f"SELECT {_RIDER_PROFILE_COLS} FROM rp_rider WHERE google_id = %s",
        (google_id,),
    )


def get_rider_membership_fields(rider_id):
    """Lightweight membership lookup for global nav banners."""
    row = db.query_one(
        "SELECT rusa_id, sfr_member_year, rusa_membership_expires, "
        "       rusa_membership_checked_at "
        "FROM rp_rider WHERE id = %s",
        (rider_id,),
    )
    if not row:
        return None
    return {
        'rusa_id': row['rusa_id'],
        'sfr_member_year': row['sfr_member_year'],
        'rusa_membership_expires': row['rusa_membership_expires'],
        'rusa_membership_checked_at': row['rusa_membership_checked_at'],
    }


def get_rider_by_rusa_id(rusa_id):
    if not rusa_id:
        return None
    return db.query_one(
        f"SELECT {_RIDER_PROFILE_COLS} FROM rp_rider WHERE rusa_id = %s",
        (str(rusa_id),),
    )


def update_rider_rusa_membership(rider_id, *, membership_expires, checked_at=None):
    """Persist a RUSA.org membership scrape (NULL expiry = not found or unknown)."""
    return db.execute(
        "UPDATE rp_rider SET rusa_membership_expires = %s, "
        "    rusa_membership_checked_at = COALESCE(%s, NOW()) "
        "WHERE id = %s "
        f"RETURNING {_RIDER_PROFILE_COLS}",
        (membership_expires, checked_at, rider_id),
        returning=True,
    )


def clear_rider_rusa_membership_cache(rider_id):
    return db.execute(
        "UPDATE rp_rider SET rusa_membership_expires = NULL, "
        "    rusa_membership_checked_at = NULL "
        "WHERE id = %s "
        f"RETURNING {_RIDER_PROFILE_COLS}",
        (rider_id,),
        returning=True,
    )


def get_rider_membership_year(rider_id):
    """Back-compat: return SFR membership year only."""
    fields = get_rider_membership_fields(rider_id)
    return fields['sfr_member_year'] if fields else None


def get_rider_by_id(rider_id):
    """The rider row for the signed-in session (the own-profile loader).

    Carries the cached Eddington columns (km + miles + calculated_at) so the own
    profile renders the number without a second query; they are NULL until the
    first compute (on Strava connect or the daily cron), which the template shows
    as a graceful prompt rather than a fabricated zero.
    """
    return db.query_one(
        f"SELECT {_RIDER_PROFILE_COLS} FROM rp_rider WHERE id = %s",
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
    """True if another rider has already claimed this RUSA ID."""
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


def get_rider_live_rides(rider_id):
    """Public live rides for events this rider follows or is going on.

    Follow state is stored against the calendar event, while a live ride may be
    linked to that event later (and older rides can have a missing ``event_id``
    but still match by name/date).  Resolve the event before joining follow and
    signup rows; joining those rows directly to ``r.event_id`` silently drops a
    followed ride whenever the ride was created before it was linked.
    """
    return db.query(
        "SELECT DISTINCT r.id, r.name, r.distance_km, r.start_at, r.status, c.name AS club_name "
        "FROM rp_ride r LEFT JOIN rp_club c ON c.id=r.club_id "
        "LEFT JOIN rp_brevet_event e ON (e.id=r.event_id OR "
        "  (r.event_id IS NULL AND lower(btrim(r.name))=lower(btrim(e.name)) "
        "   AND r.start_at::date=e.date)) "
        "LEFT JOIN rp_event_signup s ON s.event_id=e.id AND s.rider_id=%s "
        "LEFT JOIN rp_followed_live_event f ON f.event_id=e.id AND f.rider_id=%s "
        "WHERE r.is_public=TRUE AND (f.event_id IS NOT NULL OR s.status=%s) "
        "ORDER BY r.start_at DESC NULLS LAST",
        (rider_id, rider_id, RideStatus.REGISTERED.value),
    )


def get_public_ride(ride_id):
    """A single PUBLIC ride by id (the guest-view 404 gate), joined to its club.

    Returns None when the ride is unknown OR is_public = FALSE, so a private/
    unknown ride is indistinguishable to a guest (both 404). No rider PII.
    """
    return db.query_one(
        "SELECT r.id, r.name, r.distance_km, r.start_at, r.status, r.rwgps_url, "
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
    """Own rides for one rider, for the create/flag page listing + share links.

    ``event_id`` is selected too so the manage page can show which calendar event
    each ride is currently linked to (NULL when unlinked).
    """
    return db.query(
        "SELECT id, name, distance_km, start_at, status, is_public, event_id "
        "FROM rp_ride WHERE rider_id = %s ORDER BY start_at DESC NULLS LAST",
        (rider_id,),
    )


def get_rider_ride_for_event(rider_id, event_id):
    """The rider OWN ride linked to a calendar event, or None (most recent first).

    Backs the event-scoped live view share surface: a logged-in rider appears on the
    event Live map through a ride they own that is linked to the event (event_id FK).
    Scoped to the session rider, so it never reveals another rider ride. Returns the
    ride row (id, is_public, name, event_id) or None when the rider has not joined
    this event yet. Touches only rp_ride.
    """
    return db.query_one(
        "SELECT id, name, distance_km, is_public, event_id "
        "FROM rp_ride WHERE rider_id = %s AND event_id = %s "
        "ORDER BY start_at DESC NULLS LAST LIMIT 1",
        (rider_id, event_id),
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


def set_ride_event(ride_id, rider_id, event_id):
    """Link or unlink one ride owned by the rider to a calendar event.

    Owner-scoped: the write is filtered by rider_id too, so a non-owner can never
    point another rider ride at an event. Pass ``event_id=None`` to unlink and
    clear the FK back to NULL. Returns the updated row id or None when the ride is
    not owned by the rider, so the caller can report a non-owner no-op. The FK
    itself guarantees a missing event cannot be stored; the route validates event
    existence too.
    """
    return db.execute(
        "UPDATE rp_ride SET event_id = %s WHERE id = %s AND rider_id = %s "
        "RETURNING id",
        (event_id, ride_id, rider_id),
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
    Returns rider_id, TWO name fields, lat/lng, recorded_at, and telemetry
    (speed/heart_rate/power/cadence) + source:
      - `name` — the AUTHENTICATED member/mobile card name: the rider's display_name
        when set, else the email local-part. Shown ONLY behind authentication (the
        @profile_required member endpoint / bearer mobile poll), where it is not a
        public leak — so members stay distinguishable until display_name is set.
      - `display_name` — the raw display_name (NULL when unset). The PUBLIC roster
        uses THIS (never the email local-part), defaulting a NULL to a neutral token
        at the route layer, so no email ever reaches the world-viewable payload.

    Consumed by the @profile_required member endpoint AND, privacy-shaped through
    build_radial_roster (which drops rider_id + reads display_name), by the public
    roster.json poll."""
    return db.query(
        "SELECT DISTINCT ON (p.rider_id) "
        "       p.rider_id, "
        "       COALESCE(NULLIF(r.display_name, ''), split_part(r.email, '@', 1)) AS name, "
        "       NULLIF(r.display_name, '') AS display_name, "
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
# season rosters, public rider profile). Every query here is
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
        "SELECT r.id, r.email, r.rusa_id, r.rusa_cache, "
        "       CASE WHEN sc.id IS NOT NULL THEN r.eddington_miles END "
        "         AS eddington "
        "FROM rp_rider r "
        "LEFT JOIN rp_strava_connection sc ON sc.rider_id = r.id "
        "WHERE r.club_id = %s AND r.profile_completed = TRUE "
        "  AND r.rusa_id IS NOT NULL "
        "  AND jsonb_typeof(r.rusa_cache) = 'array' "
        "  AND jsonb_array_length(r.rusa_cache) > 0 "
        "ORDER BY r.email ASC",
        (club_id,),
    )


def get_club_rider(club_id, rider_id):
    """One club-scoped rider by primary key, with RUSA-backed public fields only.

    The row is returned only when it belongs to the given club and has a nonempty
    official RUSA history, so a viewer can never resolve a local-only rider or a
    rider outside their own club.
    """
    return db.query_one(
        "SELECT r.id, r.email, r.rusa_id, r.club_id, r.created_at, r.rusa_cache, "
        "       CASE WHEN sc.id IS NOT NULL THEN r.eddington_miles END AS eddington "
        "FROM rp_rider r LEFT JOIN rp_strava_connection sc ON sc.rider_id = r.id "
        "WHERE club_id = %s AND id = %s AND profile_completed = TRUE "
        "AND rusa_id IS NOT NULL "
        "AND jsonb_typeof(rusa_cache) = 'array' "
        "AND jsonb_array_length(rusa_cache) > 0",
        (club_id, rider_id),
    )


def get_public_rider(rider_id):
    """Return only RUSA-backed fields for an anonymous public profile.

    BrevetHub's public directory is an official-randonneuring surface, not a
    Strava/social directory. Requiring a RUSA id and a completed profile keeps
    local-only accounts and their private activity out of public pages. A nonempty
    cached official history is required; merely typing a numeric ID is not enough.
    """
    return db.query_one(
        "SELECT r.id, r.email, r.rusa_id, r.rusa_cache, "
        "       CASE WHEN sc.id IS NOT NULL THEN r.eddington_miles END AS eddington "
        "FROM rp_rider r LEFT JOIN rp_strava_connection sc ON sc.rider_id = r.id "
        "WHERE r.id = %s AND r.profile_completed = TRUE "
        "AND rusa_id IS NOT NULL "
        "AND jsonb_typeof(rusa_cache) = 'array' "
        "AND jsonb_array_length(rusa_cache) > 0",
        (rider_id,),
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
    """Upcoming brevets with separate Registered and Interested aggregate counts.

    ``state`` optionally narrows to one US state by matching the RUSA region
    label ``"<STATE>: ..."`` prefix — an honest, documented narrowing a generic
    multi-club app can do without the Team Asha hardcoded region->club map. None
    returns every upcoming brevet (the general RUSA calendar).

    ``signup_count`` counts Registered only; ``interested_count`` counts Interested only.
    Both are AGGREGATES, so the guest calendar can show intent without exposing any
    rider identity. Legacy Maybe and Withdraw rows are excluded. The counts come from
    a pre-aggregated
    sub-select LEFT-joined on the event id, so an event with zero sign-ups still
    returns (coalesced to 0) — both the sub-select and the outer query touch only
    rp_* tables (rp_event_signup / rp_brevet_event).
    """
    like = (state + ': %') if state else None
    return db.query(
        "SELECT e.id, e.rusa_route_id, e.name, e.date, e.distance_km, e.region, "
        "       e.ride_type, e.elevation_ft, e.rwgps_url, e.start_location, "
        "       e.club_id, c.name AS club_name, c.state AS club_state, "
        "       e.start_time, e.time_limit_hours, "
        "       e.fee_cents, e.registration_deadline, e.capacity, "
        "       e.event_summary, e.registration_enabled, e.volunteer_enabled, "
        "       COALESCE(sc.signup_count, 0) AS signup_count, "
        "       COALESCE(sc.interested_count, 0) AS interested_count, "
        "       COALESCE(sc.confirmed_count, 0) AS confirmed_count "
        "FROM rp_brevet_event e LEFT JOIN rp_club c ON c.id = e.club_id "
        "LEFT JOIN ("
        "  SELECT event_id, "
        "    COUNT(*) FILTER (WHERE status = %s) AS signup_count, "
        "    COUNT(*) FILTER (WHERE status = %s) AS interested_count, "
        "    COUNT(*) FILTER (WHERE registration_status = 'confirmed') AS confirmed_count "
        "  FROM rp_event_signup GROUP BY event_id"
        ") sc ON sc.event_id = e.id "
        "WHERE e.date >= CURRENT_DATE AND (%s::text IS NULL OR e.region ILIKE %s) "
        "ORDER BY e.date ASC, e.distance_km ASC LIMIT %s",
        (RideStatus.REGISTERED.value, RideStatus.INTERESTED.value,
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


def get_cached_elevation_for_rusa_route(route_id):
    """Return a known elevation for a RUSA route id from any cached brevet row."""
    if not route_id:
        return None
    row = db.query_one(
        "SELECT elevation_ft FROM rp_brevet_event "
        "WHERE rusa_route_id = %s AND elevation_ft IS NOT NULL "
        "ORDER BY date DESC LIMIT 1",
        (str(route_id),),
    )
    return row['elevation_ft'] if row else None


def backfill_missing_event_elevations_from_routes():
    """Copy elevation onto route siblings that RUSA left blank in the climbing column."""
    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE rp_brevet_event e "
                "SET elevation_ft = peer.elevation_ft "
                "FROM ( "
                "  SELECT rusa_route_id, MAX(elevation_ft) AS elevation_ft "
                "  FROM rp_brevet_event "
                "  WHERE rusa_route_id IS NOT NULL AND elevation_ft IS NOT NULL "
                "  GROUP BY rusa_route_id "
                ") peer "
                "WHERE e.elevation_ft IS NULL "
                "  AND e.rusa_route_id IS NOT NULL "
                "  AND e.rusa_route_id = peer.rusa_route_id",
            )
            updated = cur.rowcount
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise


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
    """Request withdrawal from a registered ride, or set DNS after ride start.

    Returns a route sentinel:
      not_found         no sign-up row
      not_registered    rider has not completed registration
      has_result        post-ride result already set
      already_requested withdrawal already pending admin review
      requested         status set to withdrawal_requested (still on roster)
      dns               after ride start + 1 min, status set to DNS
    """
    row = db.query_one(
        "SELECT s.status, s.registration_status, e.date, e.start_time "
        "FROM rp_event_signup s "
        "JOIN rp_brevet_event e ON e.id = s.event_id "
        "WHERE s.rider_id = %s AND s.event_id = %s",
        (rider_id, event_id),
    )
    if not row:
        return 'not_found'
    if not row['registration_status']:
        return 'not_registered'
    if RideStatus.is_post_ride(RideStatus.normalize(row['status'])):
        return 'has_result'
    if row['status'] == RideStatus.WITHDRAWAL_REQUESTED.value:
        return 'already_requested'

    event = {'date': row['date'], 'start_time': row['start_time']}
    if event_post_ride_open(event):
        db.execute(
            "UPDATE rp_event_signup "
            "SET status = %s, updated_at = NOW() "
            "WHERE rider_id = %s AND event_id = %s "
            "  AND status NOT IN (%s, %s, %s, %s)",
            (RideStatus.DNS.value, rider_id, event_id,
             RideStatus.FINISHED.value, RideStatus.DNF.value,
             RideStatus.DNS.value, RideStatus.OTL.value),
        )
        return 'dns'

    db.execute(
        "UPDATE rp_event_signup "
        "SET status = %s, updated_at = NOW() "
        "WHERE rider_id = %s AND event_id = %s "
        "  AND status NOT IN (%s, %s, %s, %s)",
        (RideStatus.WITHDRAWAL_REQUESTED.value, rider_id, event_id,
         RideStatus.FINISHED.value, RideStatus.DNF.value,
         RideStatus.DNS.value, RideStatus.OTL.value),
    )
    return 'requested'


def admin_approve_withdrawal(event_id, rider_id):
    """Remove a rider from the roster after approving their withdrawal request."""
    row = db.execute(
        "DELETE FROM rp_event_signup "
        "WHERE event_id = %s AND rider_id = %s AND status = %s RETURNING id",
        (event_id, rider_id, RideStatus.WITHDRAWAL_REQUESTED.value),
        returning=True,
    )
    return 'approved' if row else 'not_found'


def admin_reject_withdrawal(event_id, rider_id):
    """Reject a withdrawal request — rider off roster with rejected status."""
    row = db.execute(
        "UPDATE rp_event_signup "
        "SET status = %s, registration_status = NULL, updated_at = NOW() "
        "WHERE event_id = %s AND rider_id = %s AND status = %s RETURNING id",
        (RideStatus.REJECTED.value, event_id, rider_id,
         RideStatus.WITHDRAWAL_REQUESTED.value),
        returning=True,
    )
    return 'rejected' if row else 'not_found'


def get_event_signup_counts(event_id):
    """Fresh going / interested / confirmed counts for a single event.

    Called after each signup mutation so the API response carries live counts
    the client can update the roster badge with immediately, without a page reload.
    Returns a dict with registered_count, interested_count, confirmed_count (all int).
    """
    row = db.query_one(
        "SELECT "
        "  COUNT(*) FILTER (WHERE status = %s) AS registered_count, "
        "  COUNT(*) FILTER (WHERE status = %s) AS interested_count, "
        "  COUNT(*) FILTER (WHERE registration_status = 'confirmed') AS confirmed_count "
        "FROM rp_event_signup WHERE event_id = %s",
        (RideStatus.REGISTERED.value, RideStatus.INTERESTED.value, event_id),
    )
    return {
        'registered_count': int(row['registered_count'] or 0) if row else 0,
        'interested_count': int(row['interested_count'] or 0) if row else 0,
        'confirmed_count': int(row['confirmed_count'] or 0) if row else 0,
    }


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
        "SELECT status, registration_status FROM rp_event_signup "
        "WHERE rider_id = %s AND event_id = %s",
        (rider_id, event_id),
    )
    if not row:
        return 'not_found'
    if row.get('registration_status'):
        return 'registered'
    if not RideStatus.can_remove(RideStatus.normalize(row['status'])):
        return 'post_ride'
    db.execute(
        "DELETE FROM rp_event_signup "
        "WHERE rider_id = %s AND event_id = %s AND status IN (%s, %s, %s)",
        (rider_id, event_id, RideStatus.INTERESTED.value, RideStatus.MAYBE.value,
         RideStatus.REGISTERED.value),
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
        "SELECT s.status, s.finish_time, s.registration_status, "
        "       e.date, e.start_time "
        "FROM rp_event_signup s "
        "JOIN rp_brevet_event e ON e.id = s.event_id "
        "WHERE s.rider_id = %s AND s.event_id = %s",
        (rider_id, event_id),
    )
    if not row:
        return ('not_found', None)
    if not event_post_ride_open({'date': row['date'], 'start_time': row['start_time']}):
        return ('not_past', None)
    current = RideStatus.normalize(row['status'])
    if not (current == RideStatus.REGISTERED or RideStatus.is_post_ride(current)):
        return ('ineligible', None)

    new_status = RideStatus.normalize(status)
    # The eligibility set re-asserted by the guarded write: a going row or any
    # post-ride result. Kept identical to the read-time predicate above.
    eligible = (RideStatus.REGISTERED.value, RideStatus.FINISHED.value,
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
        (RideStatus.FINISHED.value, RideStatus.REGISTERED.value),
        returning=True,
    )
    return row['n'] if row else 0


def get_event_registered_riders(event_id):
    """The pre-ride roster for a brevet plan page — riders who are interested / maybe
    / registered, exposed as EMAIL LOCAL-PART ONLY.

    Guest-safety: the plan page is public, so this must never leak a full email address,
    google_id, or rider_id. Only ``split_part(email, '@', 1)`` (the part before the '@')
    and the pre-ride status are selected — the same local-part-only idiom the live map
    uses. Ordered registered-first, then interested / maybe, then by local-part. rp_* only.
    """
    return db.query(
        "SELECT split_part(r.email, '@', 1) AS name, s.status "
        "FROM rp_event_signup s "
        "JOIN rp_rider r ON r.id = s.rider_id "
        "WHERE s.event_id = %s AND s.status IN (%s, %s, %s) "
        "ORDER BY CASE s.status WHEN %s THEN 0 WHEN %s THEN 1 ELSE 2 END, name ASC",
        (event_id, RideStatus.REGISTERED.value, RideStatus.INTERESTED.value,
         RideStatus.MAYBE.value, RideStatus.REGISTERED.value, RideStatus.INTERESTED.value),
    )


def get_event_finishers(event_id):
    """Official RUSA-backed finishers for a completed event, fastest first."""
    return db.query(
        "SELECT split_part(r.email, '@', 1) AS name, r.rusa_id, "
        "       s.finish_time, s.status "
        "FROM rp_event_signup s JOIN rp_rider r ON r.id = s.rider_id "
        "WHERE s.event_id = %s AND s.status = %s AND s.finish_time IS NOT NULL "
        "ORDER BY s.finish_time ASC NULLS LAST, name ASC",
        (event_id, RideStatus.FINISHED.value),
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
         RideStatus.REGISTERED.value),
    )


def get_rider_past_results(rider_id):
    """The rider's completed brevets, newest first.

    Evidence is the only rider action on this surface.  The latest validation
    submission is joined so templates can display ``Awaiting verification`` until
    an organizer approves it, then ``Finished``.
    """
    return db.query(
        "SELECT s.event_id, s.status, s.finish_time, s.homologation_number, s.evidence_submission_allowed, e.name, e.date, e.distance_km, e.region, "
        "       vs.id AS submission_id, vs.machine_decision, vs.organizer_decision "
        "FROM rp_event_signup s "
        "JOIN rp_brevet_event e ON e.id = s.event_id "
        "LEFT JOIN LATERAL (SELECT id, machine_decision, organizer_decision "
        "  FROM rp_validation_submission WHERE event_id=s.event_id AND rider_id=s.rider_id "
        "  ORDER BY created_at DESC LIMIT 1) vs ON TRUE "
        "WHERE s.rider_id = %s AND s.status = %s "
        "  AND e.date < CURRENT_DATE "
        "ORDER BY e.date DESC, e.distance_km DESC",
        (rider_id, RideStatus.FINISHED.value),
    )


def get_rider_completed_validation_events(rider_id):
    """Completed brevets this rider may submit evidence for.

    This is deliberately narrower than ``get_rider_past_results``: only an
    authenticated rider's own FINISHED rows are returned, and each row carries
    the current validation status so the dashboard can offer a one-click
    submission without making riders guess which events are eligible.
    """
    return db.query(
        "SELECT s.event_id, s.finish_time, s.homologation_number, s.evidence_submission_allowed, e.name, e.date, e.distance_km, "
        "       e.region, e.rwgps_url, e.start_location, e.start_time, "
        "       vs.id AS submission_id, vs.machine_decision, "
        "       vs.organizer_decision, vs.created_at AS submitted_at "
        "FROM rp_event_signup s "
        "JOIN rp_brevet_event e ON e.id = s.event_id "
        "LEFT JOIN LATERAL ("
        "  SELECT id, machine_decision, organizer_decision, created_at "
        "  FROM rp_validation_submission "
        "  WHERE event_id = s.event_id AND rider_id = s.rider_id "
        "  ORDER BY created_at DESC LIMIT 1"
        ") vs ON TRUE "
        "WHERE s.rider_id = %s AND s.status = %s AND e.date <= CURRENT_DATE "
        "ORDER BY e.date DESC, e.distance_km DESC, e.name",
        (rider_id, RideStatus.FINISHED.value),
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
        "  AND (s.finish_time IS NULL OR s.finish_time = '' OR s.homologation_number IS NULL OR s.homologation_number = '') "
        "  AND r.rusa_id IS NOT NULL "
        "ORDER BY r.id, e.date",
        (RideStatus.FINISHED.value,),
    )


def set_signup_finish_time(signup_id, finish_time, homologation_number=None):
    """Write an official RUSA finish_time onto one finished sign-up (by row id).

    The SOLE real-value writer of finish_time — the self-service result endpoint only
    ever clears it. Re-asserts status = finished AND a still-empty finish_time, so a
    row that left finished, or was already filled, is never overwritten. Returns True
    when a row changed (RETURNING id, since db.execute yields the first row not a
    rowcount). rp_ tables only.
    """
    row = db.execute(
        "UPDATE rp_event_signup "
        "SET finish_time = COALESCE(NULLIF(finish_time, ''), %s), "
        "    homologation_number = COALESCE(NULLIF(homologation_number, ''), %s), updated_at = NOW() "
        "WHERE id = %s AND status = %s "
        "  AND (finish_time IS NULL OR finish_time = '' OR homologation_number IS NULL OR homologation_number = '') "
        "RETURNING id",
        (finish_time, homologation_number, signup_id, RideStatus.FINISHED.value),
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
# Event -> live-ride resolution (Closes #538). A calendar event resolves to an
# associated PUBLIC live ride so the calendar can render a per-event "Live" link
# pointing at the shared Radial view (/live/<ride_id>). Two tiers, in priority
# order and both PUBLIC-only (is_public = TRUE) so no private ride can ever leak:
#   1. the explicit FK link (rp_ride.event_id), set by the ride owner — the
#      authoritative path; and
#   2. a name+date fallback for a public ride that predates the FK (same date,
#      same normalized name) and is NOT explicitly linked elsewhere.
# On a tie the pick is deterministic (most-recently-started, then highest id), and
# only a bare ride id (never PII) is returned. Both tiers touch only rp_ride and
# rp_brevet_event.
# --------------------------------------------------------------------------- #

# Shared match clause both resolvers build on: a PUBLIC ride matches an event
# either by the explicit FK (event_id) OR, when it has no explicit link, by same-date +
# normalized-name equality. Restricting the fallback to event_id IS NULL means a
# ride explicitly linked to ANOTHER event can never be name-matched here, so the
# owner FK always wins. Ordering puts the FK tier first, then the deterministic
# tie-break, so DISTINCT ON / LIMIT 1 pick one stable ride per event.
_EVENT_LIVE_RIDE_MATCH = (
    "  ON r.is_public = TRUE AND ("
    "       r.event_id = e.id"
    "       OR (r.event_id IS NULL"
    "           AND lower(btrim(r.name)) = lower(btrim(e.name))"
    "           AND r.start_at::date = e.date)"
    "     ) "
)
_EVENT_LIVE_RIDE_ORDER = (
    "(r.event_id = e.id) DESC, r.start_at DESC NULLS LAST, r.id DESC "
)


def get_live_ride_ids_for_event(event_id):
    """The PUBLIC live ride ids associated with one calendar event, as an ordered
    ``[ride_id, ...]`` list (best match first).

    The event-scoped live view aggregates the rider rosters of EVERY public ride
    linked to a brevet, so this returns all matches, not just one: the explicit FK
    (rp_ride.event_id) first, then a public-ride name+date fallback, ordered so an
    explicitly-linked ride ranks ahead of a name-matched one. Only ride ids are
    returned — never any rider identity. Empty list when no PUBLIC ride is
    associated, so a future or quiet event resolves to an empty roster (the view
    still renders the route and a "waiting for riders" state). Touches only rp_ride
    and rp_brevet_event; never returns PII.
    """
    rows = db.query(
        "SELECT r.id AS ride_id "
        "FROM rp_brevet_event e JOIN rp_ride r "
        + _EVENT_LIVE_RIDE_MATCH +
        "WHERE e.id = %s "
        "ORDER BY " + _EVENT_LIVE_RIDE_ORDER,
        (event_id,),
    )
    return [row['ride_id'] for row in rows]


def get_followed_live_event_ids(rider_id):
    return {int(row['event_id']) for row in db.query(
        "SELECT event_id FROM rp_followed_live_event WHERE rider_id = %s",
        (rider_id,))}


def set_followed_live_event(rider_id, event_id, followed):
    if followed:
        db.execute(
            "INSERT INTO rp_followed_live_event (rider_id, event_id) VALUES (%s, %s) "
            "ON CONFLICT (rider_id, event_id) DO NOTHING",
            (rider_id, event_id),
        )
    else:
        db.execute("DELETE FROM rp_followed_live_event WHERE rider_id = %s AND event_id = %s",
                   (rider_id, event_id))
    return get_followed_live_event_ids(rider_id)


def get_followed_live_events(rider_id):
    """Events a rider follows, with any currently public live ride ids."""
    rows = db.query(
        "SELECT f.event_id, e.name, e.date, e.distance_km "
        "FROM rp_followed_live_event f JOIN rp_brevet_event e ON e.id=f.event_id "
        "ORDER BY e.date DESC, e.name",
        (rider_id,),
    )
    for row in rows:
        row['ride_ids'] = get_live_ride_ids_for_event(row['event_id'])
    return rows


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

    Guest-safety mirrors :func:`get_event_registered_riders`: the plan page is public, so this
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


def get_rider_ride_analyses(rider_id):
    """All cached ride analyses for one rider, newest cache first.

    Used by the reused Team Asha brevet-analysis index so analyzed brevet cards
    can still render when the Strava activity is older than the live activity
    picker's fetch window. Rider-scoped, and it reads only the existing analysis
    JSON plus the Strava activity id needed by the detail link.
    """
    return db.query(
        "SELECT strava_activity_id, analysis, computed_at "
        "FROM rp_ride_analysis WHERE rider_id = %s "
        "ORDER BY computed_at DESC NULLS LAST",
        (rider_id,),
    )


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


def save_ride_analysis_note(rider_id, strava_activity_id, scope, ident, note):
    """Persist a Team-Asha-template note inside the rider-owned analysis JSON.

    The reused Strava-analysis template can save an overall ride note, a planned
    segment note, or an unplanned-stop note. BrevetHub stores those in the existing
    rp_ride_analysis.analysis JSONB payload, scoped by rider/activity; no Team Asha
    table is touched.
    """
    row = get_ride_analysis(rider_id, strava_activity_id)
    if not row or not row.get('analysis'):
        return None

    analysis = dict(row['analysis'])
    notes = dict(analysis.get('notes') or {})
    notes.setdefault('segments', {})
    notes.setdefault('stops', {})

    text = (note or '').strip()[:2000]
    if scope == 'overall':
        if text:
            notes['overall'] = text
        else:
            notes.pop('overall', None)
    elif scope == 'segment':
        key = (ident or '').strip()[:200]
        if not key:
            return None
        if text:
            notes['segments'][key] = text
        else:
            notes['segments'].pop(key, None)
    elif scope == 'stop':
        key = (ident or '').strip()[:40]
        if not key:
            return None
        if text:
            notes['stops'][key] = text
        else:
            notes['stops'].pop(key, None)
    else:
        return None

    analysis['notes'] = notes
    db.execute(
        "UPDATE rp_ride_analysis SET analysis = %s "
        "WHERE rider_id = %s AND strava_activity_id = %s",
        (Json(analysis), rider_id, strava_activity_id),
    )
    return text


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


def get_brevet_route_plan_event_ids(event_ids, variant='conservative'):
    """Event ids that currently have a cached BrevetHub route plan.

    The Team Asha brevet-analysis index distinguishes the plain Strava view and
    the plan-vs-actual comparison. BrevetHub makes that decision by checking the
    rp_brevet_route_plan cache instead of assuming every finished event has a plan.
    """
    ids = [int(e) for e in (event_ids or []) if e is not None]
    if not ids:
        return set()
    rows = db.query(
        "SELECT event_id FROM rp_brevet_route_plan "
        "WHERE variant = %s AND event_id = ANY(%s)",
        (variant, ids),
    )
    return {row['event_id'] for row in rows}


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


def get_route_plan_operations_status():
    """Upcoming BrevetHub route and plan coverage, isolated to rp_* tables."""
    rows = db.query(
        "SELECT COUNT(*) AS upcoming_events, "
        "COUNT(*) FILTER (WHERE e.rwgps_url IS NULL) AS missing_routes, "
        "COUNT(*) FILTER (WHERE e.rwgps_url IS NOT NULL AND p.id IS NULL) "
        "  AS routes_missing_plans, "
        "COUNT(*) FILTER (WHERE p.id IS NOT NULL) AS plans_ready "
        "FROM rp_brevet_event e "
        "LEFT JOIN rp_brevet_route_plan p "
        "  ON p.event_id = e.id AND p.variant = 'conservative' "
        "WHERE e.date >= CURRENT_DATE"
    )
    return rows[0] if rows else None


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


def upsert_brevet_route_weather(event_id, forecast_date, weather_data,
                                sample_points, polyline=None,
                                elevation_track=None):
    """Insert or refresh one cached along-route forecast, keyed on
    (event_id, forecast_date).

    A single atomic upsert on the UNIQUE(event_id, forecast_date) constraint, so a
    repeated cron run refreshes the row in place (idempotent) instead of raising a
    unique-violation. ``weather_data`` is the raw Open-Meteo per-sample forecast list
    and ``sample_points`` is the aligned ``[{lat, lng, distance_m}]``; ``polyline`` is
    the decimated ``[[lat, lng], ...]`` route line for the Mapbox map; ``elevation_track``
    is the downsampled ``[{lat, lng, dist_m, e_m}, ...]`` route track for the rpv2
    gradient elevation profile (all optional — a caller that has no track points passes
    None and the read paths degrade: polyline falls back to sample_points, the
    elevation profile renders empty). All are JSON-adapted with psycopg2's ``Json``.
    Only ever called by the cron with a successful fetch, so a transient failure never
    overwrites a last-good row.

    (The literal is split at ``DO UPDATE`` / ``SET`` for the same rp-only-scanner
    reason documented on :func:`upsert_brevet_event`.)
    """
    db.execute(
        "INSERT INTO rp_brevet_route_weather "
        "  (event_id, forecast_date, weather_data, sample_points, polyline, "
        "   elevation_track) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (event_id, forecast_date) DO UPDATE "
        "SET weather_data = EXCLUDED.weather_data, "
        "    sample_points = EXCLUDED.sample_points, "
        "    polyline = EXCLUDED.polyline, "
        "    elevation_track = EXCLUDED.elevation_track, fetched_at = NOW()",
        (event_id, forecast_date, Json(weather_data), Json(sample_points),
         Json(polyline) if polyline is not None else None,
         Json(elevation_track) if elevation_track is not None else None),
    )


def get_brevet_route_weather(event_id, forecast_date):
    """The cached along-route forecast for a brevet on a date, or None.

    Returns ``{weather_data, sample_points, polyline, elevation_track, forecast_date,
    fetched_at}`` (the raw per-sample Open-Meteo list, the aligned sample points, the
    decimated route polyline for the Mapbox map, and the downsampled elevation track for
    the rpv2 gradient elevation profile) so the /plan route can map each stop to the
    nearest sample and compute per-stop wind in-process (shared/weather.py
    compute_stop_winds), draw the route line, and build the elevation profile — all from
    cache, no live fetch. ``polyline`` / ``elevation_track`` are NULL on rows warmed
    before they were cached — the polyline read path falls back to sample_points and the
    elevation profile renders empty. Returns None when nothing is stored (new route,
    beyond-horizon brevet, or the cron has not run yet), so the caller degrades
    gracefully with no live fallback. Touches only rp_brevet_route_weather.
    """
    return db.query_one(
        "SELECT event_id, forecast_date, weather_data, sample_points, polyline, "
        "       elevation_track, fetched_at "
        "FROM rp_brevet_route_weather WHERE event_id = %s AND forecast_date = %s",
        (event_id, forecast_date),
    )


def upsert_rp_route_geometry(route_id, elevation_track):
    """Insert or refresh one route cached elevation track (idempotent on route_id).

    Route geometry is date-invariant, so this is keyed on the RWGPS route id alone. Only
    called by the warm-plan-elevation cron with a successful fetch, so a transient RWGPS
    failure never overwrites a last-good row. The track is the downsampled
    [{lat, lng, dist_m, e_m}, ...] shared.live_radial output that build_elevation_profile
    consumes; None on a route with no usable points. Touches only rp_route_geometry_cache.

    (The literal is split at DO UPDATE / SET for the same rp-only-scanner reason
    documented on upsert_brevet_event.)
    """
    db.execute(
        "INSERT INTO rp_route_geometry_cache (route_id, elevation_track, fetched_at) "
        "VALUES (%s, %s, NOW()) "
        "ON CONFLICT (route_id) DO UPDATE "
        "SET elevation_track = EXCLUDED.elevation_track, fetched_at = NOW()",
        (route_id, Json(elevation_track) if elevation_track is not None else None),
    )


def get_rp_route_elevation_track(route_id):
    """The cron-warmed elevation track for a route, or None.

    Returns the [{lat, lng, dist_m, e_m}, ...] track cached in the route-keyed
    rp_route_geometry_cache (route geometry is date-invariant), for the rpv2 /plan
    gradient elevation profile to read from cache instead of fetching RWGPS live on the
    guest request path (the guest page NEVER fetches RWGPS live). The warm-plan-elevation
    cron populates it for every route referenced by an rp_brevet_route_plan, so any plan
    profile is served once warmed. None when the route has no cached track yet. Touches
    only rp_route_geometry_cache.
    """
    row = db.query_one(
        "SELECT elevation_track FROM rp_route_geometry_cache "
        "WHERE route_id = %s AND elevation_track IS NOT NULL",
        (route_id,),
    )
    return row['elevation_track'] if row else None


def get_rp_route_geometry_freshness(route_id):
    """The fetched_at of a route cached geometry, or None — for the cron fresh-skip.

    Only counts a row that actually has a track: a NULL-track row (a fetch that yielded
    no usable points) returns None so the cron re-warms it rather than pinning an empty
    profile for the whole freshness window. Touches only rp_route_geometry_cache.
    """
    row = db.query_one(
        "SELECT fetched_at FROM rp_route_geometry_cache "
        "WHERE route_id = %s AND elevation_track IS NOT NULL",
        (route_id,),
    )
    return row['fetched_at'] if row else None


def get_brevet_route_plan_route_ids():
    """Distinct RWGPS route references across every rp_brevet_route_plan.

    Returns ``[{rwgps_route_id, rwgps_url}, ...]`` so the warm-plan-elevation cron can
    enumerate every route that needs a cached elevation track (past and upcoming, not
    just the weather-warm window). Touches only rp_brevet_route_plan.
    """
    return db.query(
        "SELECT DISTINCT rwgps_route_id, rwgps_url FROM rp_brevet_route_plan"
    )


# --------------------------------------------------------------------------- #
# Organizer brevet validation (rp_validation_*) — private BrevetHub-only proof
# and advisory checks. These functions are used only behind operator_required.
# --------------------------------------------------------------------------- #
def get_validation_candidates():
    """Past registered riders/events for the operator's new-submission picker."""
    return db.query(
        "SELECT s.event_id, s.rider_id, e.name AS event_name, e.date, "
        "       e.distance_km, split_part(r.email, '@', 1) AS rider_name "
        "FROM rp_event_signup s "
        "JOIN rp_brevet_event e ON e.id = s.event_id "
        "JOIN rp_rider r ON r.id = s.rider_id "
        "WHERE e.date <= CURRENT_DATE AND s.status <> %s "
        "ORDER BY e.date DESC, e.name, rider_name",
        (RideStatus.WITHDRAW.value,),
    )


def create_validation_submission(*, event_id, rider_id, source_type,
                                 strava_activity_id=None, source_metadata=None,
                                 normalized_track=None, rider_explanation=None,
                                 submitted_by='operator'):
    return db.execute(
        "INSERT INTO rp_validation_submission "
        "  (event_id, rider_id, submitted_by, source_type, strava_activity_id, source_metadata, "
        "   normalized_track, rider_explanation) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (event_id, rider_id, submitted_by, source_type, strava_activity_id,
         Json(source_metadata or {}), Json(normalized_track or []), rider_explanation),
        returning=True,
    )


def add_validation_evidence(submission_id, *, evidence_kind, filename=None,
                            content_type=None, content=None, sha256=None,
                            control_order=None, control_orders=None,
                            description=None, captured_at=None):
    return db.execute(
        "INSERT INTO rp_validation_evidence "
        "  (submission_id, evidence_kind, control_order, original_filename, "
        "   control_orders, content_type, byte_size, sha256, captured_at, "
        "   description, private_content) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (submission_id, evidence_kind, control_order, filename, control_orders or [],
         content_type, len(content) if content is not None else None,
         sha256, captured_at,
         description, Binary(content) if content is not None else None),
        returning=True,
    )


def find_validation_evidence_conflicts(hashes, *, event_id, rider_id,
                                       strava_activity_id=None):
    hashes = [value for value in hashes if value]
    file_rows = db.query(
        "SELECT DISTINCT s.id AS submission_id, s.event_id, s.rider_id, e.sha256 "
        "FROM rp_validation_evidence e "
        "JOIN rp_validation_submission s ON s.id = e.submission_id "
        "WHERE e.sha256 = ANY(%s) AND (s.event_id <> %s OR s.rider_id <> %s)",
        (hashes, event_id, rider_id),
    ) if hashes else []
    activity_rows = db.query(
        "SELECT s.id AS submission_id, s.event_id, s.rider_id, "
        "       s.strava_activity_id::text AS sha256 "
        "FROM rp_validation_submission s "
        "WHERE s.strava_activity_id = %s AND (s.event_id <> %s OR s.rider_id <> %s)",
        (strava_activity_id, event_id, rider_id),
    ) if strava_activity_id else []
    return list(file_rows) + list(activity_rows)


def replace_validation_checks(submission_id, machine_decision, checks):
    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM rp_validation_check WHERE submission_id = %s", (submission_id,))
            for check in checks:
                cur.execute(
                    "INSERT INTO rp_validation_check "
                    "  (submission_id, check_code, result, title, summary, metrics, map_segments) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (submission_id, check.code, check.result, check.title, check.summary,
                     Json(check.metrics), Json(check.map_segments)),
                )
            cur.execute(
                "UPDATE rp_validation_submission "
                "SET machine_decision = %s, updated_at = NOW() WHERE id = %s",
                (machine_decision, submission_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def list_validation_submissions():
    return db.query(
        "SELECT s.id, s.machine_decision, s.organizer_decision, s.source_type, "
        "       s.created_at, e.name AS event_name, e.date, e.distance_km, "
        "       split_part(r.email, '@', 1) AS rider_name "
        "FROM rp_validation_submission s "
        "JOIN rp_brevet_event e ON e.id = s.event_id "
        "JOIN rp_rider r ON r.id = s.rider_id "
        "ORDER BY (s.organizer_decision IS NULL) DESC, s.created_at DESC"
    )


def get_validation_submission(submission_id):
    return db.query_one(
        "SELECT s.*, e.name AS event_name, e.date AS event_date, e.distance_km, "
        "       e.start_location, e.start_time, e.time_limit_hours, e.rwgps_url, "
        "       split_part(r.email, '@', 1) AS rider_name "
        "FROM rp_validation_submission s "
        "JOIN rp_brevet_event e ON e.id = s.event_id "
        "JOIN rp_rider r ON r.id = s.rider_id WHERE s.id = %s",
        (submission_id,),
    )


def get_validation_checks(submission_id):
    return db.query(
        "SELECT check_code, result, title, summary, metrics, map_segments "
        "FROM rp_validation_check WHERE submission_id = %s ORDER BY id",
        (submission_id,),
    )


def get_validation_evidence(submission_id):
    """Evidence metadata only; never expose private_content to a template."""
    return db.query(
        "SELECT id, evidence_kind, control_order, control_orders, original_filename, content_type, "
        "       byte_size, sha256, captured_at, description, created_at "
        "FROM rp_validation_evidence WHERE submission_id = %s ORDER BY id",
        (submission_id,),
    )


def get_validation_evidence_content(submission_id, evidence_id):
    """One private evidence blob, double-scoped to its submission for admin download."""
    return db.query_one(
        "SELECT id, original_filename, content_type, private_content "
        "FROM rp_validation_evidence WHERE submission_id = %s AND id = %s",
        (submission_id, evidence_id),
    )


def set_validation_organizer_decision(submission_id, decision, notes, reviewed_by='operator'):
    return db.execute(
        "UPDATE rp_validation_submission SET organizer_decision = %s, "
        "organizer_notes = %s, reviewed_by = %s, reviewed_at = NOW(), updated_at = NOW() "
        "WHERE id = %s RETURNING id",
        (decision, notes, reviewed_by, submission_id),
        returning=True,
    )


# --------------------------------------------------------------------------- #
# Brevet registration (profile, waivers, confirmation) — rp_* only.
# --------------------------------------------------------------------------- #
def get_club_by_rusa_code(rusa_club_id):
    return db.query_one(
        "SELECT id, rusa_club_id, name, city, state FROM rp_club "
        "WHERE rusa_club_id = %s",
        (rusa_club_id,),
    )


def update_rider_registration_profile(rider_id, **fields):
    """Update editable registration profile fields for the signed-in rider."""
    allowed = (
        'first_name', 'last_name', 'phone', 'city',
        'emergency_name', 'emergency_phone', 'sfr_member_year', 'rusa_id', 'club_id',
    )
    sets = []
    params = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = %s")
        params.append(value)
    if not sets:
        return get_rider_by_id(rider_id)
    params.append(rider_id)
    return db.execute(
        f"UPDATE rp_rider SET {', '.join(sets)} WHERE id = %s "
        f"RETURNING {_RIDER_PROFILE_COLS}",
        tuple(params),
        returning=True,
    )


def get_brevet_event_registration(event_id):
    """Full event row for the registration/roster admin view."""
    return db.query_one(
        "SELECT e.id, e.rusa_route_id, e.name, e.date, e.distance_km, e.region, "
        "       e.ride_type, e.elevation_ft, e.rwgps_url, e.start_location, "
        "       e.start_time, e.time_limit_hours, e.club_id, "
        "       e.fee_cents, e.registration_deadline, e.capacity, e.event_summary, "
        "       e.registration_enabled, e.volunteer_enabled, e.closed_at, "
        "       c.name AS club_name, c.rusa_club_id "
        "FROM rp_brevet_event e LEFT JOIN rp_club c ON c.id = e.club_id "
        "WHERE e.id = %s",
        (event_id,),
    )


def get_event_registration_count(event_id):
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM rp_event_signup "
        "WHERE event_id = %s AND registration_status = 'confirmed'",
        (event_id,),
    )
    return int(row['n']) if row else 0


def get_event_signup_registration(rider_id, event_id):
    return db.query_one(
        "SELECT id, status, registration_status, registration_confirmed_at, "
        "       exception_reason, confirmation_code "
        "FROM rp_event_signup WHERE rider_id = %s AND event_id = %s",
        (rider_id, event_id),
    )


def get_rider_signup_registrations(rider_id):
    return db.query(
        "SELECT event_id, status, registration_status, registration_confirmed_at, "
        "       exception_reason, confirmation_code "
        "FROM rp_event_signup WHERE rider_id = %s",
        (rider_id,),
    )


def get_waiver_for_event(event):
    """Latest waiver for the event's club, falling back to the global default."""
    club_id = (event or {}).get('club_id')
    if club_id:
        row = db.query_one(
            "SELECT id, version_label, waiver_text, club_id "
            "FROM rp_waiver_version WHERE club_id = %s "
            "ORDER BY effective_at DESC, id DESC LIMIT 1",
            (club_id,),
        )
        if row:
            return row
    return db.query_one(
        "SELECT id, version_label, waiver_text, club_id "
        "FROM rp_waiver_version WHERE club_id IS NULL "
        "ORDER BY effective_at DESC, id DESC LIMIT 1",
    )


def record_waiver_acceptance(event_id, rider_id, waiver_version_id, profile_snapshot):
    return db.execute(
        "INSERT INTO rp_waiver_acceptance "
        "  (event_id, rider_id, waiver_version_id, profile_snapshot) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (event_id, rider_id, waiver_version_id) DO UPDATE "
        "SET accepted_at = NOW(), profile_snapshot = EXCLUDED.profile_snapshot "
        "RETURNING id",
        (event_id, rider_id, waiver_version_id, Json(profile_snapshot)),
        returning=True,
    )


def confirm_event_registration(rider_id, event_id, *, registration_status,
                               exception_reason=None, confirmation_code=None):
    """Mark a rider registered; registered status only when confirmed, else interested."""
    ride_status = (RideStatus.REGISTERED.value if registration_status == 'confirmed'
                   else RideStatus.INTERESTED.value)
    return db.execute(
        "INSERT INTO rp_event_signup "
        "  (rider_id, event_id, status, registration_status, "
        "   registration_confirmed_at, exception_reason, confirmation_code) "
        "VALUES (%s, %s, %s, %s, NOW(), %s, %s) "
        "ON CONFLICT (event_id, rider_id) DO UPDATE "
        "SET status = EXCLUDED.status, "
        "    registration_status = EXCLUDED.registration_status, "
        "    registration_confirmed_at = NOW(), "
        "    exception_reason = EXCLUDED.exception_reason, "
        "    confirmation_code = EXCLUDED.confirmation_code, "
        "    updated_at = NOW() "
        "WHERE rp_event_signup.status NOT IN (%s, %s, %s, %s) "
        "RETURNING id, status, registration_status, confirmation_code",
        (rider_id, event_id, ride_status, registration_status,
         exception_reason, confirmation_code,
         RideStatus.FINISHED.value, RideStatus.DNF.value,
         RideStatus.DNS.value, RideStatus.OTL.value),
        returning=True,
    )


def enrich_brevet_event_registration(event_id, **fields):
    """Merge registration metadata onto a cached brevet (SFR sheet / admin)."""
    allowed = (
        'start_time', 'start_location', 'fee_cents', 'registration_deadline',
        'capacity', 'event_summary', 'registration_enabled', 'volunteer_enabled',
        'club_id',
        'elevation_ft', 'rwgps_url', 'time_limit_hours',
    )
    sets = []
    params = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = %s")
        params.append(value)
    if not sets:
        return None
    params.append(event_id)
    return db.execute(
        "UPDATE rp_brevet_event SET " + ", ".join(sets) + " WHERE id = %s RETURNING id",
        tuple(params),
        returning=True,
    )


def find_brevet_event_by_key(date_value, name, distance_km):
    return db.query_one(
        "SELECT id FROM rp_brevet_event "
        "WHERE date = %s AND name = %s AND distance_km = %s",
        (date_value, name, distance_km),
    )


def list_registration_exceptions(limit=100, club_id=None, region_prefix=None):
    """Registration exceptions, optionally scoped to a club or region prefix."""
    params = []
    extra = ""
    if region_prefix:
        extra = "AND e.region = %s "
        params.append(region_prefix)
    elif club_id is not None:
        extra = "AND e.club_id = %s "
        params.append(club_id)
    params.append(limit)
    return db.query(
        "SELECT s.id, s.event_id, s.rider_id, s.status, s.registration_status, "
        "       s.exception_reason, s.registration_confirmed_at, s.confirmation_code, "
        "       e.name AS event_name, e.date AS event_date, e.distance_km, "
        "       r.first_name, r.last_name, r.email, r.rusa_id "
        "FROM rp_event_signup s "
        "JOIN rp_brevet_event e ON e.id = s.event_id "
        "JOIN rp_rider r ON r.id = s.rider_id "
        "WHERE s.registration_status = 'exception' " + extra +
        "ORDER BY s.registration_confirmed_at DESC NULLS LAST, s.id DESC "
        "LIMIT %s",
        tuple(params),
    )


def list_event_registrations(event_id):
    return db.query(
        "SELECT s.id, s.status, s.registration_status, s.registration_confirmed_at, "
        "       s.confirmation_code, s.exception_reason, "
        "       r.first_name, r.last_name, r.email, r.rusa_id, r.phone "
        "FROM rp_event_signup s JOIN rp_rider r ON r.id = s.rider_id "
        "WHERE s.event_id = %s AND s.registration_status IS NOT NULL "
        "ORDER BY s.registration_confirmed_at DESC NULLS LAST, r.last_name, r.first_name",
        (event_id,),
    )


def enable_sfr_region_registration_defaults():
    """Turn on registration for upcoming SFR-region events with sensible fee defaults."""
    db.execute(
        "UPDATE rp_brevet_event SET registration_enabled = TRUE, "
        "  fee_cents = COALESCE(fee_cents, CASE "
        "    WHEN distance_km <= 100 THEN 1500 "
        "    WHEN distance_km <= 130 THEN 2000 "
        "    ELSE 2500 END) "
        "WHERE region ILIKE 'CA: San Francisco%' AND date >= CURRENT_DATE",
    )
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM rp_brevet_event "
        "WHERE region ILIKE 'CA: San Francisco%' AND date >= CURRENT_DATE "
        "AND registration_enabled = TRUE",
    )
    return int(row['n']) if row else 0


def list_registration_events(limit=80, club_id=None, region_prefix=None):
    """Upcoming and recent events with registration/roster counts for admin.

    When club_id or region_prefix is supplied only that club's events are returned.
    """
    params = [RideStatus.REGISTERED.value, RideStatus.FINISHED.value, RideStatus.DNF.value,
              RideStatus.DNS.value, RideStatus.OTL.value]
    club_filter = ""
    if region_prefix:
        club_filter = "AND e.region = %s "
        params.append(region_prefix)
    elif club_id is not None:
        club_filter = "AND e.club_id = %s "
        params.append(club_id)
    params.append(limit)
    return db.query(
        "SELECT e.id, e.name, e.date, e.distance_km, e.region, e.start_time, "
        "       e.start_location, e.registration_enabled, e.volunteer_enabled, "
        "       COUNT(s.id) FILTER (WHERE s.registration_status IS NOT NULL) AS roster_count, "
        "       COUNT(s.id) FILTER (WHERE s.status = %s) AS registered_count, "
        "       COUNT(s.id) FILTER (WHERE s.registration_status = 'exception') AS exception_count, "
        "       COUNT(s.id) FILTER (WHERE s.status IN (%s, %s, %s, %s)) AS result_count "
        "FROM rp_brevet_event e "
        "LEFT JOIN rp_event_signup s ON s.event_id = e.id "
        "WHERE e.date >= CURRENT_DATE - INTERVAL '14 days' " + club_filter +
        "GROUP BY e.id "
        "ORDER BY e.date ASC, e.distance_km ASC "
        "LIMIT %s",
        tuple(params),
    )


def get_admin_events(include_past=False):
    """All events with signup/result counts for the admin events view.

    When include_past is False only events from today forward are returned.
    When True all historical events are included as well, newest-past first
    within the past bucket and soonest-first within future/current.
    Returns a single flat list; the caller is responsible for splitting into
    this-week / upcoming / past buckets using the ``date`` field.
    """
    date_filter = "" if include_past else "WHERE e.date >= CURRENT_DATE "
    return db.query(
        "SELECT e.id, e.name, e.date, e.distance_km, e.region, e.start_time, "
        "       e.start_location, e.registration_enabled, e.volunteer_enabled, e.closed_at, e.club_id, "
        "       c.name AS club_name, "
        "       COUNT(s.id) FILTER (WHERE s.registration_status IS NOT NULL) AS roster_count, "
        "       COUNT(s.id) FILTER (WHERE s.status = %s) AS registered_count, "
        "       COUNT(s.id) FILTER (WHERE s.registration_status = 'exception') AS exception_count, "
        "       COUNT(s.id) FILTER (WHERE s.status IN (%s, %s, %s, %s)) AS result_count, "
        "       COUNT(s.id) AS total_count "
        "FROM rp_brevet_event e "
        "LEFT JOIN rp_club c ON c.id = e.club_id "
        "LEFT JOIN rp_event_signup s ON s.event_id = e.id "
        + date_filter +
        "GROUP BY e.id, c.name "
        "ORDER BY e.date ASC, e.distance_km ASC",
        (RideStatus.REGISTERED.value, RideStatus.FINISHED.value, RideStatus.DNF.value,
         RideStatus.DNS.value, RideStatus.OTL.value),
    )


def get_club_admin_by_username(username):
    """Look up a club admin by username for login verification.

    Returns the full row including password_hash, club_id, and region_prefix,
    or None when the username does not exist or the account is inactive. The
    caller must verify the password with werkzeug.security.check_password_hash
    before trusting the row.
    """
    return db.query_one(
        "SELECT a.id, a.club_id, a.username, a.password_hash, a.display_name, "
        "       a.is_active, c.name AS club_name, c.rusa_club_id, c.region_prefix "
        "FROM rp_club_admin a "
        "JOIN rp_club c ON c.id = a.club_id "
        "WHERE a.username = %s AND a.is_active = TRUE",
        (username,),
    )


def record_club_admin_login(admin_id):
    """Stamp last_login_at for the admin row after a successful login."""
    db.execute(
        "UPDATE rp_club_admin SET last_login_at = NOW() WHERE id = %s",
        (admin_id,),
    )


def get_club_admin_events(club_id, include_past=False, region_prefix=None):
    """Events for a specific club with signup/result counts.

    When club_id is None (super-admin) all clubs are returned (delegates to
    get_admin_events). Otherwise events are matched by region_prefix (the RUSA
    feed region string, e.g. 'CA: San Francisco') since feed events have
    club_id = NULL. Falls back to club_id matching if region_prefix is absent.
    """
    if club_id is None:
        return get_admin_events(include_past=include_past)

    date_clause = "" if include_past else "AND e.date >= CURRENT_DATE "

    if region_prefix:
        where = "WHERE e.region = %s " + date_clause
        params = (region_prefix,)
    else:
        where = "WHERE e.club_id = %s " + date_clause
        params = (club_id,)

    return db.query(
        "SELECT e.id, e.name, e.date, e.distance_km, e.region, e.start_time, "
        "       e.start_location, e.registration_enabled, e.volunteer_enabled, e.closed_at, e.club_id, "
        "       c.name AS club_name, "
        "       COUNT(s.id) FILTER (WHERE s.registration_status IS NOT NULL) AS roster_count, "
        "       COUNT(s.id) FILTER (WHERE s.status = %s) AS registered_count, "
        "       COUNT(s.id) FILTER (WHERE s.registration_status = 'exception') AS exception_count, "
        "       COUNT(s.id) FILTER (WHERE s.status IN (%s, %s, %s, %s)) AS result_count, "
        "       COUNT(s.id) AS total_count "
        "FROM rp_brevet_event e "
        "LEFT JOIN rp_club c ON c.id = e.club_id "
        "LEFT JOIN rp_event_signup s ON s.event_id = e.id "
        + where +
        "GROUP BY e.id, c.name "
        "ORDER BY e.date ASC, e.distance_km ASC",
        (RideStatus.REGISTERED.value, RideStatus.FINISHED.value,
         RideStatus.DNF.value, RideStatus.DNS.value, RideStatus.OTL.value) + params,
    )


def list_club_admins(club_id):
    """All admin accounts for a given club (for the dashboard admin management UI)."""
    return db.query(
        "SELECT id, username, display_name, is_active, created_at, last_login_at "
        "FROM rp_club_admin WHERE club_id = %s ORDER BY username",
        (club_id,),
    )


def create_club_admin(club_id, username, password_hash, display_name=None):
    """Insert a new club admin row. Raises IntegrityError on duplicate username."""
    return db.execute(
        "INSERT INTO rp_club_admin (club_id, username, password_hash, display_name) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (club_id, username, password_hash, display_name),
        returning=True,
    )


def reactivate_club_admin(admin_id):
    """Re-enable a previously deactivated admin."""
    db.execute(
        "UPDATE rp_club_admin SET is_active = TRUE WHERE id = %s",
        (admin_id,),
    )


def deactivate_club_admin(admin_id):
    """Soft-delete: mark is_active = FALSE."""
    db.execute(
        "UPDATE rp_club_admin SET is_active = FALSE WHERE id = %s",
        (admin_id,),
    )


def update_club_admin_password(admin_id, password_hash):
    """Replace the password hash for an existing admin account."""
    db.execute(
        "UPDATE rp_club_admin SET password_hash = %s WHERE id = %s",
        (password_hash, admin_id),
    )


def get_club_admin_by_id(admin_id):
    """Single admin row by id — used to verify club ownership before mutations."""
    return db.query_one(
        "SELECT id, club_id, username, display_name, is_active "
        "FROM rp_club_admin WHERE id = %s",
        (admin_id,),
    )


def list_all_clubs_for_admin():
    """All clubs sorted by name — for the super-admin club picker."""
    return db.query(
        "SELECT id, name, rusa_club_id, state, region_prefix FROM rp_club ORDER BY name",
        (),
    )


def get_club_region_prefix(club_id):
    """Return the RUSA feed region label for a club, or None when unmapped."""
    row = db.query_one(
        "SELECT region_prefix FROM rp_club WHERE id = %s",
        (club_id,),
    )
    return (row or {}).get('region_prefix') or None


def list_all_club_admins():
    """All club admin accounts across all clubs — for the super-admin view."""
    return db.query(
        "SELECT a.id, a.club_id, a.username, a.display_name, a.is_active, "
        "       a.created_at, a.last_login_at, c.name AS club_name, c.rusa_club_id "
        "FROM rp_club_admin a "
        "JOIN rp_club c ON c.id = a.club_id "
        "ORDER BY c.name, a.username",
        (),
    )


def get_admin_event_roster(event_id):
    """Full signup roster for operator management (PII allowed).

    Each row includes the rider's most recent validation submission for this event
    (machine_decision, organizer_decision, submission_id) so the roster page can
    surface proof status inline without a separate round-trip.
    """
    return db.query(
        "SELECT s.id AS signup_id, s.rider_id, s.status, s.registration_status, "
        "       s.registration_confirmed_at, s.confirmation_code, s.exception_reason, "
        "       s.finish_time, s.updated_at, s.created_at, "
        "       s.homologation_number, s.evidence_submission_allowed, "
        "       r.first_name, r.last_name, r.email, r.rusa_id, r.phone, r.city, "
        "       (e.date < CURRENT_DATE) AS event_past, "
        "       (e.date = CURRENT_DATE) AS event_today, "
        "       live.id AS live_ride_id, live.is_public AS live_public, "
        "       val.id AS validation_id, val.machine_decision, val.organizer_decision, "
        "       val.organizer_notes, val.source_type AS validation_source "
        "FROM rp_event_signup s "
        "JOIN rp_rider r ON r.id = s.rider_id "
        "JOIN rp_brevet_event e ON e.id = s.event_id "
        "LEFT JOIN LATERAL ("
        "  SELECT id, is_public FROM rp_ride "
        "  WHERE rider_id = s.rider_id AND event_id = s.event_id "
        "  ORDER BY start_at DESC NULLS LAST LIMIT 1"
        ") live ON TRUE "
        "LEFT JOIN LATERAL ("
        "  SELECT id, machine_decision, organizer_decision, organizer_notes, source_type "
        "  FROM rp_validation_submission "
        "  WHERE rider_id = s.rider_id AND event_id = s.event_id "
        "  ORDER BY created_at DESC LIMIT 1"
        ") val ON TRUE "
        "WHERE s.event_id = %s "
        "  AND (s.registration_status IS NOT NULL "
        "       OR s.status IN (%s, %s, %s, %s, %s)) "
        "ORDER BY CASE s.status WHEN %s THEN 0 WHEN %s THEN 1 WHEN %s THEN 2 ELSE 3 END, "
        "         r.last_name, r.first_name",
        (event_id,
         RideStatus.WITHDRAWAL_REQUESTED.value,
         RideStatus.FINISHED.value, RideStatus.DNF.value,
         RideStatus.DNS.value, RideStatus.OTL.value,
         RideStatus.REGISTERED.value, RideStatus.INTERESTED.value, RideStatus.FINISHED.value),
    )


def get_event_close_blockers(event_id):
    """Riders on the roster whose status is not a final post-ride result.

    An event may only be closed when every roster rider is FINISHED, DNF, DNS, OTL,
    or WITHDRAW. withdrawal_requested also blocks close until resolved.
    """
    return db.query(
        "SELECT s.rider_id, s.status, s.registration_status, "
        "       r.email, r.first_name, r.last_name "
        "FROM rp_event_signup s "
        "JOIN rp_rider r ON r.id = s.rider_id "
        "WHERE s.event_id = %s "
        "  AND (s.registration_status IS NOT NULL "
        "       OR s.status = %s) "
        "  AND s.status NOT IN (%s, %s, %s, %s, %s) "
        "ORDER BY r.last_name, r.first_name",
        (event_id,
         RideStatus.WITHDRAWAL_REQUESTED.value,
         RideStatus.FINISHED.value, RideStatus.DNF.value,
         RideStatus.DNS.value, RideStatus.OTL.value, RideStatus.WITHDRAW.value),
    )


def set_event_closed(event_id, closed: bool):
    """Open (closed=False) or close (closed=True) an event for validation purposes.

    Returns 'closed', 'opened', or 'unresolved_riders' when close is blocked.
    """
    if closed:
        if get_event_close_blockers(event_id):
            return 'unresolved_riders'
        db.execute(
            "UPDATE rp_brevet_event SET closed_at = NOW() WHERE id = %s AND closed_at IS NULL",
            (event_id,),
        )
        return 'closed'
    db.execute(
        "UPDATE rp_brevet_event SET closed_at = NULL WHERE id = %s",
        (event_id,),
    )
    return 'opened'


def admin_update_event_signup(event_id, rider_id, status, *, finish_time=None,
                              registration_status=None):
    """Operator-only signup update — bypasses rider self-service guards."""
    new_status = RideStatus.normalize(status)
    sets = ["status = %s", "updated_at = NOW()"]
    params = [new_status.value]
    if registration_status is not None:
        sets.append("registration_status = %s")
        params.append(registration_status)
    if finish_time is not None:
        sets.append("finish_time = %s")
        params.append(finish_time)
    elif RideStatus.is_post_ride(new_status) and not RideStatus.is_successful(new_status):
        sets.append("finish_time = NULL")
    params.extend([event_id, rider_id])
    row = db.execute(
        "UPDATE rp_event_signup SET " + ", ".join(sets) + " "
        "WHERE event_id = %s AND rider_id = %s RETURNING id, status, finish_time, "
        "registration_status",
        tuple(params),
        returning=True,
    )
    return row


def admin_remove_event_signup(event_id, rider_id):
    return db.execute(
        "DELETE FROM rp_event_signup WHERE event_id = %s AND rider_id = %s RETURNING id",
        (event_id, rider_id),
        returning=True,
    )


# ── Team registration ─────────────────────────────────────────────────────────

def create_team_registration(event_id, captain_rider_id, team_name,
                              team_event_type=None, proof_method=None,
                              rwgps_url=None, notes=None):
    """Create a team registration record and return the new row id."""
    row = db.execute(
        "INSERT INTO rp_team_registration "
        "(event_id, captain_rider_id, team_name, team_event_type, "
        " proof_method, rwgps_url, notes) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (event_id, captain_rider_id, team_name, team_event_type,
         proof_method, rwgps_url, notes),
        returning=True,
    )
    return row['id'] if row else None


def add_team_member(team_registration_id, member_order,
                    rider_id=None, rusa_id=None,
                    first_name=None, last_name=None):
    row = db.execute(
        "INSERT INTO rp_team_member "
        "(team_registration_id, rider_id, rusa_id, first_name, last_name, member_order) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (team_registration_id, rider_id, rusa_id, first_name, last_name, member_order),
        returning=True,
    )
    return row['id'] if row else None


def get_team_registrations_for_event(event_id):
    return db.query(
        "SELECT tr.*, r.first_name AS captain_first, r.last_name AS captain_last, "
        "       r.email AS captain_email, r.rusa_id AS captain_rusa_id "
        "FROM rp_team_registration tr "
        "LEFT JOIN rp_rider r ON r.id = tr.captain_rider_id "
        "WHERE tr.event_id = %s ORDER BY tr.created_at",
        (event_id,),
    )


def get_team_members(team_registration_id):
    return db.query(
        "SELECT tm.*, r.email FROM rp_team_member tm "
        "LEFT JOIN rp_rider r ON r.id = tm.rider_id "
        "WHERE tm.team_registration_id = %s ORDER BY tm.member_order",
        (team_registration_id,),
    )


def get_rider_team_registration(rider_id, event_id):
    return db.query_one(
        "SELECT tr.* FROM rp_team_registration tr "
        "WHERE tr.captain_rider_id = %s AND tr.event_id = %s",
        (rider_id, event_id),
    )


# ── Enhanced waiver acceptance ────────────────────────────────────────────────

def record_waiver_acceptance_v2(event_id, rider_id, waiver_version_id,
                                 profile_snapshot, *, is_minor=False,
                                 signatory_name=None, guardian_name=None,
                                 guardian_phone=None, age_certified=False,
                                 esign_consented=False, ride_phone=None,
                                 waiver_method='in_app', smartwaiver_id=None,
                                 initials=None, waiver_signed_date=None):
    """Enhanced waiver acceptance with e-sig fields; upserts on (event_id, rider_id, waiver_version_id)."""
    snapshot = profile_snapshot if isinstance(profile_snapshot, str) else Json(profile_snapshot)
    return db.execute(
        "INSERT INTO rp_waiver_acceptance "
        "(event_id, rider_id, waiver_version_id, profile_snapshot, "
        " is_minor, signatory_name, guardian_name, guardian_phone, "
        " age_certified, esign_consented, waiver_method, smartwaiver_id, "
        " initials, waiver_signed_date) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (event_id, rider_id, waiver_version_id) DO UPDATE SET "
        "  is_minor = EXCLUDED.is_minor, "
        "  signatory_name = EXCLUDED.signatory_name, "
        "  guardian_name = EXCLUDED.guardian_name, "
        "  guardian_phone = EXCLUDED.guardian_phone, "
        "  age_certified = EXCLUDED.age_certified, "
        "  esign_consented = EXCLUDED.esign_consented, "
        "  waiver_method = EXCLUDED.waiver_method, "
        "  smartwaiver_id = EXCLUDED.smartwaiver_id, "
        "  initials = EXCLUDED.initials, "
        "  waiver_signed_date = EXCLUDED.waiver_signed_date, "
        "  accepted_at = NOW() "
        "RETURNING id",
        (event_id, rider_id, waiver_version_id, snapshot,
         is_minor, signatory_name, guardian_name, guardian_phone,
         age_certified, esign_consented, waiver_method, smartwaiver_id,
         initials, waiver_signed_date),
        returning=True,
    )


# ── Volunteer slots & signups ─────────────────────────────────────────────────

def count_volunteer_slots(event_id):
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM rp_volunteer_slot WHERE event_id = %s",
        (event_id,),
    )
    return int(row['n']) if row else 0


def get_volunteer_slots_for_event(event_id):
    """Slots for an event with confirmed signup counts."""
    return db.query(
        "SELECT s.id, s.event_id, s.role_name, s.description, s.capacity, "
        "       s.sort_order, s.created_at, "
        "       COALESCE(v.confirmed_count, 0) AS confirmed_count "
        "FROM rp_volunteer_slot s "
        "LEFT JOIN ("
        "  SELECT slot_id, COUNT(*) AS confirmed_count "
        "  FROM rp_volunteer_signup WHERE status = 'confirmed' "
        "  GROUP BY slot_id"
        ") v ON v.slot_id = s.id "
        "WHERE s.event_id = %s "
        "ORDER BY s.sort_order, s.id",
        (event_id,),
    )


def get_volunteer_slot(slot_id):
    return db.query_one(
        "SELECT id, event_id, role_name, description, capacity, sort_order, created_at "
        "FROM rp_volunteer_slot WHERE id = %s",
        (slot_id,),
    )


def create_volunteer_slot(event_id, role_name, *, description=None, capacity=1,
                          sort_order=0):
    role_name = (role_name or '').strip()
    if description is not None:
        description = (description or '').strip() or None
    row = db.execute(
        "INSERT INTO rp_volunteer_slot "
        "(event_id, role_name, description, capacity, sort_order) "
        "VALUES (%s, %s, %s, %s, %s) "
        "RETURNING id, event_id, role_name, description, capacity, sort_order",
        (event_id, role_name, description, capacity, sort_order),
        returning=True,
    )
    return row


def update_volunteer_slot(slot_id, *, role_name=None, description=None,
                          capacity=None, sort_order=None):
    if role_name is not None:
        role_name = (role_name or '').strip()
    if description is not None:
        description = (description or '').strip() or None
    sets = []
    params = []
    for key, value in (
        ('role_name', role_name),
        ('description', description),
        ('capacity', capacity),
        ('sort_order', sort_order),
    ):
        if value is not None:
            sets.append(f"{key} = %s")
            params.append(value)
    if not sets:
        return get_volunteer_slot(slot_id)
    params.append(slot_id)
    return db.execute(
        f"UPDATE rp_volunteer_slot SET {', '.join(sets)} WHERE id = %s "
        "RETURNING id, event_id, role_name, description, capacity, sort_order",
        tuple(params),
        returning=True,
    )


def delete_volunteer_slot(slot_id):
    return db.execute(
        "DELETE FROM rp_volunteer_slot WHERE id = %s RETURNING id",
        (slot_id,),
        returning=True,
    )


def set_event_volunteer_enabled(event_id, enabled):
    return db.execute(
        "UPDATE rp_brevet_event SET volunteer_enabled = %s WHERE id = %s RETURNING id",
        (bool(enabled), event_id),
        returning=True,
    )


def count_slot_confirmed_signups(slot_id):
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM rp_volunteer_signup "
        "WHERE slot_id = %s AND status = 'confirmed'",
        (slot_id,),
    )
    return int(row['n']) if row else 0


def get_volunteer_signup(signup_id):
    return db.query_one(
        "SELECT vs.id, vs.slot_id, vs.rider_id, vs.status, vs.signed_up_at, "
        "       vs.approved_at, vs.approved_by, vs.notes, "
        "       s.event_id, s.role_name "
        "FROM rp_volunteer_signup vs "
        "JOIN rp_volunteer_slot s ON s.id = vs.slot_id "
        "WHERE vs.id = %s",
        (signup_id,),
    )


def get_volunteer_signup_for_slot_rider(slot_id, rider_id):
    return db.query_one(
        "SELECT id, slot_id, rider_id, status, signed_up_at, approved_at, approved_by, notes "
        "FROM rp_volunteer_signup WHERE slot_id = %s AND rider_id = %s",
        (slot_id, rider_id),
    )


def get_rider_active_volunteer_signups(rider_id, event_id):
    """Non-withdrawn signups for a rider on one event."""
    return db.query(
        "SELECT vs.id, vs.slot_id, vs.status, vs.signed_up_at, "
        "       s.role_name, s.capacity "
        "FROM rp_volunteer_signup vs "
        "JOIN rp_volunteer_slot s ON s.id = vs.slot_id "
        "WHERE vs.rider_id = %s AND s.event_id = %s AND vs.status <> 'withdrawn' "
        "ORDER BY vs.signed_up_at",
        (rider_id, event_id),
    )


def get_rider_volunteer_signups_for_event(rider_id, event_id):
    rows = db.query(
        "SELECT vs.id, vs.slot_id, vs.status, vs.signed_up_at, vs.approved_at, "
        "       s.role_name, s.capacity, "
        "       COALESCE(v.confirmed_count, 0) AS confirmed_count "
        "FROM rp_volunteer_signup vs "
        "JOIN rp_volunteer_slot s ON s.id = vs.slot_id "
        "LEFT JOIN ("
        "  SELECT slot_id, COUNT(*) AS confirmed_count "
        "  FROM rp_volunteer_signup WHERE status = 'confirmed' "
        "  GROUP BY slot_id"
        ") v ON v.slot_id = s.id "
        "WHERE vs.rider_id = %s AND s.event_id = %s AND vs.status <> 'withdrawn' "
        "ORDER BY vs.signed_up_at",
        (rider_id, event_id),
    )
    result = []
    for row in rows:
        capacity = int(row.get('capacity') or 1)
        confirmed = int(row.get('confirmed_count') or 0)
        result.append({
            'id': row['id'],
            'slot_id': row['slot_id'],
            'status': row['status'],
            'role_name': row['role_name'],
            'signed_up_at': str(row['signed_up_at']) if row.get('signed_up_at') else None,
            'approved_at': str(row['approved_at']) if row.get('approved_at') else None,
        })
    return result


def get_rider_volunteer_signups_by_event(rider_id):
    """Map event_id -> list of active volunteer signups for calendar badges."""
    rows = db.query(
        "SELECT s.event_id, vs.id, vs.status, s.role_name "
        "FROM rp_volunteer_signup vs "
        "JOIN rp_volunteer_slot s ON s.id = vs.slot_id "
        "WHERE vs.rider_id = %s AND vs.status <> 'withdrawn' "
        "ORDER BY s.event_id, vs.signed_up_at",
        (rider_id,),
    )
    by_event = {}
    for row in rows:
        by_event.setdefault(row['event_id'], []).append({
            'id': row['id'],
            'status': row['status'],
            'role_name': row['role_name'],
        })
    return by_event


def get_volunteer_summaries_for_events(event_ids):
    """Public volunteer fill summary per event for calendar cards (no rider PII)."""
    if not event_ids:
        return {}
    rows = db.query(
        "SELECT s.event_id, s.role_name, s.capacity, "
        "       COALESCE(v.confirmed_count, 0) AS confirmed_count "
        "FROM rp_volunteer_slot s "
        "LEFT JOIN ("
        "  SELECT slot_id, COUNT(*) AS confirmed_count "
        "  FROM rp_volunteer_signup WHERE status = 'confirmed' "
        "  GROUP BY slot_id"
        ") v ON v.slot_id = s.id "
        "WHERE s.event_id = ANY(%s) "
        "ORDER BY s.event_id, s.sort_order, s.id",
        (list(event_ids),),
    )
    summaries = {}
    for row in rows:
        eid = row['event_id']
        summary = summaries.setdefault(eid, {
            'slot_count': 0,
            'capacity_total': 0,
            'confirmed_total': 0,
            'open_total': 0,
            'open_roles': [],
        })
        cap = int(row['capacity'] or 1)
        confirmed = int(row['confirmed_count'] or 0)
        available = max(0, cap - confirmed)
        summary['slot_count'] += 1
        summary['capacity_total'] += cap
        summary['confirmed_total'] += confirmed
        summary['open_total'] += available
        if available > 0:
            summary['open_roles'].append({
                'role_name': row['role_name'],
                'available': available,
            })
    return summaries


def upsert_volunteer_signup(slot_id, rider_id, *, status, approved_by=None):
    approved_sql = 'NOW()' if status == 'confirmed' else 'NULL'
    return db.execute(
        "INSERT INTO rp_volunteer_signup (slot_id, rider_id, status, approved_at, approved_by) "
        f"VALUES (%s, %s, %s, {approved_sql}, %s) "
        "ON CONFLICT (slot_id, rider_id) DO UPDATE "
        "SET status = EXCLUDED.status, "
        "    signed_up_at = NOW(), "
        f"    approved_at = {approved_sql}, "
        "    approved_by = EXCLUDED.approved_by "
        "RETURNING id, slot_id, rider_id, status, signed_up_at",
        (slot_id, rider_id, status, approved_by),
        returning=True,
    )


def set_volunteer_signup_status(signup_id, status, *, approved_by=None):
    sets = ["status = %s"]
    params = [status]
    if status == 'confirmed':
        sets.extend(["approved_at = NOW()", "approved_by = %s"])
        params.append(approved_by)
    elif status == 'withdrawn':
        sets.append("approved_at = NULL")
    params.append(signup_id)
    return db.execute(
        f"UPDATE rp_volunteer_signup SET {', '.join(sets)} WHERE id = %s "
        "RETURNING id, slot_id, rider_id, status",
        tuple(params),
        returning=True,
    )


def admin_remove_volunteer_signup(signup_id):
    return db.execute(
        "DELETE FROM rp_volunteer_signup WHERE id = %s RETURNING id",
        (signup_id,),
        returning=True,
    )


def get_admin_volunteer_roster(event_id):
    """All volunteer signups for operator review."""
    return db.query(
        "SELECT vs.id AS signup_id, vs.status, vs.signed_up_at, vs.approved_at, "
        "       vs.approved_by, vs.notes, "
        "       s.id AS slot_id, s.role_name, s.capacity, "
        "       r.id AS rider_id, r.first_name, r.last_name, r.email, r.phone, r.rusa_id "
        "FROM rp_volunteer_signup vs "
        "JOIN rp_volunteer_slot s ON s.id = vs.slot_id "
        "JOIN rp_rider r ON r.id = vs.rider_id "
        "WHERE s.event_id = %s AND vs.status <> 'withdrawn' "
        "ORDER BY s.sort_order, s.id, vs.signed_up_at",
        (event_id,),
    )
