"""BrevetHub public shell + rider dashboard.

- `/`             neutral landing page with the "Sign in with Google" entry point.
- `/dashboard`    a signed-in rider's home (profile required): RUSA brevet history
                  + stats and the Strava activity summary.
- `/rusa/refresh` forces a fresh RUSA scrape, bypassing the cache TTL.

RUSA scraping reuses `shared.rusa` (the same logic Team Asha uses) and is cached
on `rp_rider` (rusa_cache JSONB + rusa_fetched_at); the Strava summary lives in
`routes/strava.py`. Every external call degrades gracefully — a RUSA/Strava
outage shows a message, never a 500. The guest/spectator public-ride browse is
deferred to a follow-on mission (the `rp_ride.is_public` flag + `get_public_rides`
helper already exist so no later migration alters this baseline).
"""
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   url_for)

from brevethub import models, rusa_stats
from brevethub.decorators import current_rider, profile_required
from brevethub.routes.strava import load_strava_section
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


@main_bp.route('/dashboard')
@profile_required
def dashboard():
    rider = current_rider()
    club = models.get_club(rider['club_id']) if rider.get('club_id') else None
    rusa = load_rusa_section(rider)
    strava = load_strava_section(rider)
    return render_template('dashboard.html', rider=rider, club=club,
                           rusa=rusa, strava=strava)


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
