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
                   request, url_for)

from brevethub import models, rusa_stats
from brevethub.decorators import current_rider, login_required, profile_required
from brevethub.redirects import is_safe_relative_url, safe_redirect
from brevethub.services.registration import (
    membership_pills,
    membership_status,
    profile_field_status,
    resolve_rusa_id_for_save,
    rusa_membership_needs_refresh,
    rider_display_name,
    validate_profile_phones,
)
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
    """Club home when HOST_CLUB_ID is configured; otherwise the generic landing."""
    from brevethub.services.club_site import build_club_home_context, host_club_from_config

    host_club = host_club_from_config(current_app)
    if host_club:
        rider = current_rider()
        return render_template(
            'club_home.html',
            **build_club_home_context(host_club, rider_id=rider['id'] if rider else None),
        )
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


def _rider_from_form(rider, form):
    """Re-render the profile form with the rider's submitted values."""
    updated = dict(rider)
    for key in (
        'first_name', 'last_name', 'phone', 'city',
        'emergency_name', 'emergency_phone', 'rusa_id',
    ):
        val = (form.get(key) or '').strip()
        updated[key] = val or None
    updated['sfr_member_year'] = form.get('sfr_member_year', type=int)
    club_id = form.get('club_id', type=int)
    if club_id:
        updated['club_id'] = club_id
    return updated


def _profile_page_context(rider, *, field_errors=None):
    """Shared template context for the combined profile + registration edit page."""
    club = models.get_club(rider['club_id']) if rider.get('club_id') else None
    rusa = load_rusa_section(rider)
    strava = load_strava_section(rider)
    activity_feed = build_private_activity_feed(
        strava_activities=(strava.get('stats') or {}).get('activities') or [])
    activity_calendar = build_activity_calendar(activity_feed[:60], date.today())
    # Career/SR/R-12 come from the RUSA cache only (the official record), so a
    # self-logged rp_ride can never inflate them. seasons.career_summary tolerates
    # an empty history, giving a graceful zero-state for a RUSA-less rider.
    brevets = rusa.get('brevets') or []
    career = seasons.career_summary(brevets, date.today())
    awards = seasons.ranked_awards(brevets, date.today())
    member_since = seasons.earliest_brevet_date(brevets)
    pbp_finishes = seasons.pbp_finishes(brevets)
    pbp_years = seasons.pbp_ancien_years(brevets)
    member = membership_status(rider, refresh_rusa=rusa_membership_needs_refresh(rider))
    next_url = request.args.get('next')
    form_action = url_for('main.edit_profile')
    if is_safe_relative_url(next_url):
        form_action = url_for('main.edit_profile', next=next_url)
    return {
        'rider': rider,
        'club': club,
        'rusa': rusa,
        'strava': strava,
        'career': career,
        'awards': awards,
        'pbp_years': pbp_years,
        'pbp_finishes': pbp_finishes,
        'activity_feed': activity_feed,
        'activity_calendar': activity_calendar,
        'member_since': member_since,
        'display_name': rider_display_name(rider),
        'clubs': models.get_all_clubs(),
        'membership_pills': membership_pills(rider, membership=member),
        'field_status': profile_field_status(rider, rusa_lookup=member['rusa']),
        'field_errors': field_errors or {},
        'form_action': form_action,
    }


@main_bp.route('/profile')
@login_required
def profile():
    """Signed-in rider profile: editable registration fields plus career/Strava."""
    rider = current_rider()
    return render_template('profile.html', **_profile_page_context(rider))


@main_bp.route('/connections')
@profile_required
def connections():
    """Private integration management page.

    Keep this page intentionally narrow: Strava is the only supported
    connection today; future Garmin, Coros, and AXS integrations can be added
    as separate cards without changing the account menu or profile page.
    """
    rider = current_rider()
    return render_template(
        'connections.html',
        rider=rider,
        strava_connected=models.get_strava_connection(rider['id']) is not None,
    )


@main_bp.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    """Save profile edits; GET redirects to the combined profile page."""
    if request.method == 'GET':
        anchor = '#edit-profile'
        next_url = request.args.get('next')
        if is_safe_relative_url(next_url):
            return redirect(url_for('main.profile', next=next_url) + anchor)
        return redirect(url_for('main.profile') + anchor)

    rider = current_rider()
    clubs = models.get_all_clubs()
    raw_rusa = (request.form.get('rusa_id') or '').strip()
    club_id = request.form.get('club_id', type=int)
    sfr_year = request.form.get('sfr_member_year', type=int)
    form_rider = _rider_from_form(rider, request.form)
    field_errors = {}
    first_name = (request.form.get('first_name') or '').strip() or None
    last_name = (request.form.get('last_name') or '').strip() or None
    rusa_id, rusa_errors = resolve_rusa_id_for_save(raw_rusa, first_name, last_name)
    if (
        not rusa_errors
        and rusa_id
        and models.rusa_id_already_claimed(rusa_id, exclude_rider_id=rider['id'])
    ):
        rusa_errors['rusa_id'] = (
            'That RUSA ID is already registered to another account.')
    field_errors.update(rusa_errors)
    if not club_id or not models.club_exists(club_id):
        field_errors['club_id'] = 'Please choose your home club.'

    ok, phone_errors, phones = validate_profile_phones(
        request.form.get('phone'),
        request.form.get('emergency_phone'),
    )
    field_errors.update(phone_errors)
    if field_errors:
        ctx = _profile_page_context(form_rider, field_errors=field_errors)
        ctx['clubs'] = clubs
        return render_template('profile.html', **ctx)

    if str(rusa_id or '') != str(rider.get('rusa_id') or ''):
        models.clear_rider_rusa_membership_cache(rider['id'])

    rider = models.update_rider_registration_profile(
        rider['id'],
        first_name=(request.form.get('first_name') or '').strip() or None,
        last_name=(request.form.get('last_name') or '').strip() or None,
        phone=phones['phone'],
        city=(request.form.get('city') or '').strip() or None,
        emergency_name=(request.form.get('emergency_name') or '').strip() or None,
        emergency_phone=phones['emergency_phone'],
        sfr_member_year=sfr_year,
        rusa_id=rusa_id,
        club_id=club_id,
    )
    models.complete_rider_profile(
        rider['id'], rider.get('rusa_id'), club_id,
        rusa_id_duplicate=False)
    flash('Profile saved.', 'success')
    return safe_redirect(request.args.get('next'), 'main.profile')


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
