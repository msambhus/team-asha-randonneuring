"""BrevetHub data model — rp_* tables only.

Every SQL statement in this module targets a `rp_`-prefixed tenant table. The
app never reads or writes any Team Asha table; `tests/brevethub/test_rp_only.py`
scans this file and fails the build if a non-`rp_` table name ever appears.
"""
from enum import Enum

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
