"""BrevetHub public shell + rider dashboard.

- `/`             neutral landing page with the "Sign in with Google" entry point.
- `/dashboard`    a signed-in rider's home (profile required): RUSA brevet history
                  + stats and the Strava activity summary.
- `/rusa/refresh` forces a fresh RUSA scrape, bypassing the cache TTL.

RUSA scraping reuses `shared.rusa` (the same logic Team Asha uses) and is cached
on `rp_rider` (rusa_cache JSONB + rusa_fetched_at); the Strava summary lives in
`routes/strava.py`. Every external call degrades gracefully — a RUSA/Strava
outage shows a message, never a 500. The guest/spectator public-ride browse now
ships in `routes/live.py` (the public `/live` list + per-ride map); the dashboard
links into it.
"""
from datetime import date, datetime, timedelta, timezone

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   url_for)

from brevethub import models, rusa_stats
from brevethub.decorators import current_rider, profile_required
from brevethub.routes.strava import load_strava_section
from shared import seasons
from shared.activity_feed import build_activity_calendar, build_private_activity_feed
from shared.rusa import fetch_rider_results

main_bp = Blueprint('main', __name__)

# Re-scrape RUSA at most once a week per rider; a manual refresh overrides this.
RUSA_CACHE_TTL = timedelta(days=7)


def load_rusa_section(rider, force_refresh=False):
    """Assemble the dashboard RUSA section for a rider (cache-aware, failure-tolerant).

    Returns a dict the template renders directly:
      - no RUSA ID:  {'has_id': False}
      - has RUSA ID: {'has_id': True, 'rusa_id', 'brevets', 'stats', 'fetched_at', 'error'}
    """
    rusa_id = rider.get('rusa_id')
    if not rusa_id:
        return {'has_id': False}

    cache_row = models.get_rider_rusa_cache(rider['id'])
    cached = cache_row.get('rusa_cache') if cache_row else None
    fetched_at = cache_row.get('rusa_fetched_at') if cache_row else None
    fresh = (fetched_at is not None
             and (datetime.now(timezone.utc) - fetched_at) < RUSA_CACHE_TTL)

    error = None
    if force_refresh or not fresh or cached is None:
        try:
            brevets = rusa_stats.normalize_results(fetch_rider_results(rusa_id))
            # The scraper conflates a transient RUSA outage with a genuinely
            # empty result — both come back as []. So an empty scrape is only
            # ever treated as "fresh data" when it is non-empty. Never overwrite
            # a good cache with an empty scrape, and always surface a message so a
            # forced/stale refresh that got nothing is not reported as success.
            if brevets:
                models.update_rider_rusa_cache(rider['id'], brevets)
                cached = brevets
                fetched_at = datetime.now(timezone.utc)
            elif cached:
                error = ('Could not fetch fresh RUSA results just now — showing '
                         'your cached history.')
            else:
                cached = []
                error = ('No RUSA results found — RUSA may be temporarily '
                         'unavailable, or this ID has no completed brevets yet.')
        except Exception as e:
            current_app.logger.warning('RUSA scrape failed for rider %s: %s', rider['id'], e)
            if cached is None:
                cached = []
            error = 'Could not reach RUSA right now. Showing cached results if available.'

    brevets = cached or []
    return {
        'has_id': True,
        'rusa_id': rusa_id,
        'brevets': brevets,
        'stats': rusa_stats.compute_stats(brevets) if brevets else None,
        'fetched_at': fetched_at,
        'error': error,
    }


@main_bp.route('/')
def landing():
    """De-branded landing. Signed-in riders go straight to their dashboard."""
    if current_rider():
        return redirect(url_for('main.dashboard'))
    return render_template('landing.html')


def load_signups(rider):
    """The rider's upcoming brevet sign-ups (interested/going) for the dashboard.

    Failure-tolerant like the RUSA/Strava sections: a DB hiccup returns [] and logs
    a warning rather than 500-ing the whole dashboard over a secondary section.
    """
    try:
        return models.get_rider_signups(rider['id'])
    except Exception as e:
        current_app.logger.warning('sign-up load failed for rider %s: %s', rider['id'], e)
        return []


def load_past_results(rider):
    """The rider's past-event results (finished/dnf/dns/otl) for the dashboard.

    Failure-tolerant like the sign-up/RUSA/Strava sections: a DB hiccup returns []
    and logs a warning rather than 500-ing the whole dashboard over a secondary card.
    """
    try:
        return models.get_rider_past_results(rider['id'])
    except Exception as e:
        current_app.logger.warning('past-result load failed for rider %s: %s', rider['id'], e)
        return []


@main_bp.route('/dashboard')
@profile_required
def dashboard():
    rider = current_rider()
    club = models.get_club(rider['club_id']) if rider.get('club_id') else None
    rusa = load_rusa_section(rider)
    signups = load_signups(rider)
    past_results = load_past_results(rider)
    return render_template('dashboard.html', rider=rider, club=club,
                           rusa=rusa, signups=signups, past_results=past_results)


@main_bp.route('/profile')
@profile_required
def profile():
    """The signed-in rider's own private profile: identity, Strava status, and a
    career summary (km, brevet count, current-season SR, R-12) computed from the
    authoritative RUSA history only. Login-required and self-scoped — there is no
    rider-id parameter, so a rider can only ever load their own row (no PII leak
    to other riders).
    """
    rider = current_rider()
    club = models.get_club(rider['club_id']) if rider.get('club_id') else None
    rusa = load_rusa_section(rider)
    strava = load_strava_section(rider)
    activity_feed = build_private_activity_feed(
        strava_activities=(strava.get('stats') or {}).get('activities') or [])
    activity_calendar = build_activity_calendar(activity_feed[:60], date.today())
    # Career/SR/R-12 come from the RUSA cache only (the official record), so a
    # self-logged rp_ride can never inflate them. seasons.career_summary is total
    # for an empty history, giving a graceful zero-state for a RUSA-less rider.
    brevets = rusa.get('brevets') or []
    career = seasons.career_summary(brevets, date.today())
    awards = seasons.ranked_awards(brevets, date.today())
    member_since = seasons.earliest_brevet_date(brevets)
    # PBP Ancien: years the rider finished Paris-Brest-Paris, derived from the same
    # authorized RUSA history (read-only; no new input, key, or migration).
    pbp_years = seasons.pbp_ancien_years(brevets)
    return render_template('profile.html', rider=rider, club=club,
                           rusa=rusa, strava=strava, career=career,
                           pbp_years=pbp_years, awards=awards, activity_feed=activity_feed,
                           activity_calendar=activity_calendar,
                           member_since=member_since)


def _finished_rides_as_brevets(rides):
    """Convert a rider's FINISHED ``rp_ride`` rows into the brevet dict shape so
    they can be merged into the season view. Rides without a start date or a
    distance are skipped (they can't be placed in a season or measured)."""
    out = []
    for r in rides or []:
        if r.get('status') != models.RideStatus.FINISHED.value:
            continue
        start = r.get('start_at')
        if not start or not r.get('distance_km'):
            continue
        iso = start.date().isoformat() if hasattr(start, 'date') else str(start)[:10]
        out.append({
            'date': iso,
            'distance_km': int(r['distance_km']),
            'finish_time': '',
            'route_name': r.get('name') or '',
            'source': 'ride',
        })
    return out


def _merge_brevets(rusa_brevets, own_brevets):
    """Merge the rider's own finished rides into the RUSA history, preferring the
    RUSA entry on a ``(distance_km, date)`` collision so an officially-recorded
    brevet is never double-counted by a self-logged ride of the same day+distance.
    """
    seen = {(b.get('distance_km'), b.get('date')) for b in rusa_brevets}
    merged = list(rusa_brevets)
    for b in own_brevets:
        key = (b.get('distance_km'), b.get('date'))
        if key not in seen:
            merged.append(b)
            seen.add(key)
    return merged


@main_bp.route('/rides-by-season')
@profile_required
def rides_by_season():
    """The signed-in rider's brevets grouped into randonneuring seasons (Nov 1
    boundary), current season first + highlighted, past seasons collapsible. The
    RUSA history is merged with the rider's own finished ``rp_ride`` records
    (deduped on date+distance). Self-scoped like /profile — no rider-id parameter.
    """
    rider = current_rider()
    rusa = load_rusa_section(rider)
    own = _finished_rides_as_brevets(models.get_rider_rides(rider['id']))
    merged = _merge_brevets(rusa.get('brevets') or [], own)
    today = date.today()
    return render_template(
        'rides_by_season.html', rider=rider, rusa=rusa,
        seasons=seasons.seasons_with_summaries(merged, today),
        current_season=seasons.current_season_name(today),
    )


@main_bp.route('/rusa/refresh', methods=['POST'])
@profile_required
def rusa_refresh():
    """Force a fresh RUSA scrape, ignoring the cache TTL."""
    rider = current_rider()
    if not rider.get('rusa_id'):
        flash('Add a RUSA ID to your profile first.', 'info')
        return redirect(url_for('main.dashboard'))

    section = load_rusa_section(rider, force_refresh=True)
    if section.get('error'):
        flash(section['error'], 'error')
    else:
        flash('RUSA history refreshed.', 'success')
    return redirect(url_for('main.dashboard'))
