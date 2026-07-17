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
    (interested or going) — an AGGREGATE only, so the guest calendar can show
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
        "  FROM rp_event_signup WHERE status IN (%s, %s) GROUP BY event_id"
        ") sc ON sc.event_id = e.id "
        "WHERE e.date >= CURRENT_DATE AND (%s::text IS NULL OR e.region ILIKE %s) "
        "ORDER BY e.date ASC, e.distance_km ASC LIMIT %s",
        (RideStatus.INTERESTED.value, RideStatus.GOING.value, state, like, limit),
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

    Returns None for an unknown event so the plan route can 404. Includes ``club_id``
    (the resolved owning club, NULL for national-feed events) so the club-admin route
    can verify a generator owns the event club before persisting the plan. Touches
    only rp_brevet_event.
    """
    return db.query_one(
        "SELECT id, rusa_route_id, name, date, distance_km, region, ride_type, "
        "       elevation_ft, rwgps_url, start_location, start_time, time_limit_hours, "
        "       club_id "
        "FROM rp_brevet_event WHERE id = %s",
        (event_id,),
    )


def get_rider_brevet_plan(rider_id, event_id):
    """The rider's saved pacing plan for a brevet, or None. One row per pair."""
    return db.query_one(
        "SELECT rider_id, event_id, target_speed_kmh, target_finish_min, plan_data "
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
def get_brevet_route_plan(event_id):
    """The persisted real ride plan for a brevet, or None. One row per event."""
    return db.query_one(
        "SELECT id, event_id, club_id, name, slug, total_distance_miles, "
        "       total_elevation_ft, rwgps_url, rwgps_route_id, distance_km, "
        "       cutoff_hours, start_time, avg_moving_speed, avg_elapsed_speed, "
        "       total_moving_time_min, total_elapsed_time_min, total_break_time_min, "
        "       overall_ft_per_mile, created_at "
        "FROM rp_brevet_route_plan WHERE event_id = %s",
        (event_id,),
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


def get_brevet_route_plan_with_stops(event_id):
    """The real plan for a brevet plus its ordered stops, or None when no real plan
    exists (so the /plan route falls back to the synthetic schedule).

    Returns ``{'plan': <row>, 'stops': [<row>, ...]}``.
    """
    plan = get_brevet_route_plan(event_id)
    if not plan:
        return None
    return {'plan': plan, 'stops': get_brevet_route_plan_stops(plan['id'])}


def upsert_brevet_route_plan(event_id, plan, stops, club_id=None):
    """Persist a real RWGPS-derived plan + its stops for a brevet, atomically.

    One transaction on the per-request connection (the per-call ``db.execute`` can't
    span the three statements): upsert the plan row keyed on the UNIQUE(event_id)
    constraint (so re-warming/re-generating the same brevet refreshes it in place —
    idempotent), delete the old stops, then re-insert the fresh ordered stops. Values
    are stored VERBATIM in the engine's native miles / mph / feet.

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
    # slug is UNIQUE; suffix with event_id so two brevets that share a route name
    # never collide, while staying deterministic (idempotent) for the same event.
    slug = f"{plan.get('slug') or 'route'}-{event_id}"

    conn = db.get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO rp_brevet_route_plan "
                "  (event_id, club_id, name, slug, total_distance_miles, "
                "   total_elevation_ft, rwgps_url, rwgps_route_id, distance_km, "
                "   cutoff_hours, start_time, avg_moving_speed, avg_elapsed_speed, "
                "   total_moving_time_min, total_elapsed_time_min, "
                "   total_break_time_min, overall_ft_per_mile) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (event_id) DO UPDATE "
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
                (event_id, club_id, plan.get('name'), slug,
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
    """Upcoming brevets with a non-NULL rwgps_url — the events the warm cron should
    pre-fetch and persist a real plan for.

    Returns ``[{id, rwgps_url}, ...]`` for events dated today or later that carry an
    RWGPS URL; events without one have no real route to build, so they are skipped
    (the calendar falls back to the synthetic schedule). Touches only rp_brevet_event.
    """
    return db.query(
        "SELECT id, rwgps_url FROM rp_brevet_event "
        "WHERE date >= CURRENT_DATE AND rwgps_url IS NOT NULL "
        "ORDER BY date ASC",
    )
