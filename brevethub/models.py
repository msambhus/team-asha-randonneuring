"""BrevetHub data model — rp_* tables only.

Every SQL statement in this module targets a `rp_`-prefixed tenant table. The
app never reads or writes any Team Asha table; `tests/brevethub/test_rp_only.py`
scans this file and fails the build if a non-`rp_` table name ever appears.
"""
import secrets
from enum import Enum

from psycopg2.extras import Json

from brevethub import db


class RideStatus(str, Enum):
    """BrevetHub's own ride-status enum — defined here so BrevetHub shares no
    code with Team Asha's models. Kept as a str-Enum for direct SQL binding."""
    INTERESTED = 'interested'
    GOING = 'going'
    WITHDRAW = 'withdraw'
    FINISHED = 'finished'
    DNF = 'dnf'
    DNS = 'dns'
    OTL = 'otl'


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
    return db.query_one(
        "SELECT id, email, google_id, rusa_id, club_id, "
        "       profile_completed, rusa_id_duplicate, created_at, last_login_at "
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
        "       is_public FROM rp_ride WHERE id = %s",
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
    """The ride's position breadcrumbs (oldest→newest) for the live trail.

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

    ``recorded_at`` is an optional ISO-8601 string (as the rider's device reports
    it); when omitted the DB stamps NOW(). Owner enforcement is the route's job —
    this is the raw insert.
    """
    db.execute(
        "INSERT INTO rp_live_position (ride_id, rider_id, lat, lng, recorded_at) "
        "VALUES (%s, %s, %s, %s, COALESCE(%s::timestamptz, NOW()))",
        (ride_id, rider_id, lat, lng, recorded_at),
    )


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

    The calendar cache-TTL check reads this: None (or a stale value) triggers a
    re-scrape; a fresh value serves the cache without any HTTP.
    """
    row = db.query_one("SELECT MAX(scraped_at) AS latest FROM rp_brevet_event")
    return row['latest'] if row else None


def get_upcoming_events(state=None, limit=200):
    """Upcoming brevets (date >= today), soonest first.

    ``state`` optionally narrows to one US state by matching the RUSA region
    label ``"<STATE>: ..."`` prefix — an honest, documented narrowing a generic
    multi-club app can do without the Team Asha hardcoded region->club map. None
    returns every upcoming brevet (the general RUSA calendar).
    """
    like = (state + ': %') if state else None
    return db.query(
        "SELECT id, rusa_route_id, name, date, distance_km, region, ride_type, "
        "       elevation_ft, rwgps_url, start_location, start_time, time_limit_hours "
        "FROM rp_brevet_event "
        "WHERE date >= CURRENT_DATE AND (%s::text IS NULL OR region ILIKE %s) "
        "ORDER BY date ASC, distance_km ASC LIMIT %s",
        (state, like, limit),
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
    """Create or transition a rider sign-up on an event (one row per pair).

    A single atomic INSERT ... ON CONFLICT ... DO UPDATE keyed on the
    UNIQUE(event_id, rider_id) constraint: the calendar UI POSTs several status
    buttons to this same endpoint, so rapid/duplicate requests for one rider-event
    pair can race — the conflict target makes them transition the status cleanly
    (last write wins) instead of one hitting a unique-violation and returning a 500.

    (The literal is split at ``DO UPDATE`` / ``SET`` for the same rp-only-scanner
    reason documented on :func:`upsert_brevet_event`.)
    """
    db.execute(
        "INSERT INTO rp_event_signup (rider_id, event_id, status) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (event_id, rider_id) DO UPDATE "
        "SET status = EXCLUDED.status, updated_at = NOW()",
        (rider_id, event_id, status),
    )


def get_rider_signups(rider_id):
    """The active upcoming sign-ups (interested/going) for a rider, joined to the event,
    soonest first — for the dashboard "My upcoming sign-ups" section. WITHDRAW
    rows are excluded so a withdrawn brevet drops off the list."""
    return db.query(
        "SELECT s.event_id, s.status, e.name, e.date, e.distance_km, e.region, "
        "       e.start_location, e.start_time "
        "FROM rp_event_signup s "
        "JOIN rp_brevet_event e ON e.id = s.event_id "
        "WHERE s.rider_id = %s AND s.status IN (%s, %s) AND e.date >= CURRENT_DATE "
        "ORDER BY e.date ASC, e.distance_km ASC",
        (rider_id, RideStatus.INTERESTED.value, RideStatus.GOING.value),
    )
