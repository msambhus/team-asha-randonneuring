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
the page does not re-scrape on every load: the first request past the TTL blocks on
a scrape and upserts; later requests hit the cache. A scrape failure never 500s —
it serves the (stale) cache and shows a soft degradation banner, and an empty scrape
never overwrites good cache.

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
  - Sign-up: Team Asha's participation lives in rider_ride (INTERESTED/MAYBE/GOING/…)
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
from shared.rusa_calendar import get_rusa_events

calendar_bp = Blueprint('calendar', __name__)

# Re-scrape the national RUSA calendar at most once every 6h; between scrapes every
# /calendar load is served from the rp_brevet_event cache (no HTTP). Matches
# BrevetHub's cron-less serverless pattern (on-demand refresh, no background job).
CALENDAR_TTL = timedelta(hours=6)

# The statuses a rider may set from the calendar (BrevetHub's own enum values).
_SIGNUP_STATUSES = {
    models.RideStatus.INTERESTED.value,
    models.RideStatus.GOING.value,
    models.RideStatus.WITHDRAW.value,
}


def _refresh_calendar_cache():
    """Scrape + cache upcoming brevets when the cache is empty or past the TTL.

    Never raises: returns a soft-degradation flag the page renders as a banner —
      None      cache is fresh, or a fresh scrape succeeded,
      'stale'   scrape failed/empty but a prior cache is still being served,
      'empty'   nothing cached and the scrape failed/returned nothing.
    An empty scrape never overwrites a good cache (a transient RUSA outage and a
    genuinely empty result are indistinguishable to the scraper).
    """
    latest = models.get_events_cache_freshness()
    fresh = (latest is not None
             and (datetime.now(timezone.utc) - latest) < CALENDAR_TTL)
    if fresh:
        return None

    try:
        # fetch_rwgps=False: the national feed alone is enough for the calendar, and
        # following every route-detail page would make a cold load do dozens of
        # blocking HTTP calls. region_filter=None → the general RUSA calendar.
        events = get_rusa_events(fetch_rwgps=False)
    except Exception as e:
        current_app.logger.warning('RUSA calendar scrape failed: %s', e)
        return 'stale' if latest is not None else 'empty'

    if events:
        for event in events:
            models.upsert_brevet_event(event)
        return None
    # Empty scrape: keep any good cache, but flag that we served nothing fresh.
    return 'stale' if latest is not None else 'empty'


@calendar_bp.route('/calendar')
def calendar():
    """Public upcoming-brevets calendar. Guests browse freely; a signed-in rider
    additionally sees their own per-event status and an optional "my region" view."""
    rider = current_rider()
    club = None
    if rider and rider.get('club_id'):
        club = models.get_club(rider['club_id'])

    # Optional state-level narrowing (only meaningful when the rider has a club).
    scope = request.args.get('scope', 'all')
    state = club['state'] if (scope == 'club' and club and club.get('state')) else None

    degraded = _refresh_calendar_cache()
    events = models.get_upcoming_events(state=state)

    # The current rider's OWN status per event — never another rider's, so the
    # guest/other-rider view stays free of any participation PII.
    my_status = {}
    if rider:
        my_status = {row['event_id']: row['status']
                     for row in models.get_rider_signup_statuses(rider['id'])}

    return render_template(
        'calendar.html', events=events, my_status=my_status, rider=rider,
        club=club, scope=scope, degraded=degraded,
    )


@calendar_bp.route('/calendar/<int:event_id>/signup', methods=['POST'])
def signup(event_id):
    """Mark the signed-in rider interested / going / withdraw on a brevet.

    JSON API (no redirects), auth ladder:
      - no session rider           → 401 (+ a login_url the client can send them to)
      - invalid status             → 400
      - unknown event              → 404
    Only after all three pass is the sign-up upserted (one row per rider+event).
    """
    rider = current_rider()
    if not rider:
        return jsonify({
            'error': 'Sign in to sign up for a brevet.',
            'login_url': url_for('auth.login', next=url_for('calendar.calendar')),
        }), 401

    payload = request.get_json(silent=True) or request.form
    status = (payload.get('status') or '').strip().lower()
    if status not in _SIGNUP_STATUSES:
        return jsonify({'error': 'Invalid status'}), 400

    event = models.get_brevet_event(event_id)
    if not event:
        return jsonify({'error': 'Event not found'}), 404

    models.set_rider_signup(rider['id'], event_id, status)
    return jsonify({'ok': True, 'event_id': event_id, 'status': status}), 200
