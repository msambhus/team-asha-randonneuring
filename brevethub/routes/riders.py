"""BrevetHub community surfaces — club-scoped, read-only rider pages.

Login-gated surfaces that turn BrevetHub from self-profile-only into a real
multi-rider club, each scoped to the signed-in viewer's OWN club:

- ``/riders/<rusa_id>``           a same-club rider's public profile (access-gated)
- ``/riders``                     the club rider directory (searchable by display name)
- ``/riders/leaderboard``         career km leaderboard for the club
- ``/riders/season/<name>``       the club roster for one randonneuring season

Tenant isolation is the load-bearing property: every rider query is parameterized
by the *viewer's* ``club_id`` (from the session-resolved current rider), never by a
user-supplied value, so a rider in another club can never appear in a directory,
leaderboard, or roster, and a cross-club profile 404s. Career numbers reuse the
exact engine ``/profile`` uses (``shared.seasons`` over the cached RUSA history),
so nothing here recomputes randonneuring rules. Only a rider's display name (the
email local-part), RUSA id, and derived career numbers ever cross into a template —
never a full email address or ``google_id``.
"""
from datetime import date

from flask import Blueprint, abort, render_template, request

from brevethub import models
from brevethub.decorators import current_rider, login_required
from shared import seasons

riders_bp = Blueprint('riders', __name__)


def _display_name(email):
    """The public display name for a rider: the local-part of the email, matching
    the self-profile page. The full address and google_id are never exposed to
    another rider."""
    return (email or '').split('@')[0]


def _career_row(rider_row, today):
    """Build the club-safe view model for one rider from the cached RUSA history.

    Only the display name, RUSA id, and derived career numbers cross into a
    template — the raw email and google_id never do. The career numbers come from
    ``seasons.career_summary`` (the same engine the self-profile page uses).
    """
    brevets = rider_row.get('rusa_cache') or []
    career = seasons.career_summary(brevets, today)
    return {
        'rusa_id': rider_row.get('rusa_id'),
        'display_name': _display_name(rider_row.get('email')),
        'total_km': career['total_km'],
        'count': career['count'],
        'sr_count': len(career['sr_seasons']),
        'career': career,
    }


@riders_bp.route('/riders')
@login_required
def directory():
    """Searchable directory of the viewer's club riders. A club-less viewer gets a
    graceful join-a-club state (never another club's riders)."""
    viewer = current_rider()
    club = models.get_club(viewer['club_id']) if viewer.get('club_id') else None
    q = (request.args.get('q') or '').strip()

    riders = []
    if viewer.get('club_id'):
        today = date.today()
        rows = models.get_club_riders_with_rusa(viewer['club_id'])
        riders = [_career_row(r, today) for r in rows]
        if q:
            needle = q.lower()
            riders = [r for r in riders if needle in r['display_name'].lower()]
        riders.sort(key=lambda r: r['display_name'].lower())

    return render_template('riders_directory.html', club=club, riders=riders,
                           q=q, has_club=bool(viewer.get('club_id')))


@riders_bp.route('/riders/leaderboard')
@login_required
def leaderboard():
    """The viewer's club riders ranked by career km descending, with a
    deterministic tie-break (display name ascending)."""
    viewer = current_rider()
    club = models.get_club(viewer['club_id']) if viewer.get('club_id') else None

    riders = []
    if viewer.get('club_id'):
        today = date.today()
        rows = models.get_club_riders_with_rusa(viewer['club_id'])
        riders = [_career_row(r, today) for r in rows]
        riders.sort(key=lambda r: (-r['total_km'], r['display_name'].lower()))

    return render_template('career_leaderboard.html', club=club, riders=riders,
                           has_club=bool(viewer.get('club_id')))


@riders_bp.route('/riders/<rusa_id>')
@login_required
def rider_profile(rusa_id):
    """A same-club rider's public profile: hero + career stat cards + season-by-season
    brevet history. Access gate: same club → 200; other club → 404; a rider viewing
    their own record → 200 (even when club-less); anonymous → login redirect (the
    decorator). No full email or google_id ever reaches the rendered page.
    """
    viewer = current_rider()
    today = date.today()
    rusa_id = str(rusa_id)

    target = None
    if viewer.get('club_id'):
        target = models.get_club_rider_by_rusa(viewer['club_id'], rusa_id)
    is_self = (viewer.get('rusa_id') is not None
               and str(viewer['rusa_id']) == rusa_id)
    if target is None and is_self:
        # Self-view fallback: a rider can always see their own record, even before
        # joining a club. current_rider() carries no rusa_cache, so read it here.
        cache = models.get_rider_rusa_cache(viewer['id']) or {}
        target = {
            'email': viewer['email'],
            'rusa_id': viewer['rusa_id'],
            'rusa_cache': cache.get('rusa_cache'),
        }
    if target is None:
        abort(404)

    brevets = target.get('rusa_cache') or []
    career = seasons.career_summary(brevets, today)
    season_groups = seasons.seasons_with_summaries(brevets, today)

    return render_template('rider_profile.html',
                           display_name=_display_name(target.get('email')),
                           rusa_id=target.get('rusa_id'),
                           career=career, seasons=season_groups,
                           is_self=is_self)
