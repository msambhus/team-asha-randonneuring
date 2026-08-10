"""BrevetHub brevet calendar + rider sign-up.

Guest surface (NO account required):
  GET  /calendar                     — upcoming RUSA brevets (date, name, distance,
                                        region, and start location/time ONLY when a
                                        source provides them — an honest "—"
                                        placeholder otherwise). Exposes no rider PII.

Rider surface (authenticated BrevetHub rider):
  POST /calendar/<event_id>/signup   — mark interested / going / withdraw on a
                                        brevet (JSON API; 401 for anon, no redirect
                                        magic — the client shows a sign-in prompt).

Events are sourced from RUSA's national listing via the shared, club-agnostic
scraper (shared.rusa_calendar.get_rusa_events) and cached in rp_brevet_event, so
the page never scrapes on a warm load. The heavy scrape lives on the scheduled
refresh (brevethub.routes.cron.refresh_calendar) — /calendar only READS the cache.
The ONLY on-request scrape is a one-time, bounded seed of a truly EMPTY cache
(first deploy, before any cron run); a present cache — even a stale one — is served
as-is and never scraped synchronously (this is the redteam fix that keeps the heavy
scrape off the hot path for the whole running lifetime, not just for a TTL window).
A scrape failure never 500s — it serves the cache and shows a soft degradation
banner, and an empty scrape never overwrites good cache.

Web parity vs Team Asha's upcoming-brevets page (templates/upcoming_brevets.html,
populated by scripts/update_rusa_events.py), deliberately narrowed for a multi-club
app and called out rather than silently diverged (see the frame plan):
  - Sources: Team Asha enriches the national feed with club-curated Google Sheets +
    per-club scrapers that supply real start locations/times (SFR sheet, SCR/SRR/SLO
    sites) with hardcoded start cities. Those are single-club sources a generic app
    can't reuse, so BrevetHub uses ONLY the national feed — which carries no start
    location/time — and renders an honest placeholder rather than inventing one.
  - Region scope: Team Asha maps specific RUSA region labels to its clubs via a
    hardcoded TEAM_RUSA_REGIONS dict. A generic app has no such map, so the calendar
    shows the general RUSA calendar and offers an optional "my region" view that
    filters by the rider's club's STATE prefix (an honest state-level narrowing).
  - Sign-up: Team Asha's participation lives in rider_ride (INTERESTED/MAYBE/REGISTERED/…)
    against its own ride table. BrevetHub stores participation in rp_event_signup
    against the rp_brevet_event cache, using BrevetHub's own RideStatus enum values
    (interested/going/withdraw) — it imports nothing from Team Asha's models.

Isolation: imports only flask / stdlib / brevethub.* / shared.*, and every model
call is on an rp_* table, so test_brevethub_isolation.py and test_rp_only.py stay
green; shared.rusa_calendar is the vendored, byte-identical engine.
"""
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, current_app, jsonify, render_template, request,
                   session, url_for)

from brevethub import models
from brevethub.decorators import current_rider
from brevethub.services.registration import membership_pills
from shared.rusa_calendar import RUSA_NATIONAL_URL, get_rusa_events
from shared.calendar_view import calendar_event, event_category, group_events_by_month, finisher_row
from shared.weather import summarize_point_forecast

calendar_bp = Blueprint('calendar', __name__)


@calendar_bp.route('/calendar/<int:event_id>/finishers')
def event_finishers(event_id):
    """Public finisher list; times come only from the RUSA sync job."""
    event = models.get_brevet_event(event_id)
    if not event:
        return ('Not found', 404)
    finishers = [finisher_row(row) for row in models.get_event_finishers(event_id)]
    return render_template('finishers.html', event=event, finishers=finishers)

# How old the cache may get before /calendar shows a soft "stale" banner. This is a
# DISPLAY threshold only — it never triggers a scrape (the redteam fix). Set wider
# than the daily cron interval so a normal day never trips it, while a genuine
# multi-day cron outage does. The scheduled refresh (routes/cron.py) keeps the cache
# warm; a present cache is NEVER re-scraped on the request path, only reported stale.
CALENDAR_STALE_AFTER = timedelta(hours=40)

# The pre-ride statuses a rider may set on the /signup endpoint (BrevetHub's own
# Pre-ride intent: interested only (registered comes from registration; withdraw is
# a registered-rider withdrawal request).
_SIGNUP_STATUSES = {
    models.RideStatus.INTERESTED.value,
    models.RideStatus.WITHDRAW.value,
}

# The post-ride result values a rider may self-report on their OWN past sign-up via
# the /result endpoint. Kept distinct from _SIGNUP_STATUSES so a pre-ride value in a
# /result body (or a result value in a /signup body) is rejected with 400.
_RESULT_STATUSES = {
    models.RideStatus.FINISHED.value,
    models.RideStatus.DNF.value,
    models.RideStatus.DNS.value,
    models.RideStatus.OTL.value,
}


def _filter_events_by_region(events, region_prefix):
    """Keep only events whose RUSA region label matches the club prefix."""
    if not region_prefix:
        return events
    return [e for e in events if (e.get('region') or '') == region_prefix]


def _fill_scraped_event_elevations(events):
    """Fill missing RUSA climbing values from sibling rows with the same route id."""
    by_route = {}
    for event in events:
        route_id = event.get('route_id')
        elevation_ft = event.get('elevation_ft')
        if route_id and elevation_ft:
            by_route.setdefault(str(route_id), elevation_ft)

    for event in events:
        if event.get('elevation_ft'):
            continue
        route_id = event.get('route_id')
        if not route_id:
            continue
        peer_elev = by_route.get(str(route_id))
        if peer_elev is not None:
            event['elevation_ft'] = peer_elev


def _scrape_and_upsert(region_prefix=None):
    """Scrape the national RUSA calendar and upsert each event into rp_brevet_event.

    Returns the number of events upserted (0 when the scrape returned nothing).
    Raises on a scrape/DB failure so the caller can decide how to degrade — the
    cron endpoint logs a warning and returns non-500 JSON, the /calendar seed path
    shows a soft banner. An empty scrape performs NO upsert, so a transient RUSA
    outage that returns nothing never clobbers a good cache. Shared by the cron
    refresh and the one-time empty-cache seed so the scrape+upsert logic lives once.

    ``region_prefix`` — when set (e.g. ``CA: San Francisco``), only events for
    that RUSA region label are upserted. The national feed is still fetched once;
    filtering happens in-process. ``None`` upserts every sanctioned event.

    fetch_rwgps=False: the national feed alone is enough for the calendar, and
    following every route-detail page would make a load do dozens of blocking HTTP
    calls. region_filter=None → the general RUSA calendar.
    """
    events = get_rusa_events(
        fetch_rwgps=False,
        include_all_sanctioned=True,
    )
    if not events:
        return 0
    events = _filter_events_by_region(events, region_prefix)
    if not events:
        return 0
    _fill_scraped_event_elevations(events)
    for event in events:
        models.upsert_brevet_event(event)
    return len(events)


def _seed_calendar_cache_if_empty():
    """Warm a truly EMPTY calendar cache with one bounded scrape (first-deploy seed).

    Never raises: returns a soft-degradation flag the page renders as a banner —
      None      cache already has rows (any age — NEVER scraped here) and is fresh,
                or the empty-cache seed scrape succeeded,
      'stale'   cache has rows but they are older than CALENDAR_STALE_AFTER,
      'empty'   cache is empty and the seed scrape failed or returned nothing.

    Policy (redteam fix): a present cache is NEVER scraped on the request path — not
    even when stale. Only a truly empty cache (first deploy, before any cron run)
    triggers the single bounded seed scrape; the daily cron keeps it warm thereafter,
    so once seeded no /calendar load ever scrapes synchronously again.
    """
    latest = models.get_events_cache_freshness()
    if latest is not None:
        # Present cache — serve as-is, never scrape on the request path.
        stale = (datetime.now(timezone.utc) - latest) >= CALENDAR_STALE_AFTER
        return 'stale' if stale else None

    # Empty cache: one bounded seed scrape. Any failure degrades to 'empty', never 500.
    try:
        if _scrape_and_upsert() > 0:
            return None
    except Exception as e:
        current_app.logger.warning('RUSA calendar seed failed: %s', e)
    return 'empty'


def _weather_by_event(events):
    """Map each event id to its summarized cached forecast (cache-read-only).

    Reads the pre-warmed rp_brevet_weather cache in one query for all events on the
    page, then summarizes each raw Open-Meteo payload with the shared pure
    summarizer — NO network fetch. Events with no cache row (far-out, region-less,
    or not yet warmed) simply don't appear in the map, so the template shows the
    honest "forecast not available yet" state for them. Returns ``{event_id:
    summary_dict}``.
    """
    ids = [ev['id'] for ev in events]
    cached = models.get_brevet_weather_for_events(ids)
    result = {}
    for event_id, row in cached.items():
        summary = summarize_point_forecast(row.get('weather_data'))
        if summary:
            result[event_id] = summary
    return result


@calendar_bp.route('/calendar')
def calendar():
    """Public upcoming-brevets calendar. Guests browse freely; a signed-in rider
    additionally sees their own per-event status and an optional "my region" view."""
    rider = current_rider()
    club = None
    if rider and rider.get('club_id'):
        club = models.get_club(rider['club_id'])

    degraded = _seed_calendar_cache_if_empty()
    events = [calendar_event(row) for row in models.get_upcoming_events()]
    for ev in events:
        ev['data_category'] = event_category(ev.get('ride_type'))
    event_categories = sorted({ev['data_category'] for ev in events})
    months = group_events_by_month(events)

    # State -> sorted RBA areas map for the cascading region dropdowns. RUSA region
    # labels look like "CA: San Francisco" -> state "CA", area "San Francisco". Derived
    # from the already-loaded events (no extra query, no club<->region map). Events carry
    # no club data (club_id is NULL for the national feed), so the RBA area after the
    # colon is the honest secondary axis. This powers the client-side State -> Area
    # dropdowns (replacing the flat chip row): works for guests, filters instantly.
    regions_by_state = {}
    for ev in events:
        region = (ev.get('region') or '').strip()
        if not region:
            continue
        state, _, area = region.partition(':')
        state = state.strip()
        area = area.strip()
        if not state:
            continue
        regions_by_state.setdefault(state, set())
        if area:
            regions_by_state[state].add(area)
    regions_by_state = {st: sorted(areas)
                        for st, areas in sorted(regions_by_state.items())}
    states = list(regions_by_state.keys())

    # Weather badges are CACHE-READ-ONLY: one query for every event on the page,
    # then summarize the stored raw forecast in-process. NO Open-Meteo/RWGPS fetch
    # ever happens here — the /cron/fetch-brevet-weather warmer populates the cache
    # off the request path. Near-term events with a resolvable region get a badge;
    # far-out or region-less events simply have no cache row and render the honest
    # "forecast not available yet" state (handled in the template).
    weather = _weather_by_event(events)

    # The current rider's OWN status per event — never another rider's, so the
    # guest/other-rider view stays free of any participation PII.
    my_status = {}
    my_registrations = {}
    my_results = []
    proof_by_event = {}
    followed_live_event_ids = set()
    if rider:
        my_status = {row['event_id']: row['status']
                     for row in models.get_rider_signup_statuses(rider['id'])}
        reg_rows = models.get_rider_signup_registrations(rider['id'])
        my_registrations = {
            row['event_id']: row for row in reg_rows if row.get('registration_status')
        }
        try:
            proof_by_event = {
                int(row['event_id']): row
                for row in models.get_rider_completed_validation_events(rider['id'])
            }
        except Exception as e:
            current_app.logger.warning('proof-eligible load failed for rider %s: %s',
                                       rider['id'], e)
            proof_by_event = {}
        # The rider's OWN past-event results, so the calendar carries the post-ride
        # surface (result badge + read-only finish_time + a status-only correction)
        # the upcoming grid cannot show. Failure-tolerant: a DB hiccup drops the
        # section rather than 500-ing the whole calendar. rider_id-scoped -> a rider
        # only ever sees and corrects their OWN results.
        try:
            my_results = models.get_rider_past_results(rider['id'])
        except Exception as e:
            current_app.logger.warning('past-result load failed for rider %s: %s',
                                       rider['id'], e)
            my_results = []
        followed_live_event_ids = models.get_followed_live_event_ids(rider['id'])
        my_volunteer_signups = models.get_rider_volunteer_signups_by_event(rider['id'])
    else:
        my_volunteer_signups = {}

    default_state = request.args.get('state') or None
    default_area = request.args.get('area') or None
    from brevethub.services.club_site import host_club_from_config
    host = host_club_from_config(current_app)
    if host:
        default_state = default_state or host.get('filter_state')
        default_area = default_area or host.get('filter_area')
    elif club:
        default_state = default_state or club.get('state')
    post_ride_open_by_event = {
        ev['id']: models.event_post_ride_open(ev) for ev in events
    }
    volunteer_event_ids = [ev['id'] for ev in events if ev.get('volunteer_enabled')]
    volunteer_summary_by_event = models.get_volunteer_summaries_for_events(volunteer_event_ids)
    return render_template(
        'calendar.html', events=events, months=months, my_status=my_status,
        my_registrations=my_registrations,
        post_ride_open_by_event=post_ride_open_by_event,
        proof_by_event=proof_by_event,
        my_results=my_results, rider=rider, club=club, states=states,
        regions_by_state=regions_by_state,
        default_state=default_state,
        default_area=default_area,
        followed_live_event_ids=followed_live_event_ids,
        my_volunteer_signups=my_volunteer_signups,
        volunteer_summary_by_event=volunteer_summary_by_event,
        degraded=degraded, weather=weather,
        event_categories=event_categories,
        membership_pills=membership_pills(rider),
        rusa_event_search_url=RUSA_NATIONAL_URL,
    )


def _login_required_json():
    """Shared 401 body for the rider-only participation endpoints (no redirect)."""
    return jsonify({
        'error': 'Sign in to sign up for a brevet.',
        'login_url': url_for('auth.login', next=url_for('calendar.calendar')),
    }), 401


@calendar_bp.route('/calendar/<int:event_id>/signup', methods=['POST'])
def signup(event_id):
    """Mark the signed-in rider interested / going / withdraw on a brevet.

    JSON API (no redirects), auth ladder:
      - no session rider           → 401 (+ a login_url the client can send them to)
      - invalid status             → 400
      - unknown event              → 404
      - a post-ride result exists  → 409 (a pre-ride intent may not clobber a result)
    Then the pre-ride intent is applied (one row per rider+event). WITHDRAW is
    UPDATE-only (mirrors the parent web app guard): withdrawing with no existing
    sign-up → 404, so a rider cannot manufacture a withdraw row. A pre-ride status
    (or withdraw) is refused over a finished / dnf / dns / otl result so it cannot
    erase the result; the rider must use the /result endpoint to correct it. Every
    mutation is scoped to the signed-in rider, so a rider can only ever change their
    OWN row.
    """
    rider = current_rider()
    if not rider:
        return _login_required_json()

    payload = request.get_json(silent=True) or request.form
    status = (payload.get('status') or '').strip().lower()
    if status not in _SIGNUP_STATUSES:
        return jsonify({'error': 'Invalid status'}), 400

    event = models.get_brevet_event(event_id)
    if not event:
        return jsonify({'error': 'Event not found'}), 404

    if status == models.RideStatus.WITHDRAW.value:
        outcome = models.withdraw_rider_signup(rider['id'], event_id)
        if outcome == 'not_found':
            counts = models.get_event_signup_counts(event_id)
            return jsonify({'ok': True, 'event_id': event_id, 'status': None,
                            **counts}), 200
        if outcome == 'not_registered':
            return jsonify({'error': 'Register before requesting withdrawal.'}), 400
        if outcome == 'has_result':
            return jsonify({'error': 'Cannot change a sign-up with a result'}), 409
        if outcome == 'already_requested':
            return jsonify({'error': 'Withdrawal already pending review.'}), 409
        counts = models.get_event_signup_counts(event_id)
        resp_status = 'dns' if outcome == 'dns' else 'withdrawal_requested'
        return jsonify({'ok': True, 'event_id': event_id, 'status': resp_status,
                        'outcome': outcome, **counts}), 200
    else:
        reg = models.get_event_signup_registration(rider['id'], event_id)
        if reg and reg.get('registration_status'):
            return jsonify({'error': 'Clear registration before changing interest.'}), 409
        outcome = models.set_rider_signup(rider['id'], event_id, status)
        if outcome == 'has_result':
            return jsonify({'error': 'Cannot change a sign-up with a result'}), 409
    counts = models.get_event_signup_counts(event_id)
    return jsonify({'ok': True, 'event_id': event_id, 'status': status,
                    **counts}), 200


@calendar_bp.route('/calendar/<int:event_id>/follow-live', methods=['POST'])
def follow_live(event_id):
    """Follow/unfollow a brevet's live view before or during the ride."""
    rider = current_rider()
    if not rider:
        return _login_required_json()
    if not models.get_brevet_event(event_id):
        return jsonify({'error': 'Event not found'}), 404
    payload = request.get_json(silent=True) or request.form
    raw = payload.get('followed', payload.get('follow', True))
    followed = raw is True or str(raw).lower() in {'1', 'true', 'yes', 'on'}
    ids = models.set_followed_live_event(rider['id'], event_id, followed)
    return jsonify({'ok': True, 'event_id': event_id, 'followed': event_id in ids}), 200


@calendar_bp.route('/calendar/<int:event_id>/signup', methods=['DELETE'])
def unsignup(event_id):
    """Clear the signed-in rider's OWN pre-ride sign-up on a brevet.

    JSON API (no redirects), mirroring the parent web app unsignup guard: only a
    pre-ride intent (interested / maybe / going) may be cleared; a post-ride result
    (or a withdraw) is retained as history.
      - no session rider           → 401
      - no sign-up row             → 404
      - a non-clearable status     → 400
      - a pre-ride row             → 200 (deleted)
    rider_id-scoped, so a rider can only ever clear their OWN row.
    """
    rider = current_rider()
    if not rider:
        return _login_required_json()

    outcome = models.clear_rider_signup(rider['id'], event_id)
    if outcome == 'not_found':
        return jsonify({'error': 'No sign-up to remove'}), 404
    if outcome == 'registered':
        return jsonify({'error': 'Cannot clear interest while registered.'}), 400
    if outcome == 'post_ride':
        return jsonify({'error': 'Cannot remove a sign-up with a result'}), 400
    counts = models.get_event_signup_counts(event_id)
    return jsonify({'ok': True, 'event_id': event_id, 'status': None,
                    **counts}), 200


@calendar_bp.route('/calendar/<int:event_id>/result', methods=['POST'])
def set_result(event_id):
    """Self-service post-ride result on the signed-in rider's OWN past sign-up.

    The parent web app sets result states via a club-admin grid; BrevetHub has no
    admin surface yet, so a rider self-reports the result of their own past ride
    instead (see the web-parity notes). This is inherently tenant-safe: every
    mutation binds rider_id, so a rider can NEVER touch another rider's row — a
    result on a row that is not theirs reads as "no such sign-up" → 404.

    STATUS-ONLY: the endpoint reads only ``status`` from the body. Any client-sent
    ``finish_time`` is ignored — an official finish time comes from RUSA, not a rider
    self-report (parity + anti-abuse). finish_time is reflected read-only in the
    response.

    Auth ladder / result guard:
      - no session rider                          → 401
      - a value that is not a result status       → 400 (a pre-ride value is rejected
                                                     before any DB read)
      - no sign-up for this rider on this event   → 404 (absorbs cross-rider probes)
      - the event date has not passed             → 409
      - a non-convertible current status          → 409
      - own past going/post-ride row              → 200 (status set)
    """
    rider = current_rider()
    if not rider:
        return _login_required_json()

    payload = request.get_json(silent=True) or request.form
    status = (payload.get('status') or '').strip().lower()
    if status not in _RESULT_STATUSES:
        return jsonify({'error': 'Invalid result status'}), 400

    # status-only: any client finish_time in the body is never read here.
    outcome, finish_time = models.set_signup_result(rider['id'], event_id, status)
    if outcome == 'not_found':
        return jsonify({'error': 'No past sign-up to set a result on'}), 404
    if outcome in ('not_past', 'ineligible'):
        return jsonify({'error': 'This sign-up is not eligible for a result'}), 409
    return jsonify({'ok': True, 'event_id': event_id, 'status': status,
                    'finish_time': finish_time}), 200
