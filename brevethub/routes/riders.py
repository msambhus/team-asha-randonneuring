"""BrevetHub community surfaces — club-scoped, read-only rider pages.

Login-gated surfaces that turn BrevetHub from self-profile-only into a real
multi-rider club, each scoped to the signed-in viewer's OWN club:

- ``/riders/<int:rusa_id>``       a same-club rider's public profile (access-gated)
- ``/riders``                     the club rider directory (searchable by display name)
- ``/riders/season/<name>``       the club roster for one randonneuring season

Tenant isolation is the load-bearing property: every rider query is parameterized
by the *viewer's* ``club_id`` (from the session-resolved current rider), never by a
user-supplied value, so a rider in another club can never appear in a directory,
roster, or public profile, and a cross-club profile 404s. Career numbers reuse the
exact engine ``/profile`` uses (``shared.seasons`` over the cached RUSA history),
so nothing here recomputes randonneuring rules. Only a rider's display name (the
email local-part), RUSA id, and derived career numbers ever cross into a template —
never a full email address or ``google_id``.
"""
from datetime import date

from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, url_for,
)

from brevethub import models
from brevethub.decorators import current_rider, login_required, profile_required
from brevethub.routes.analysis import (
    _build_analysis,
    _compress_streams,
    _event_date,
    _match_activity_to_brevet,
    _owned_cycling_activities,
    _parse_hm,
    _rider_finished_brevets,
)
from brevethub.routes.strava import _valid_access_token
from shared import seasons
from shared.strava_analysis_index import ride_card, season_group
from shared.rider_directory_view import public_rider_row
from shared.strava import fetch_activity_streams
from shared.rusa_ride_kind import ride_kind_counts

riders_bp = Blueprint('riders', __name__)

_METERS_PER_KM = 1000.0
_METERS_PER_MILE = 1609.34
_M_TO_FT = 3.28084
_M_PER_S_TO_MPH = 2.23694
_KMH_TO_MPH = 0.621371


def _activity_date(activity):
    return (activity.get('start_date_local') or activity.get('date') or '')[:10]


def _activity_distance_km(activity):
    if activity.get('distance') is not None:
        try:
            return float(activity.get('distance') or 0) / _METERS_PER_KM
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(activity.get('distance_km') or 0)
    except (TypeError, ValueError):
        return 0.0


def _brevet_distance_km(brevet):
    try:
        return float(brevet.get('distance_km') or 0)
    except (TypeError, ValueError):
        return 0.0


def _brevet_name(brevet):
    return (
        brevet.get('name') or brevet.get('route_name') or brevet.get('route') or
        brevet.get('permanent_name') or 'RUSA brevet'
    )


def _activity_brevet_delta_km(activity, brevet):
    activity_date = _activity_date(activity)
    brevet_date = _event_date(brevet.get('date'))
    if not activity_date or activity_date != brevet_date:
        return None
    brevet_km = _brevet_distance_km(brevet)
    activity_km = _activity_distance_km(activity)
    if not brevet_km or not activity_km:
        return None
    delta = abs(activity_km - brevet_km)
    tolerance = max(8.0, brevet_km * 0.10)
    return delta if delta <= tolerance else None


def _split_minutes(minutes):
    total = int(round(minutes or 0))
    hours, mins = divmod(max(total, 0), 60)
    return hours, mins


def _cached_activity_from_analysis(row):
    analysis = (row or {}).get('analysis') or {}
    activity = analysis.get('activity') or {}
    if not activity:
        return None
    activity_id = row.get('strava_activity_id')
    return public_rider_row({
        'id': activity_id,
        'name': activity.get('name') or 'Strava ride',
        'date': activity.get('date'),
        'start_date_local': (activity.get('date') or '') + 'T00:00:00',
        'distance_km': activity.get('distance_km'),
        'total_elevation_gain_ft': activity.get('elevation_ft'),
        'strava_url': (
            activity.get('strava_url') or
            f'https://www.strava.com/activities/{activity_id}'
        ),
        '_cached_analysis': analysis,
    })


def _activity_metrics(activity):
    cached = activity.get('_cached_analysis') or {}
    cached_activity = cached.get('activity') or {}
    cached_summary = cached.get('summary') or {}

    if cached_activity:
        distance_km = float(cached_activity.get('distance_km') or 0)
        moving_min = _parse_hm(cached_activity.get('moving_time')) or 0
        elapsed_min = _parse_hm(cached_activity.get('elapsed_time')) or moving_min
        elevation_ft = cached_activity.get('elevation_ft') or 0
        avg_speed_mph = None
        if elapsed_min and distance_km:
            avg_speed_mph = round((distance_km * _KMH_TO_MPH) / (elapsed_min / 60), 1)
        elif cached_activity.get('avg_speed_kmh') is not None:
            avg_speed_mph = round(cached_activity['avg_speed_kmh'] * _KMH_TO_MPH, 1)
        average_hr = cached_summary.get('avg_hr')
        average_watts = cached_summary.get('avg_watts')
        max_watts = cached_summary.get('max_watts')
        return {
            'distance_miles': round(distance_km * _KMH_TO_MPH, 1),
            'moving_time_hrs': _split_minutes(moving_min)[0],
            'moving_time_min': _split_minutes(moving_min)[1],
            'elapsed_time_hrs': _split_minutes(elapsed_min)[0],
            'elapsed_time_min': _split_minutes(elapsed_min)[1],
            'stopped_time_min': round(max(0, elapsed_min - moving_min)),
            'elevation_ft': round(elevation_ft),
            'avg_speed_mph': avg_speed_mph,
            'strava_url': (
                cached_activity.get('strava_url') or
                f"https://www.strava.com/activities/{activity.get('id')}"
            ),
            'has_heartrate': average_hr is not None,
            'average_heartrate': average_hr,
            'max_heartrate': cached_summary.get('max_hr'),
            'device_watts': average_watts is not None or max_watts is not None,
            'average_watts': average_watts,
            'suffer_score': cached_summary.get('suffer_score'),
        }

    moving_min = (activity.get('moving_time') or 0) / 60
    elapsed_min = (activity.get('elapsed_time') or 0) / 60
    distance_miles = (activity.get('distance') or 0) / _METERS_PER_MILE
    elevation_ft = (activity.get('total_elevation_gain') or 0) * _M_TO_FT
    moving_hours, moving_remainder = _split_minutes(moving_min)
    elapsed_hours, elapsed_remainder = _split_minutes(elapsed_min)
    return {
        'distance_miles': round(distance_miles, 1),
        'moving_time_hrs': moving_hours,
        'moving_time_min': moving_remainder,
        'elapsed_time_hrs': elapsed_hours,
        'elapsed_time_min': elapsed_remainder,
        'stopped_time_min': round(max(0, elapsed_min - moving_min)),
        'elevation_ft': round(elevation_ft),
        'avg_speed_mph': round((activity.get('average_speed') or 0) * _M_PER_S_TO_MPH, 1),
        'strava_url': (
            activity.get('strava_url') or
            f"https://www.strava.com/activities/{activity.get('id')}"
        ),
        'has_heartrate': bool(activity.get('has_heartrate') or activity.get('average_heartrate')),
        'average_heartrate': activity.get('average_heartrate'),
        'max_heartrate': activity.get('max_heartrate'),
        'device_watts': bool(activity.get('device_watts') or activity.get('average_watts')),
        'average_watts': activity.get('average_watts'),
        'suffer_score': activity.get('suffer_score'),
    }


def _load_analysis_index_activities(rider_id, connection):
    """Live Strava activities plus any cached BrevetHub analyses.

    Team Asha renders this index from its Strava tables. BrevetHub does not keep a
    full activity table, so the reusable template receives live owner activities
    where available and falls back to existing rp_ride_analysis cache rows.
    """
    activities = {}
    try:
        for row in models.get_rider_ride_analyses(rider_id):
            cached = _cached_activity_from_analysis(row)
            if cached and cached.get('id') is not None:
                activities[cached['id']] = cached
    except Exception as e:  # noqa: BLE001 - index should not 500 over cache extras
        current_app.logger.warning(
            'brevet analysis index: cached analyses failed for rider %s: %s',
            rider_id, e)

    if connection:
        try:
            token = _valid_access_token(rider_id, connection)
            activities.update(_owned_cycling_activities(token))
        except Exception as e:  # noqa: BLE001 - render finished brevets without matches
            current_app.logger.warning(
                'brevet analysis index: Strava activity fetch failed for rider %s: %s',
                rider_id, e)
            flash('Could not refresh Strava activities right now. Showing cached matches if available.',
                  'warning')

    return activities


def _match_brevets_to_activities(brevets, activities):
    matched = []
    used_activity_ids = set()
    activity_rows = list((activities or {}).values())
    for brevet in brevets or []:
        best = None
        best_delta = None
        for activity in activity_rows:
            activity_id = activity.get('id')
            if activity_id in used_activity_ids:
                continue
            delta = _activity_brevet_delta_km(activity, brevet)
            if delta is not None and (best_delta is None or delta < best_delta):
                best = activity
                best_delta = delta
        if best is not None and best.get('id') is not None:
            used_activity_ids.add(best['id'])
        matched.append((brevet, best))
    return matched


def _plan_event_ids(brevets):
    event_ids = [b.get('event_id') for b in brevets or [] if b.get('event_id')]
    try:
        return models.get_brevet_route_plan_event_ids(event_ids)
    except Exception as e:  # noqa: BLE001 - plan badges are additive
        current_app.logger.warning('brevet analysis index: plan lookup failed: %s', e)
        return set()


def _season_analysis_cards(rider_id, connection):
    brevets = _rider_finished_brevets(rider_id)
    brevets.sort(key=lambda b: _event_date(b.get('date')), reverse=True)
    activities = _load_analysis_index_activities(rider_id, connection)
    plan_ids = _plan_event_ids(brevets)

    by_season = {}
    used_activity_ids = set()
    for brevet, activity in _match_brevets_to_activities(brevets, activities):
        season_name = seasons.season_name_for_date(_event_date(brevet.get('date')))
        if not season_name:
            continue
        if activity and activity.get('id') is not None:
            used_activity_ids.add(activity['id'])
        has_comparison = bool(activity and (activity.get('_cached_analysis') or {}).get('comparison'))
        event_id = brevet.get('event_id')
        card = ride_card(
            ride_id=activity.get('id') if activity else event_id,
            ride_name=_brevet_name(brevet),
            date=_event_date(brevet.get('date')),
            distance_km=brevet.get('distance_km'),
            elevation_ft=brevet.get('elevation_ft'),
            finish_time=brevet.get('finish_time'),
            has_plan=bool((event_id and event_id in plan_ids) or has_comparison),
            has_match=activity is not None,
            is_brevet=True,
            activity=_activity_metrics(activity) if activity else None,
        )
        by_season.setdefault(season_name, []).append(card)

    # The private owner page is also the rider's Strava ride list. Activities that
    # did not match an official finished brevet remain visible as Regular rides;
    # they are not silently discarded or mislabeled as failed brevet matches.
    for activity in activities.values():
        activity_id = activity.get('id')
        if activity_id is None or activity_id in used_activity_ids:
            continue
        activity_date = _activity_date(activity)
        season_name = seasons.season_name_for_date(_event_date(activity_date))
        if not season_name:
            continue
        metrics = _activity_metrics(activity)
        by_season.setdefault(season_name, []).append(ride_card(
            ride_id=activity_id,
            ride_name=activity.get('name') or 'Strava ride',
            date=activity_date,
            distance_km=round(_activity_distance_km(activity), 1),
            elevation_ft=metrics.get('elevation_ft'),
            has_plan=False,
            has_match=True,
            is_brevet=False,
            activity=metrics,
        ))

    current = seasons.current_season_name(date.today())
    season_analysis = []
    for name in sorted(by_season, reverse=True):
        ride_cards = sorted(by_season[name], key=lambda c: c.get('date') or '', reverse=True)
        season_analysis.append(
            season_group({'name': name}, name == current, ride_cards))
    return season_analysis


def _team_asha_rider_context(rider):
    display_name = _display_name(rider.get('email'))
    return {
        'id': rider.get('id'),
        'first_name': display_name,
        'last_name': '',
        # BrevetHub riders may not have a RUSA id. The compatibility route accepts
        # either the real RUSA id or this rider id fallback.
        'rusa_id': rider.get('rusa_id') or rider.get('id'),
    }


def _ensure_cached_ride_analysis(rider, activity_id):
    if models.get_ride_analysis(rider['id'], activity_id):
        return True

    connection = models.get_strava_connection(rider['id'])
    if not connection:
        flash('Connect Strava to analyze your rides.', 'error')
        return False

    try:
        token = _valid_access_token(rider['id'], connection)
        owned = _owned_cycling_activities(token)
    except Exception as e:  # noqa: BLE001
        current_app.logger.warning(
            'brevet analysis detail: owned-list fetch failed for rider %s: %s',
            rider['id'], e)
        flash('Could not reach Strava right now. Please try again later.', 'error')
        return False

    activity = owned.get(activity_id)
    if activity is None:
        abort(404)

    try:
        streams = fetch_activity_streams(
            token, activity_id, api_base=current_app.config['STRAVA_API_BASE'])
    except Exception as e:  # noqa: BLE001
        current_app.logger.warning(
            'brevet analysis detail: stream fetch failed for rider %s activity %s: %s',
            rider['id'], activity_id, e)
        flash('Could not fetch this activity from Strava. Please try again later.', 'error')
        return False

    brevets = _rider_finished_brevets(rider['id'])
    analysis = _build_analysis(activity, streams,
                               _match_activity_to_brevet(activity, brevets))
    models.upsert_ride_analysis(
        rider['id'], activity_id, analysis,
        compressed_streams=_compress_streams(streams))
    return True


@riders_bp.route('/my/strava-analysis')
@profile_required
def my_strava_analysis():
    """Private page: the reused Team Asha completed-brevet analysis UX."""
    rider = current_rider()
    connection = models.get_strava_connection(rider['id'])
    return render_template(
        'my_strava_analysis.html',
        rider=_team_asha_rider_context(rider),
        season_analysis=_season_analysis_cards(rider['id'], connection),
    )


@riders_bp.route('/rider/<int:rusa_id>/ride/<int:ride_id>/strava-analysis')
@profile_required
def ride_strava_analysis(rusa_id, ride_id):
    """Compatibility endpoint for Team Asha's brevet-card analysis links."""
    rider = current_rider()
    allowed_ids = {rider['id']}
    if rider.get('rusa_id'):
        allowed_ids.add(rider['rusa_id'])
    if rusa_id not in allowed_ids:
        abort(404)
    if not _ensure_cached_ride_analysis(rider, ride_id):
        return redirect(url_for('riders.my_strava_analysis'))
    return redirect(url_for('analysis.analysis_detail', activity_id=ride_id))


@riders_bp.route('/brevets/comparison')
@login_required
def brevet_comparison():
    """Compatibility endpoint for the reused Team Asha Strava template."""
    return redirect(url_for('analysis.analysis_list'))


@riders_bp.route('/analysis/<int:ride_id>/cohort-comparison')
@login_required
def ride_cohort_comparison(ride_id):
    """Compatibility endpoint for the reused Team Asha Strava template."""
    return redirect(url_for('analysis.analysis_detail', activity_id=ride_id))


@riders_bp.route('/analysis/<int:ride_id>/notes', methods=['POST'])
@login_required
def save_ride_notes(ride_id):
    """Persist owner notes from the reused Team Asha Strava template."""
    rider = current_rider()
    payload = request.get_json(silent=True) or {}
    saved = models.save_ride_analysis_note(
        rider['id'],
        ride_id,
        payload.get('scope'),
        payload.get('ident'),
        payload.get('note'),
    )
    if saved is None:
        return jsonify({'error': 'note not saved'}), 404
    return jsonify({'note': saved})


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
    kinds = ride_kind_counts(brevets)
    pbp_years = seasons.pbp_ancien_years(brevets)
    return public_rider_row({
        'id': rider_row.get('id'),
        'rusa_id': rider_row.get('rusa_id'),
        'display_name': _display_name(rider_row.get('email')),
        'total_km': career['total_km'],
        'count': career['count'],
        # SR awards across the career (a season with two full series counts twice),
        # not the number of SR seasons, matching the "SR×N" profile display.
        'sr_count': career['total_sr'],
        'pbp_years': pbp_years,
        'pbp_count': len(pbp_years),
        'permanent_count': kinds['permanent'],
        'populaire_count': kinds['populaire'],
        'rides_1000_plus': sum(1 for b in brevets if (b.get('distance_km') or 0) >= 1000),
        'eddington': rider_row.get('eddington'),
        'career': career,
    })


@riders_bp.route('/riders')
@login_required
def directory():
    """Searchable directory of the viewer's club riders. A club-less viewer gets a
    graceful join-a-club state (never another club's riders)."""
    viewer = current_rider()
    club = models.get_club(viewer['club_id']) if viewer.get('club_id') else None
    q = (request.args.get('q') or '').strip()

    riders = []
    season_names = {seasons.current_season_name(date.today())}
    if viewer.get('club_id'):
        today = date.today()
        rows = models.get_club_riders_with_rusa(viewer['club_id'])
        for row in rows:
            season_names.update(group['season'] for group in seasons.seasons_with_summaries(row.get('rusa_cache') or [], today))
        riders = [_career_row(r, today) for r in rows]
        if q:
            needle = q.lower()
            riders = [r for r in riders if needle in r['display_name'].lower()]
        riders.sort(key=lambda r: r['display_name'].lower())

    return render_template('riders_directory.html', club=club, riders=riders,
                           q=q, has_club=bool(viewer.get('club_id')),
                           season_names=sorted((s for s in season_names if s), reverse=True),
                           current_season=seasons.current_season_name(date.today()))


@riders_bp.route('/riders/season/<season_name>')
@login_required
def season_roster(season_name):
    """The club roster for one randonneuring season: riders who completed at least
    one brevet in ``season_name``, with that season's per-rider summary."""
    viewer = current_rider()
    club = models.get_club(viewer['club_id']) if viewer.get('club_id') else None
    today = date.today()

    roster = []
    if viewer.get('club_id'):
        rows = models.get_club_riders_with_rusa(viewer['club_id'])
        for r in rows:
            brevets = r.get('rusa_cache') or []
            group = next((s for s in seasons.seasons_with_summaries(brevets, today)
                          if s['season'] == season_name), None)
            if group is None:
                continue  # no brevet in this season → not on the roster
            roster.append({
                'id': r.get('id'),
                'rusa_id': r.get('rusa_id'),
                'display_name': _display_name(r.get('email')),
                'summary': group['summary'],
            })
        roster.sort(key=lambda x: (-x['summary']['total_km'], x['display_name'].lower()))

    return render_template('season_roster.html', club=club, season_name=season_name,
                           roster=roster, has_club=bool(viewer.get('club_id')),
                           current_season=seasons.current_season_name(today))


@riders_bp.route('/riders/<int:rusa_id>')
def rider_profile(rusa_id):
    """A same-club rider's public profile: hero + career stat cards + season-by-season
    brevet history. Keyed on the RUSA member id — the identifier riders recognize —
    while database ids stay internal. Access gate: same club → 200; other club → 404;
    a rider viewing their own record → 200 (even when club-less); anonymous may view
    when the rider has official RUSA history. No full email or google_id ever reaches
    the rendered page.
    """
    viewer = current_rider()
    today = date.today()

    resolved = models.get_rider_by_rusa_id(rusa_id)
    if resolved is None:
        # Legacy URLs keyed on internal database id before public pages used RUSA ids.
        legacy = models.get_public_rider(rusa_id)
        if legacy and legacy.get('rusa_id'):
            return redirect(url_for('riders.rider_profile', rusa_id=legacy['rusa_id']))
        abort(404)

    rider_id = resolved['id']
    is_self = bool(viewer and viewer['id'] == rider_id)
    target = None
    if viewer and viewer.get('club_id'):
        target = models.get_club_rider(viewer['club_id'], rider_id)
    if target is None and not is_self:
        target = models.get_public_rider(rider_id)
    if target is None and is_self:
        # Self-view fallback: a rider can always see their own record, even before
        # joining a club. This is still the PUBLIC-profile contract, even for its
        # owner, so only RUSA-backed fields cross into the template. Private Strava
        # metrics belong on /profile and /my/strava-analysis.
        cache = models.get_rider_rusa_cache(viewer['id']) or {}
        target = {
            'email': viewer['email'],
            'rusa_id': viewer['rusa_id'],
            'rusa_cache': cache.get('rusa_cache'),
            'eddington': (viewer.get('eddington_miles')
                          if models.get_strava_connection(viewer['id']) else None),
        }
    if target is None:
        abort(404)

    brevets = target.get('rusa_cache') or []
    career = seasons.career_summary(brevets, today)
    season_groups = seasons.seasons_with_summaries(brevets, today)
    # PBP Ancien years from this rider's RUSA history — derived and read-only, and
    # (like every other career number here) no email or google_id crosses over.
    pbp_years = seasons.pbp_ancien_years(brevets)
    awards = seasons.ranked_awards(brevets, today)

    return render_template('rider_profile.html',
                           display_name=_display_name(target.get('email')),
                           rusa_id=target.get('rusa_id'),
                           career=career, seasons=season_groups,
                           pbp_years=pbp_years, awards=awards, eddington=target.get('eddington'),
                           is_self=is_self)
