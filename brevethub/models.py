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
# Public rides (rp_ride) — guest/spectator browse of rides opted into public
# tracking. The full live-position ingestion is a follow-on mission; this is the
# read-only shell the guest browse view renders.
# --------------------------------------------------------------------------- #
def get_public_rides():
    return db.query(
        "SELECT id, club_id, name, distance_km, start_at, is_public, status "
        "FROM rp_ride WHERE is_public = TRUE ORDER BY start_at DESC"
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
