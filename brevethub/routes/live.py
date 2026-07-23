"""BrevetHub public/guest live-ride browse + rider-owned position ingestion.

Guest surfaces (NO account required):
  GET  /live                        — list rp_ride rows where is_public = TRUE
  GET  /live/<ride_id>              — per-ride live map (Leaflet + OSM); 404 for a
                                       private/unknown ride
  GET  /live/<ride_id>/positions.json — JSON position trail for the map poll
                                       (lat/lng/recorded_at only — no rider PII)

Rider surfaces (authenticated BrevetHub rider):
  GET/POST /live/new               — create/flag one of your rides public+live and
                                       get its shareable /live/<id> URL
  POST /live/<ride_id>/public      — flip your own ride's public flag (owner-scoped)
  POST /api/rides/<ride_id>/position — append a {lat,lng,recorded_at} breadcrumb for
                                       YOUR ride (401 anon / 403 non-owner)

Member live-tracking surfaces (Surface B — Mission 1: real Garmin ingestion +
multi-rider Mapbox map). PHYSICALLY SPLIT from the anonymous guest map so a rider
name/telemetry can NEVER reach the world-viewable poll:
  GET/POST /live/settings          — master live-tracking opt-in toggle (self-scoped)
  POST /live/<ride_id>/garmin      — register/clear YOUR OWN Garmin link for a ride;
                                       self-scoped + accessibility-gated (public OR
                                       own ride, else 404)
  GET  /live/<ride_id>/map         — member Mapbox map: named dots + telemetry +
                                       route polyline (@profile_required + gate;
                                       anon → login; inaccessible → 404; no-token →
                                       graceful "map unavailable")
  GET  /live/<ride_id>/live-positions.json — named+telemetry positions poll
                                       (401 anon / 404 inaccessible)
Access rule (both attach + member map): a logged-in rider may attach to / view a
ride iff it is public OR they own it. Attaching to a public ride is the multi-rider
join; a private ride is owner-only.

Web parity vs Team Asha's live tracking (routes/live.py), deliberately narrowed for
M3 and called out rather than silently diverged (see the frame plan):
  - Access model: Team Asha gates the per-ride map with a per-ride INVITE CODE
    (get_valid_ride_invite / live_guest session grant). BrevetHub uses the per-ride
    ``is_public`` flag instead — a public ride is world-viewable, a private one 404s.
  - Map: Team Asha renders Mapbox GL with an RWGPS route polyline + rich per-rider
    telemetry (pace/plan/weather). BrevetHub has no Mapbox token, so it uses
    Leaflet + OpenStreetMap tiles and shows only the position trail (anonymous dots
    — no rider name column exists). Route overlay + telemetry are out of scope.
  - Ingestion: Team Asha ingests via Garmin LiveTrack polled by a Railway cron
    (services/live_telemetry.py, which transitively imports Flask via services.rwgps
    and so must NOT move into shared/ — M1 red-team). BrevetHub wires only the clean
    rider-posts-own-position path; a Garmin follow-on would add a poller that calls
    the SAME models.insert_position with the ride owner's rider_id.
  - Refresh: both poll periodically (Team Asha ~fast; BrevetHub every 20s).

Isolation: imports only flask / stdlib / brevethub.*, and every model call is on an
rp_* table, so test_brevethub_isolation.py and test_rp_only.py stay green.
"""
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)

from brevethub import models
from brevethub.auth_api import bearer_or_session_rider
from brevethub.decorators import current_rider, profile_required
from brevethub.shared import live_radial as radial
from brevethub.shared import live_telemetry as tlm
from brevethub.shared.garmin_livetrack import parse_session
from brevethub.shared.plan_match import match_plan
from brevethub.shared.rwgps import extract_rwgps_route_id, fetch_route

live_bp = Blueprint('live', __name__)

# Unit conversions for the plan-aware telemetry readout (mirrors the parent app,
# which carries brevet plans in native miles / mph / feet).
M_TO_MI = 1 / 1609.344
MS_TO_MPH = 2.236936

# Downsample cap for the cached route geometry used by telemetry (distance /
# ascent / on-route projection). Long brevet routes carry tens of thousands of
# points; this keeps the per-poll projection cheap.
_MAX_CONTEXT_TRACK_POINTS = 2000

# Minimum position fixes needed to project a trajectory and derive movement. Below
# this a rider shows the Mission-1 basics only (no plan-aware grading).
MIN_HISTORY_FOR_PLAN = 2

# Plan-timing dot colors for the member map (ahead / behind / unknown). Kept
# distinct from the signup-status color so a ride without a plan never regresses.
PLAN_AHEAD_COLOR = '#16a34a'
PLAN_BEHIND_COLOR = '#dc2626'
PLAN_UNKNOWN_COLOR = '#9ca3af'

# How often the live map re-polls the positions endpoint (seconds). Polling is
# deliberately simple — no websockets — mirroring Team Asha's poll model.
LIVE_POLL_SECONDS = 20

# Cap breadcrumbs a rider may hold for one ride's trail (defensive; the read side
# also caps). A brevet posting every ~30s for 40h is < 5k points.
_MAX_TRAIL_POINTS = 500


def _valid_coord(value, lo, hi):
    """Parse a lat/lng to a float within [lo, hi], or None if invalid/out-of-range."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f < lo or f > hi or f != f:  # f != f rejects NaN
        return None
    return f


# --------------------------------------------------------------------------- #
# Guest browse (no account)
# --------------------------------------------------------------------------- #
@live_bp.route('/live')
def live_list():
    """Public list of rides opted into live tracking (is_public = TRUE only).

    Viewer-aware: guests browse freely; a signed-in rider additionally gets a
    per-row link into the member map (live.live_ride_map), the page carrying the
    Garmin LiveTrack + phone-location share. The rider is resolved with the same
    current_rider helper calendar.py uses; the ride list itself is unchanged."""
    rider = current_rider()
    rides = models.get_public_rides()
    return render_template('live_list.html', rides=rides, rider=rider,
                           poll_seconds=LIVE_POLL_SECONDS)


# NOTE: no along-route weather overlay on the BrevetHub live map yet. The shared
# _radial_live.html partial supports it (weather_points), and Team Asha passes it, but
# BrevetHub's weather cache (rp_brevet_route_weather) is keyed by EVENT id while an
# rp_ride carries no event/route-id linkage (only rwgps_url + start_at) — so a live
# ride can't resolve its cached forecast today. The partial degrades gracefully (no
# weather_points -> route + rider dots only). Follow-up: add an rp_ride->event link (or
# a route-id-keyed weather read) so BH can pass weather_points the same way TA does.
@live_bp.route('/live/<int:ride_id>')
def live_map(ride_id):
    """Public per-ride live map — the SHARED Mapbox GL Radial view (the retired
    Leaflet map's replacement). 404 for a private or unknown ride so a guest can
    never tell a private ride from a nonexistent one. The map, compact rider table
    and altitude profile all come from the shared _radial_live.html partial, polling
    the public, PII-safe roster.json. Degrades gracefully when the Mapbox token is
    unset (BrevetHub's Vercel project has it set)."""
    ride = models.get_public_ride(ride_id)
    if not ride:
        abort(404)
    track = _route_geometry(ride)
    return render_template(
        'live_public.html',
        ride=ride,
        mapbox_token=current_app.config.get('MAPBOX_ACCESS_TOKEN') or '',
        route_polyline=_map_polyline(track),
        elevation_profile=radial.build_elevation_profile(track or []),
        roster_url=url_for('live.live_roster', ride_id=ride_id),
        poll_seconds=LIVE_POLL_SECONDS,
    )


@live_bp.route('/live/<int:ride_id>/positions.json')
def live_positions(ride_id):
    """JSON position trail for the map poll. Re-checks is_public here (defense in
    depth — never trust that the map route already gated it) and returns ONLY
    lat/lng/recorded_at, so no rider identity (email/rider_id) ever leaks."""
    ride = models.get_public_ride(ride_id)
    if not ride:
        abort(404)
    rows = models.get_ride_positions(ride_id, limit=_MAX_TRAIL_POINTS)
    points = [
        {'lat': float(r['lat']), 'lng': float(r['lng']),
         'recorded_at': r['recorded_at'].isoformat() if r.get('recorded_at') else None}
        for r in rows
    ]
    return jsonify({'ride_id': ride_id, 'positions': points})


@live_bp.route('/live/<int:ride_id>/roster.json')
def live_roster(ride_id):
    """PUBLIC, PII-safe roster poll for the shared Radial live view.

    Guest-reachable: gated on rp_ride.is_public (or the ride's owner for a private
    preview). Returns ONLY a display_name + coarse position + derived stats + an
    opaque per-view key — NEVER rider_id / email / google_id (the shared
    build_radial_roster strips them). Opted-in riders only (get_live_positions_rp
    enforces enabled + attach), so a rider who stops sharing disappears within one
    poll. Fail-soft: a load/telemetry error degrades to an empty/base roster, never
    a 500."""
    ride = models.get_ride(ride_id)
    rider_id = session.get('rider_id')
    if not ride or not (ride.get('is_public') or ride.get('rider_id') == rider_id):
        abort(404)

    now = datetime.now(timezone.utc)
    roster = _ride_roster(ride, now)

    return jsonify({
        'ride_id': ride_id,
        'roster': roster,
        'server_time': now.isoformat(),
        'stale_after_minutes': STALE_AFTER_MINUTES,
        'poll_seconds': LIVE_POLL_SECONDS,
    })


def _ride_roster(ride, now):
    """The PUBLIC, PII-safe roster list for a single ride, built with the shared
    radial builder. Extracted so both the per-ride roster.json and the event-scoped
    roster poll (which aggregates several rides) share the exact same privacy-shaped
    build — display_name + coarse position + derived stats + an opaque per-view key,
    never rider_id / email. Fail-soft: a positions/history load error degrades that
    ride to an empty/base contribution, never a 500."""
    ride_id = ride['id']
    since = now - timedelta(hours=DISPLAY_WINDOW_HOURS)
    try:
        rows = models.get_live_positions_rp(ride_id, since)
    except Exception:  # noqa: BLE001 — never 500 the public poll
        current_app.logger.exception('live roster: positions load failed for ride %s', ride_id)
        rows = []

    # PUBLIC name = the rider's real display_name (never the email local-part the
    # authenticated `name` field falls back to), defaulting a NULL to a neutral token.
    # Setting it explicitly means the shared builder never reads the email-bearing
    # `name`, so no email can reach the world-viewable payload.
    for row in rows:
        row['display_name'] = (row.get('display_name') or '').strip() or 'Rider'

    ctx = _ride_live_context(ride)
    history_by = {}
    for row in rows:
        try:
            history_by[row['rider_id']] = models.get_rider_position_history_rp(
                ride_id, row['rider_id'], since) or []
        except Exception:  # noqa: BLE001 — history is best-effort; base row instead
            current_app.logger.exception(
                'live roster: history load failed for rider %s on ride %s',
                row['rider_id'], ride_id)
            history_by[row['rider_id']] = []

    return radial.build_radial_roster(
        rows, ctx, now, history_by, ride_id=ride_id, anchor='first_fix',
        min_history=MIN_HISTORY_FOR_PLAN, stateless_fallback=False,
        stale_after_minutes=STALE_AFTER_MINUTES)


# --------------------------------------------------------------------------- #
# Event-scoped live view — every calendar brevet gets a Live link, keyed by the
# event rather than a single ride. BrevetHub events (rp_brevet_event) and rides
# (rp_ride) are disjoint tables (unlike Team Asha, where event.id == ride.id), so
# the calendar cannot point at a ride directly; this view resolves the event to any
# public rides linked to it and shows the SAME shared Radial map. A future or quiet
# event shows its route with an empty "waiting for riders" roster (no ride needed).
# --------------------------------------------------------------------------- #
def _event_route_track(event):
    """Elevation track [{lat,lng,dist_m,e_m}] for an event's route, feeding the
    event live view's map polyline + altitude profile. Reads the cron-warmed,
    route-keyed rp_route_geometry_cache first (guest-safe — no RWGPS fetch on the
    request path for a warmed route); only a cold cache falls back to a best-effort
    live fetch so an un-warmed event still draws its route. None when the event has
    no route or nothing resolves."""
    route_id = extract_rwgps_route_id(event.get('rwgps_url')) if event else None
    if not route_id:
        return None
    try:
        track = models.get_rp_route_elevation_track(route_id)
    except Exception as exc:  # noqa: BLE001 — cache read is optional; degrade to fetch
        current_app.logger.warning(
            'event live: cached geometry read failed for route %s: %s', route_id, exc)
        track = None
    if track:
        return track
    return _route_geometry({'rwgps_url': event.get('rwgps_url')})


@live_bp.route('/live/event/<int:event_id>')
def event_live_map(event_id):
    """Public event-scoped live map — the SHARED Mapbox GL Radial view keyed by a
    calendar brevet, so EVERY event card can carry a Live link. Renders the event's
    route (cache-warmed geometry) plus the combined PII-safe roster of any public
    rides linked to the event; an event with no live rides simply shows the route and
    an empty rider table. 404 for an unknown event. Degrades gracefully when the
    Mapbox token is unset."""
    event = models.get_brevet_event_full(event_id)
    if not event:
        abort(404)
    track = _event_route_track(event)
    return render_template(
        'event_live.html',
        event=event,
        mapbox_token=current_app.config.get('MAPBOX_ACCESS_TOKEN') or '',
        route_polyline=_map_polyline(track),
        elevation_profile=radial.build_elevation_profile(track or []),
        roster_url=url_for('live.event_live_roster', event_id=event_id),
        poll_seconds=LIVE_POLL_SECONDS,
    )


@live_bp.route('/live/event/<int:event_id>/roster.json')
def event_live_roster(event_id):
    """PUBLIC, PII-safe roster poll for the event-scoped live view — the union of the
    rosters of EVERY public ride linked to the event (explicit FK or a public-ride
    name+date match), so all riders on the brevet appear on one map. 404 for an
    unknown event (so a phantom id cannot be probed). Each ride is gated on
    is_public again (defense in depth) and built with the shared per-ride builder, so
    no rider id / email ever reaches the payload. Fail-soft: a resolution or per-ride
    error drops that contribution rather than 500-ing the public poll. Riders are
    ordered by route progress so the combined list reads as one leaderboard."""
    event = models.get_brevet_event(event_id)
    if not event:
        abort(404)

    now = datetime.now(timezone.utc)
    try:
        ride_ids = models.get_live_ride_ids_for_event(event_id)
    except Exception:  # noqa: BLE001 — never 500 the public poll
        current_app.logger.exception(
            'event roster: ride resolution failed for event %s', event_id)
        ride_ids = []

    combined = []
    for ride_id in ride_ids:
        try:
            ride = models.get_ride(ride_id)
            if not ride or not ride.get('is_public'):
                continue
            combined.extend(_ride_roster(ride, now))
        except Exception:  # noqa: BLE001 — one bad ride never sinks the public poll
            current_app.logger.exception(
                'event roster: ride %s contribution failed', ride_id)
            continue

    # One leaderboard across rides: furthest-along first, un-positioned riders last.
    combined.sort(key=lambda r: (r.get('route_position_mi') is not None,
                                 r.get('route_position_mi') or 0), reverse=True)

    return jsonify({
        'event_id': event_id,
        'roster': combined,
        'server_time': now.isoformat(),
        'stale_after_minutes': STALE_AFTER_MINUTES,
        'poll_seconds': LIVE_POLL_SECONDS,
    })


# --------------------------------------------------------------------------- #
# Rider-facing: create/flag a live ride + share its URL
# --------------------------------------------------------------------------- #
@live_bp.route('/live/new', methods=['GET', 'POST'])
@profile_required
def live_new():
    """Create a ride and (optionally) flag it public+live in one step, then land on
    its shareable public /live/<id> URL. GET also lists the rider's own rides with
    their share links + a per-ride public toggle."""
    rider_id = session['rider_id']

    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        if not name:
            flash('Give your ride a name.', 'error')
            return redirect(url_for('live.live_new'))

        distance_km = None
        raw_distance = (request.form.get('distance_km') or '').strip()
        if raw_distance:
            try:
                distance_km = int(raw_distance)
            except ValueError:
                flash('Distance must be a whole number of kilometers.', 'error')
                return redirect(url_for('live.live_new'))

        rider = models.get_rider_by_id(rider_id)
        make_public = request.form.get('is_public') == 'on'
        ride_id = models.create_ride(
            rider_id,
            name=name,
            distance_km=distance_km,
            is_public=make_public,
            club_id=(rider.get('club_id') if rider else None),
            status=models.RideStatus.GOING.value,
        )
        if not ride_id:
            flash('Could not create the ride. Please try again.', 'error')
            return redirect(url_for('live.live_new'))
        if make_public:
            flash('Ride created. Share the link below so anyone can follow along.',
                  'success')
            return redirect(url_for('live.live_map', ride_id=ride_id))
        flash('Ride created. Make it public when you are ready to share it.',
              'success')
        return redirect(url_for('live.live_new'))

    rides = models.get_rider_rides(rider_id)
    # Upcoming brevets the rider can point a ride at (the per-ride "link to event"
    # control). Fail-soft: a load error just omits the linker, never 500s the page.
    try:
        events = models.get_upcoming_events()
    except Exception as e:  # noqa: BLE001 — the linker is optional; never 500 here
        current_app.logger.warning('live_new: upcoming events load failed: %s', e)
        events = []
    return render_template('live_new.html', rides=rides, events=events)


@live_bp.route('/live/<int:ride_id>/public', methods=['POST'])
@profile_required
def set_public(ride_id):
    """Flip one of the rider's OWN rides public/private. Owner-scoped in the model
    UPDATE, so a non-owner's POST changes nothing."""
    rider_id = session['rider_id']
    make_public = request.form.get('is_public') == 'on'
    updated = models.set_ride_public(ride_id, rider_id, make_public)
    if not updated:
        flash('That ride is not one of yours.', 'error')
    else:
        flash('Ride is now public — share its link.' if make_public
              else 'Ride is no longer public.', 'success')
    return redirect(url_for('live.live_new'))


@live_bp.route('/live/<int:ride_id>/link-event', methods=['POST'])
@profile_required
def link_event(ride_id):
    """Link (or unlink) one of the rider OWN rides to a calendar event.

    This is the association entry point for the event-scoped live view: an owner
    points their live ride at an rp_brevet_event, so the ride's rider appears on that
    event's Live map (models.get_live_ride_ids_for_event). Owner-scoped in the model
    UPDATE (rider_id-filtered), so a non-owner POST changes nothing. An empty
    event_id unlinks (clears the FK). A non-empty event_id must name an existing
    event (else 404), so a ride is never linked to a phantom event; a malformed id
    is rejected with a flash rather than a 500."""
    rider_id = session['rider_id']
    raw = (request.form.get('event_id') or '').strip()

    if raw == '':
        event_id = None                       # unlink — clear the FK back to NULL
    else:
        try:
            event_id = int(raw)
        except ValueError:
            flash('That is not a valid brevet to link to.', 'error')
            return redirect(url_for('live.live_new'))
        if not models.get_brevet_event(event_id):
            abort(404)

    updated = models.set_ride_event(ride_id, rider_id, event_id)
    if not updated:
        flash('That ride is not one of yours.', 'error')
    elif event_id is None:
        flash('Ride unlinked from its brevet.', 'success')
    else:
        flash('Ride linked to the brevet — you now appear on its Live map.',
              'success')
    return redirect(url_for('live.live_new'))


# --------------------------------------------------------------------------- #
# Member live tracking (Surface B): self-scoped setup + accessibility gate
#
# A logged-in rider may attach THEMSELVES to a ride, and view its member map, iff
# the ride is public OR they own it (a private ride they don't own is
# indistinguishable from a nonexistent one → 404). Registration is self-scoped:
# every write targets the SESSION rider's own rp_live_tracking row, so a rider can
# only ever set their own tracking prefs — never another rider's. Attaching to a
# public ride is how the map becomes genuinely multi-rider.
# --------------------------------------------------------------------------- #
def _accessible_ride(ride_id, rider_id):
    """Return the ride row if the session rider may attach to / view it, else None.

    Accessible ⇔ ``is_public`` OR the rider owns it. A None return maps to 404 at
    the call site (a private ride a rider doesn't own must not be distinguishable
    from a nonexistent one). Resolvable directly from the get_ride row, which
    exposes both rider_id and is_public."""
    ride = models.get_ride(ride_id)
    if not ride:
        return None
    if ride.get('is_public') or ride.get('rider_id') == rider_id:
        return ride
    return None


@live_bp.route('/live/settings', methods=['GET', 'POST'])
@profile_required
def live_settings():
    """Master live-tracking opt-in toggle for the SESSION rider (self-scoped).

    The Garmin LiveTrack link itself is set per-ride on each ride's member map (it
    changes every ride), not here — mirroring Team Asha's split of a global toggle
    from the per-ride link."""
    rider_id = session['rider_id']

    if request.method == 'POST':
        enabled = request.form.get('enabled') == 'on'
        ok = models.upsert_rider_live_tracking_rp(rider_id, enabled)
        if ok:
            flash('Live tracking ' + ('enabled.' if enabled else 'disabled.'),
                  'success')
        else:
            flash('Could not save your live-tracking settings. Please try again.',
                  'error')
        return redirect(url_for('live.live_settings'))

    tracking = models.get_live_tracking_rp(rider_id)
    return render_template('live_settings.html', tracking=tracking)


@live_bp.route('/live/<int:ride_id>/garmin', methods=['POST'])
@profile_required
def ride_garmin_link(ride_id):
    """Register (or clear) the SESSION rider's Garmin LiveTrack link FOR THIS RIDE.

    Self-scoped + accessibility-gated: the ride must be public or owned by the
    rider (else 404), and the write only ever touches the session rider's own
    rp_live_tracking row (set_ride_garmin_rp / clear_ride_garmin_rp take the
    session rider_id as the subject). Garmin mints a fresh session each ride, so
    the link lives on the ride, not in global settings; saving opts the rider in
    and points tracking at this ride, clearing removes it (master toggle
    untouched). Any logged-in rider attaching to a PUBLIC ride is exactly what
    makes the member map multi-rider."""
    rider_id = session['rider_id']
    ride = _accessible_ride(ride_id, rider_id)
    if not ride:
        abort(404)

    action = request.form.get('action', 'save')
    if action == 'clear':
        models.clear_ride_garmin_rp(rider_id, ride_id)
        flash('Garmin LiveTrack link removed for this ride.', 'success')
        return redirect(url_for('live.live_ride_map', ride_id=ride_id))

    session_url = (request.form.get('garmin_session_url') or '').strip()
    parsed = parse_session(session_url) if session_url else None
    if not parsed:
        flash('That does not look like a Garmin LiveTrack link. Expected '
              'https://livetrack.garmin.com/session/.../token/...', 'warning')
        return redirect(url_for('live.live_ride_map', ride_id=ride_id))

    ok = models.set_ride_garmin_rp(rider_id, ride_id, session_url, parsed['token'])
    # Don't promise a specific short interval: on the current Vercel Hobby schedule
    # the ingest cron runs daily, so a position only appears after the next poll.
    # Near-real-time is a deploy-time upgrade (Vercel Pro → per-minute cron); see
    # the poll cron docstring + the PR's deploy prerequisites.
    flash('Garmin LiveTrack linked for this ride — your position appears after the '
          'next tracking poll runs.' if ok
          else 'Could not save your Garmin link. Please try again.',
          'success' if ok else 'error')
    return redirect(url_for('live.live_ride_map', ride_id=ride_id))


# --------------------------------------------------------------------------- #
# Rider-facing: position ingestion (owner-only, JSON API)
# --------------------------------------------------------------------------- #
@live_bp.route('/api/rides/<int:ride_id>/position', methods=['POST'])
def post_position(ride_id):
    """Append a {lat,lng,recorded_at} breadcrumb for the ride's OWNER.

    Auth ladder (all JSON, no redirects — this is an API):
      - no session rider           → 401
      - unknown ride               → 404
      - session rider != ride owner → 403
      - lat/lng missing/out-of-range → 400
    Only after all four pass is the position inserted. ``recorded_at`` is an
    optional ISO-8601 string (the device's fix time); omitted → the DB stamps NOW().
    """
    rider_id = session.get('rider_id')
    if not rider_id:
        return jsonify({'error': 'Authentication required'}), 401

    ride = models.get_ride(ride_id)
    if not ride:
        return jsonify({'error': 'Ride not found'}), 404
    if ride.get('rider_id') != rider_id:
        current_app.logger.warning(
            'live: rider %s tried to post a position for ride %s owned by %s',
            rider_id, ride_id, ride.get('rider_id'))
        return jsonify({'error': 'You can only post positions for your own ride'}), 403

    payload = request.get_json(silent=True) or request.form
    lat = _valid_coord(payload.get('lat'), -90.0, 90.0)
    lng = _valid_coord(payload.get('lng'), -180.0, 180.0)
    if lat is None or lng is None:
        return jsonify({'error': 'lat and lng are required and must be valid coordinates'}), 400

    recorded_raw = payload.get('recorded_at')
    recorded_at = str(recorded_raw).strip() if recorded_raw is not None else None
    recorded_at = recorded_at or None
    if recorded_at is not None:
        try:
            datetime.fromisoformat(recorded_at)
        except ValueError:
            return jsonify({'error': 'recorded_at must be an ISO-8601 timestamp'}), 400

    models.insert_position(ride_id, rider_id, lat, lng, recorded_at=recorded_at)
    return jsonify({'ok': True}), 200


# --------------------------------------------------------------------------- #
# Browser beacon (Mission 3, Feature 1) — a rider streams their OWN phone location
# to a ride's member map. Two gates, both mandatory before any point is stored:
#   - CONSENT: a persistent, revocable opt-in (rp_live_tracking.enabled). Without
#     it the rider is never inserted and never appears in the member poll/map.
#   - SELF-SCOPE: the rider is ALWAYS the trusted session identity; a client-
#     supplied rider id is ignored, so a rider can only ever stream their own phone.
# The beacon page mirrors the parent phone-share UI on the shared design system.
# --------------------------------------------------------------------------- #
@live_bp.route('/live/share')
@profile_required
def live_share():
    """Phone beacon page: stream this device location to a ride's member map.

    Opened standalone (the rider picks up their active ride) or from a ride's
    member map with ``?ride_id=`` so the beacon streams to THAT ride — the
    multi-rider public-ride join. @profile_required (a completed-profile rider);
    the map appearance itself is still gated on the consent toggle below."""
    rider_id = session['rider_id']
    ride_id = request.args.get('ride_id', type=int)
    tracking = models.get_live_tracking_rp(rider_id)
    opted_in = bool(tracking and tracking.get('enabled'))
    return render_template('live_beacon.html', opted_in=opted_in, ride_id=ride_id,
                           poll_seconds=LIVE_POLL_SECONDS)


@live_bp.route('/api/live/sharing', methods=['GET'])
def live_sharing_status():
    """Read the current rider's location-sharing consent flag (JSON).

    Lets the beacon UI reflect the real server-side opt-in on open. 401 for an
    anonymous caller, 403 for a signed-in rider whose profile is incomplete (the
    same bar the member surface enforces)."""
    rider = bearer_or_session_rider()
    if not rider:
        return jsonify({'error': 'Authentication required'}), 401
    if not rider['profile_completed']:
        return jsonify({'error': 'Complete your profile to share your location'}), 403
    tracking = models.get_live_tracking_rp(rider['id'])
    return jsonify({'enabled': bool(tracking and tracking.get('enabled'))})


@live_bp.route('/api/live/sharing', methods=['POST'])
def live_sharing_toggle():
    """Set the current rider's location-sharing consent on/off (JSON).

    Tapping "Start sharing" (with the on-page privacy note) is the consent act;
    tapping stop, or POSTing enabled=false, revokes it and drops the rider off the
    member map on the next poll. Preserves any registered Garmin session
    (upsert_rider_live_tracking_rp touches only the enabled flag). Self-scoped to
    the session rider. 401 anon / 403 incomplete profile."""
    rider = bearer_or_session_rider()
    if not rider:
        return jsonify({'error': 'Authentication required'}), 401
    if not rider['profile_completed']:
        return jsonify({'error': 'Complete your profile to share your location'}), 403
    enabled = bool((request.get_json(silent=True) or {}).get('enabled'))
    ok = models.upsert_rider_live_tracking_rp(rider['id'], enabled)
    if not ok:
        # A failed opt-in write must NOT read as success — the beacon page checks
        # r.ok before starting geolocation, so a silent 200 here would start a watch
        # whose fixes are then all rejected (tracking was never enabled).
        current_app.logger.warning(
            'live: sharing toggle write failed for rider %s (enabled=%s)',
            rider['id'], enabled)
        return jsonify({'ok': False, 'enabled': enabled,
                        'error': 'Could not save your sharing setting. Please try again.'}), 500
    return jsonify({'ok': True, 'enabled': enabled})


def _resolve_beacon_ride(rider_id, payload, tracking):
    """Resolve (and persist) the ride a beacon fix attaches to, self-scoped and
    accessibility-gated. Returns ``(ride_id, None)`` on success or
    ``(None, (response, status))`` to reject.

    Ladder — every resolved ride is re-checked through ``_accessible_ride`` (public
    OR owned) before any write, so a rider can never beacon to a ride they cannot
    access:
      1. explicit ``ride_id`` in the body — the primary multi-rider join (a public
         ride the rider opened the beacon from); an inaccessible/private non-owned
         ride is refused (403). A malformed id is a 400.
      2. the rider's current active ride (a prior beacon/Garmin attach), re-gated
         defensively.
      3. cold-start auto-attach — deterministically pick the rider's nearest
         accessible attached ride (owned, or a public ride they already stream to),
         re-gated before persisting.
      4. none accessible → 400 (open a ride live map to share for that ride).
    A newly picked ride is persisted to active_ride_id so the member poll surfaces
    the rider on it (without clobbering a Garmin link). If that attach write fails,
    the fix is NOT stored (500) — a 200 whose point never appears on the map would
    be a silent lie, because the member poll requires active_ride_id == ride_id."""
    explicit = payload.get('ride_id')
    if explicit is not None and str(explicit).strip() != '':
        try:
            rid = int(explicit)
        except (TypeError, ValueError):
            return None, (jsonify({'error': 'ride_id must be a ride id'}), 400)
        if not _accessible_ride(rid, rider_id):
            current_app.logger.warning(
                'live: rider %s beacon to inaccessible ride %s refused', rider_id, rid)
            return None, (jsonify({'error': 'You cannot share to that ride'}), 403)
        if (tracking or {}).get('active_ride_id') != rid:
            attach_error = _persist_attach(rider_id, rid)
            if attach_error:
                return None, attach_error
        return rid, None

    active = (tracking or {}).get('active_ride_id')
    if active and _accessible_ride(active, rider_id):
        return active, None

    # Cold start: no explicit and no (still-accessible) active ride. Deterministically
    # attach to the rider's nearest accessible ride, re-gated before the write so an
    # inaccessible ride can never slip through even if the resolver widened.
    picked = models.get_auto_attach_ride_rp(rider_id)
    if picked and _accessible_ride(picked['id'], rider_id):
        attach_error = _persist_attach(rider_id, picked['id'])
        if attach_error:
            return None, attach_error
        return picked['id'], None

    return None, (jsonify(
        {'error': 'Open a ride live map to share for that ride'}), 400)


def _persist_attach(rider_id, ride_id):
    """Point the rider's active ride at ``ride_id``. Returns None on success or an
    error ``(response, 500)`` tuple when the write fails — the caller must NOT store
    the fix in that case, since the member poll only surfaces a rider whose
    active_ride_id matches the point's ride."""
    if models.set_active_ride_rp(rider_id, ride_id):
        return None
    current_app.logger.warning(
        'live: could not attach rider %s to ride %s for beacon', rider_id, ride_id)
    return (jsonify(
        {'error': 'Could not start sharing for that ride. Please try again.'}), 500)


@live_bp.route('/api/live/beacon', methods=['POST'])
def live_beacon():
    """Ingest one geolocation fix for the SESSION rider (source='beacon').

    Auth ladder (JSON API, no redirects): no session rider → 401; a signed-in
    rider with an incomplete profile → 403; no location-sharing consent → 403;
    lat/lng missing or out of range → 400. The rider is ALWAYS the trusted session
    identity — a client-supplied rider id is ignored — so a rider can only stream
    their own phone. The ride the fix attaches to is resolved + accessibility-gated
    by ``_resolve_beacon_ride`` (an inaccessible ride → 403, no accessible ride →
    400). Only after every gate passes is the point stored via
    insert_live_position_rp with source='beacon'."""
    rider = bearer_or_session_rider()
    if not rider:
        return jsonify({'error': 'Authentication required'}), 401
    if not rider['profile_completed']:
        return jsonify({'error': 'Complete your profile to share your location'}), 403
    rider_id = rider['id']

    tracking = models.get_live_tracking_rp(rider_id)
    if not (tracking and tracking.get('enabled')):
        return jsonify({'error': 'Turn on location sharing first'}), 403

    payload = request.get_json(silent=True) or {}
    lat = _valid_coord(payload.get('lat'), -90.0, 90.0)
    lng = _valid_coord(payload.get('lng'), -180.0, 180.0)
    if lat is None or lng is None:
        return jsonify(
            {'error': 'lat and lng are required and must be valid coordinates'}), 400

    ride_id, error = _resolve_beacon_ride(rider_id, payload, tracking)
    if ride_id is None:
        return error

    now = datetime.now(timezone.utc)
    ok = models.insert_live_position_rp(
        rider_id=rider_id,          # session only — a client value is never trusted
        lat=lat, lng=lng,
        accuracy=payload.get('accuracy'), speed=payload.get('speed'),
        recorded_at=now, source='beacon', ride_id=ride_id,
    )
    if not ok:
        return jsonify({'error': 'Invalid coordinates'}), 400
    return jsonify({'ok': True, 'ride_id': ride_id,
                    'recorded_at': now.isoformat()}), 200


# --------------------------------------------------------------------------- #
# Member live map (Surface B) — the multi-rider Mapbox map. PHYSICALLY SPLIT from
# the anonymous guest surface (live_map / positions.json): those stay nameless and
# world-viewable; everything named/telemetry lives here behind @profile_required +
# the accessibility gate, so a rider name can never reach the anonymous poll.
# --------------------------------------------------------------------------- #
# Show points from the last 24h; grey/fade a rider whose latest point is older
# than 10 min (mirrors Team Asha's live map tuning).
DISPLAY_WINDOW_HOURS = 24
STALE_AFTER_MINUTES = 10

# Every rider on the member map has opted in AND attached to THIS ride, so they
# are all "going" for it. BrevetHub rides carry no per-rider signup table (unlike
# TA's rider_ride.status), so the dot colour is a single status here; staleness
# fade + the name distinguish riders. Kept as a map for forward-compatibility.
STATUS_COLORS = {'going': '#16a34a'}
DEFAULT_STATUS = 'going'
DEFAULT_COLOR = '#16a34a'

# Cap polyline payload — long brevet routes can have tens of thousands of points.
_MAX_POLYLINE_POINTS = 1000


def _route_geometry(ride):
    """Fetch + downsample the ride's RWGPS route ONCE into a track
    [{lat, lng, dist_m, e_m}] feeding BOTH the shared map polyline and the altitude
    profile (so the page makes a single route fetch, not two). Fail-soft → None on
    any missing route / fetch error, so the map still renders with rider dots only."""
    route_id = extract_rwgps_route_id(ride.get('rwgps_url')) if ride else None
    if not route_id:
        return None
    try:
        route = fetch_route(route_id)
    except Exception as exc:  # noqa: BLE001 — fail-soft, route line is optional
        current_app.logger.warning('live: RWGPS route %s fetch failed: %s', route_id, exc)
        return None
    tps = [tp for tp in ((route or {}).get('track_points') or [])
           if tp.get('x') is not None and tp.get('y') is not None]
    if not tps:
        return None
    step = max(1, len(tps) // _MAX_CONTEXT_TRACK_POINTS)
    track = []
    for tp in tps[::step]:
        track.append({'lat': float(tp['y']), 'lng': float(tp['x']),
                      'dist_m': float(tp.get('d') or 0),
                      'e_m': float(tp['e']) if tp.get('e') is not None else None})
    return track


def _map_polyline(track):
    """[[lng, lat], …] for the Mapbox route line from a _route_geometry track,
    capped to _MAX_POLYLINE_POINTS. None when there's no track."""
    if not track:
        return None
    coords = [[t['lng'], t['lat']] for t in track]
    if len(coords) > _MAX_POLYLINE_POINTS:
        step = len(coords) // _MAX_POLYLINE_POINTS + 1
        coords = coords[::step]
    return coords


@live_bp.route('/live/<int:ride_id>/map')
@profile_required
def live_ride_map(ride_id):
    """Member map for a ride — the SAME shared Mapbox GL Radial view the guest map
    uses, plus the member controls (Garmin link + phone beacon). @profile_required +
    accessibility-gated (public OR own ride, else 404). Renders even when
    MAPBOX_ACCESS_TOKEN is unset (graceful "map unavailable" fallback — never a
    500). The map / table / profile come from the shared _radial_live.html partial,
    polling the public roster.json (the authenticated live-positions.json remains for
    the mobile client)."""
    rider_id = session['rider_id']
    ride = _accessible_ride(ride_id, rider_id)
    if not ride:
        abort(404)

    tracking = models.get_live_tracking_rp(rider_id)
    # Only surface the Garmin link as linked here if it's pointed at THIS ride, so a
    # link saved for another ride doesn't look active on this one.
    garmin_here = bool(tracking and tracking.get('garmin_session_url')
                       and tracking.get('active_ride_id') == ride_id)
    garmin_url = tracking.get('garmin_session_url') if garmin_here else ''

    track = _route_geometry(ride)
    return render_template(
        'live_ride_map.html',
        ride=ride,
        mapbox_token=current_app.config.get('MAPBOX_ACCESS_TOKEN') or '',
        route_polyline=_map_polyline(track),
        elevation_profile=radial.build_elevation_profile(track or []),
        roster_url=url_for('live.live_roster', ride_id=ride_id),
        poll_seconds=LIVE_POLL_SECONDS,
        stale_after_minutes=STALE_AFTER_MINUTES,
        opted_in=bool(tracking and tracking.get('enabled')),
        garmin_here=garmin_here,
        garmin_url=garmin_url,
    )


# --------------------------------------------------------------------------- #
# Plan-aware telemetry (Mission 2). The heavy per-ride context (route geometry +
# the in-tenant real plan) is built once per poll; the shared telemetry engine
# (brevethub.shared.live_telemetry) then computes each rider's plan-aware numbers
# from their position history. Everything is fail-soft: a missing route or plan, a
# thin history, or an off-route rider degrades to the Mission-1 basics — never a
# 500. Mirrors the parent app live HUD field shapes; wind / toughness / charts are
# deferred (see the PR deferred-scope note).
# --------------------------------------------------------------------------- #
def _as_utc_dt(dt):
    """Treat a naive datetime as UTC so it compares with tz-aware DB timestamps."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _resolve_ride_plan(ride):
    """Resolve a live ride to its real plan WITHIN ITS OWN TENANT.

    Mirrors how the parent app resolves a ride to a plan — RWGPS route id first,
    then a name match — but BOTH paths are club-scoped so a rider is never graded
    against another club plan (rwgps_route_id is not unique across clubs). Returns a
    plan row or None when nothing in-tenant matches.
    """
    club_id = ride.get('club_id')
    route_id = extract_rwgps_route_id(ride.get('rwgps_url'))
    plan = (models.get_brevet_route_plan_by_route_id_rp(route_id, club_id)
            if route_id else None)
    if plan:
        return plan
    name = ride.get('name')
    if not name:
        return None
    candidates = models.get_brevet_route_plan_candidates_rp(club_id) or []
    return match_plan(name, candidates)


def _ride_live_context(ride):
    """Per-ride telemetry context: route geometry + the in-tenant real plan.

    Fail-soft — any missing route / plan / fetch error yields a context with
    has_route / has_plan False, so the member poll still renders the Mission-1
    basics and never 500s. No weather or wind (deferred to a later mission).

    Keys: track [{lat,lng,dist_m,e_m}], cum_ascent_ft[], total_dist_m,
    total_ascent_ft, plan_stops [{distance_miles,cum_time_min,location,stop_type}],
    plan_total_mi, plan_cutoff_hours, has_route, has_plan, base_plan_id,
    base_plan_name. base_plan_id/name seed the plan-selector allow-set + label.
    """
    ctx = {'track': [], 'cum_ascent_ft': [], 'total_dist_m': None,
           'total_ascent_ft': None, 'plan_stops': [], 'plan_total_mi': 0.0,
           'plan_cutoff_hours': None, 'has_route': False, 'has_plan': False,
           'base_plan_id': None, 'base_plan_name': None}
    if not ride:
        return ctx

    # In-tenant plan for banked-time / next-control / OTL grading.
    try:
        plan = _resolve_ride_plan(ride)
        if plan:
            ctx['base_plan_id'] = plan.get('id')
            ctx['base_plan_name'] = plan.get('name')
            ctx['plan_cutoff_hours'] = (float(plan['cutoff_hours'])
                                        if plan.get('cutoff_hours') else None)
            ctx['plan_total_mi'] = float(plan.get('total_distance_miles') or 0)
            stops = models.get_brevet_route_plan_stops(plan['id'])
            # Live grading is pinned to the conservative, MEAL-FREE checkpoints and
            # timing — byte-for-byte what the single plan produced before the variant
            # split. Exclude the stored meal-break rows (stop_type='meal', zero segment
            # distance) and subtract the accumulated preceding dwell (each meal row
            # carries its dwell in segment_time_min) from every control's cum_time_min,
            # recovering the moving-elapsed series the interpolators expect. Meal breaks
            # are display-only; they never feed banked-time / next-control / OTL grading.
            graded = []
            cum_dwell = 0.0
            for s in (stops or []):
                if s.get('stop_type') == 'meal':
                    cum_dwell += float(s.get('segment_time_min') or 0)
                    continue
                if s.get('distance_miles') is None or s.get('cum_time_min') is None:
                    continue
                graded.append(
                    {'distance_miles': float(s['distance_miles']),
                     'cum_time_min': float(s['cum_time_min']) - cum_dwell,
                     'location': s.get('location'),
                     'stop_type': s.get('stop_type')})
            ctx['plan_stops'] = graded
            ctx['has_plan'] = len(ctx['plan_stops']) >= 2
    except Exception as exc:  # noqa: BLE001 — plan is optional; degrade to base
        current_app.logger.warning('live: plan resolution failed for ride %s: %s',
                                   ride.get('id'), exc)
        ctx['plan_stops'] = []

    # Route geometry: downsampled track + cumulative ascent (feet) for distance /
    # remaining / on-route projection / ascent split.
    route_id = extract_rwgps_route_id(ride.get('rwgps_url'))
    if route_id:
        try:
            route = fetch_route(route_id)
            tps = [tp for tp in ((route or {}).get('track_points') or [])
                   if tp.get('x') is not None and tp.get('y') is not None]
            if tps:
                step = max(1, len(tps) // _MAX_CONTEXT_TRACK_POINTS)
                track, cum_ascent, prev_e, cum = [], [], None, 0.0
                for tp in tps[::step]:
                    e_ft = (tp.get('e') or 0) * tlm.METERS_TO_FEET
                    if prev_e is not None and e_ft > prev_e:
                        cum += e_ft - prev_e
                    prev_e = e_ft
                    track.append({'lat': float(tp['y']), 'lng': float(tp['x']),
                                  'dist_m': float(tp.get('d') or 0),
                                  'e_m': float(tp['e']) if tp.get('e') is not None else None})
                    cum_ascent.append(round(cum))
                ctx['track'] = track
                ctx['cum_ascent_ft'] = cum_ascent
                ctx['total_dist_m'] = track[-1]['dist_m'] if track else None
                ctx['total_ascent_ft'] = cum_ascent[-1] if cum_ascent else None
                ctx['has_route'] = True
        except Exception as exc:  # noqa: BLE001 — route geometry is optional
            current_app.logger.warning('live: route %s context failed: %s', route_id, exc)
    return ctx


def _base_now_block(row, history, now):
    """Source-agnostic 'now' metrics, always present: speed, activity, elapsed,
    moving, stopped, HR, power, cadence.

    Elapsed is anchored to the rider FIRST fix — BrevetHub ride start_at is the
    creation time, not the brevet start, so anchoring on the first tracked point is
    honest and event-lookup-free (a documented parity deviation). Moving + stopped
    then reconcile to elapsed. Pure — needs no route. Returns
    (start_dt, elapsed_min, now_block)."""
    start = _as_utc_dt(history[0]['recorded_at']) if history else None
    elapsed_min = None
    if start is not None and start <= now:
        elapsed_min = round((now - start).total_seconds() / 60)
    moving_min, stopped_min = tlm.moving_stopped(history)
    if elapsed_min is not None:
        stopped_min = round(max(0.0, elapsed_min - moving_min), 1)
    speed_ms = tlm.latest_speed_ms(history)
    if speed_ms is None and row.get('speed') is not None:
        try:
            speed_ms = float(row['speed'])
        except (TypeError, ValueError):
            speed_ms = None
    now_block = {
        'speed_mph': round(speed_ms * MS_TO_MPH, 1) if speed_ms is not None else None,
        'activity': tlm.activity_from_speed(speed_ms),
        'elapsed_min': elapsed_min,
        'moving_min': moving_min,
        'stopped_min': stopped_min,
        'heart_rate': _num_or_none(row.get('heart_rate'), int),
        'power': _num_or_none(row.get('power'), int),
        'cadence': _num_or_none(row.get('cadence'), int),
    }
    return start, elapsed_min, now_block


def _base_telemetry(row, history, now):
    """The Mission-1 base block (now-metrics only, all plan-aware fields absent).

    Used for a ride with no route / no plan, a thin-history or off-route rider, and
    as the last-resort fallback when full assembly raises — so the endpoint always
    returns position + speed + telemetry and never 500s."""
    try:
        _, _, now_block = _base_now_block(row, history, now)
    except Exception:  # noqa: BLE001 — never let the now-block sink the payload
        now_block = None
    return {'on_route': None, 'now': now_block, 'remaining': None,
            'next_control': None, 'finish': None, 'time_banked_cutoff_min': None,
            'time_banked_plan_min': None, 'plan': None, 'detailed_after_ride': True}


def _rider_telemetry(row, ctx, now, history):
    """Assemble one rider's plan-aware telemetry block via the SHARED composer.

    This delegates to shared.live_radial.compose_rider_telemetry — the single rich
    assembler both apps use — so BrevetHub and the parent app can never fork the
    per-rider math. BrevetHub anchors elapsed on the rider's FIRST fix (its ride
    start_at is the creation time, not the brevet start — a documented parity
    deviation), needs >= MIN_HISTORY_FOR_PLAN fixes before projecting, and carries no
    weather context (so it passes no wind hook and no club timezone). Everything
    still degrades gracefully — route-relative fields only when on route, plan-
    relative fields only when a >= 2-stop in-tenant plan resolved — never a 500."""
    start = _as_utc_dt(history[0]['recorded_at']) if history else None
    return radial.compose_rider_telemetry(
        row, ctx, now, history, plan_stops=ctx.get('plan_stops'), start=start,
        tz=None, min_history=MIN_HISTORY_FOR_PLAN, stateless_fallback=False)


def _plan_dot_color(telemetry):
    """Map dot color from plan timing: off-route or unknown -> grey; behind -> red;
    ahead / on plan -> green. Falls back to the default going color when no plan is
    resolved, so a ride without a plan does not regress."""
    if telemetry is not None and telemetry.get('on_route') is False:
        return PLAN_UNKNOWN_COLOR
    plan = (telemetry or {}).get('plan')
    if plan and plan.get('status'):
        return PLAN_BEHIND_COLOR if plan['status'] == 'behind' else PLAN_AHEAD_COLOR
    return DEFAULT_COLOR


# --------------------------------------------------------------------------- #
# Plan selector (Mission 3, Feature 3) — IDOR-safe allow-set + selected-plan
# resolution, mirroring the parent app's pattern (services/live.py). A viewer on
# Surface B may pick which plan riders are graded against; the allow-set is the
# SOLE source of resolvable plans, so a crafted ?plan_id can never resolve a plan
# outside it. Today a live ride has exactly one real plan (its rp_brevet_route_plan
# base plan), so there is a single option; the allow-set machinery is built
# correctly so custom per-rider plans slot in later without a rewrite.
# --------------------------------------------------------------------------- #
PLAN_BASE = 'base'


def _available_plans(base_plan_id, base_plan_name=None):
    """Assemble the plan-selector allow-set AND the dropdown option list.

    Returns (options, allowed_custom_ids):
      options: [{'id': 'base'|<int>, 'name', 'is_custom'}] — base first. Today the
               only option is the ride's base plan (custom plans are not built yet).
      allowed_custom_ids: the set of numeric plan ids a viewer may resolve BEYOND
               base. Empty today. The resolver refuses any id not in this set, so a
               crafted ?plan_id can never grade against an out-of-set plan (IDOR).

    Built as an allow-set so custom plans slot in later without a rewrite: a future
    change appends each visible custom plan id to allowed_custom_ids and a matching
    option here — the resolver already enforces membership."""
    options = [{'id': PLAN_BASE,
                'name': (base_plan_name or 'Base plan'),
                'is_custom': False}]
    allowed_custom_ids = set()
    # (When custom plans exist: extend allowed_custom_ids + options here, gated on
    # the viewer's visibility of each plan. base_plan_id anchors that lookup.)
    _ = base_plan_id
    return options, allowed_custom_ids


def _selected_plan_stops(requested_plan_id, ctx, allowed_custom_ids):
    """Resolve ``requested_plan_id`` STRICTLY against the allow-set. Returns
    (applied_id, override_stops):

      applied_id: the value actually applied — 'base', or an int id that is in the
                  allow-set. A rejected, unknown, or malformed id falls back to
                  'base' (surfaced in the response, logged — never a silent
                  misgrade).
      override_stops: the plan stops every rider is graded against; today always the
                  base plan stops, since no custom plan exists to override with.

    IDOR guard: a numeric id NOT in allowed_custom_ids (a plan the viewer may not
    resolve) is refused and logged, so no out-of-set plan can leak through the query
    string."""
    base_stops = ctx.get('plan_stops') if ctx else None
    if requested_plan_id in (None, '', PLAN_BASE):
        return PLAN_BASE, base_stops

    try:
        pid = int(requested_plan_id)
    except (TypeError, ValueError):
        current_app.logger.warning(
            'live: rejected malformed plan_id %r -> base fallback', requested_plan_id)
        return PLAN_BASE, base_stops

    if pid not in allowed_custom_ids:
        current_app.logger.warning(
            'live: rejected out-of-allowset plan_id %s -> base fallback', pid)
        return PLAN_BASE, base_stops

    # An allowed custom plan would resolve its own stops here (not built yet).
    return PLAN_BASE, base_stops


@live_bp.route('/live/<int:ride_id>/live-positions.json')
def live_member_positions(ride_id):
    """Web Surface-B poll: latest NAMED position + telemetry per opted-in rider on a
    ride (path form; ride_id in the URL). Delegates to the shared builder below."""
    return _member_positions_response(ride_id)


@live_bp.route('/api/live/positions')
def live_positions_api():
    """Mobile Bearer live poll: the SAME member positions payload as the web
    Surface-B endpoint, addressed as ``/api/live/positions?ride_id=<id>&plan_id=``
    to match the BrevetHub mobile client contract (useLivePositions). Bearer OR
    session auth, the identical accessibility gate + no-PII rules. A missing
    ride_id is a 400 (no ride to resolve)."""
    ride_id = request.args.get('ride_id', type=int)
    if not ride_id:
        return jsonify({'error': 'ride_id is required'}), 400
    return _member_positions_response(ride_id)


def _member_positions_response(ride_id):
    """Build the member live-positions JSON for ``ride_id``. Shared by the web
    (/live/<id>/live-positions.json) and mobile (/api/live/positions?ride_id=)
    routes so the two can never drift.

    Auth ladder (JSON API — no redirects): no session/Bearer rider → 401; a rider
    whose profile is INCOMPLETE → 403 (OAuth sets rider_id before signup finishes,
    so this endpoint must enforce the SAME profile-completeness bar as the
    @profile_required member page — otherwise a half-signed-up account could read
    named locations/telemetry the gated UI never shows it); inaccessible (private +
    non-owner) or unknown ride → 404. The anonymous positions.json poll never
    selects a name. Each entry:
      {rider_id, name, lat, lng, status, color, recorded_at, minutes_ago, stale,
       source, telemetry:{speed, heart_rate, power, cadence}}"""
    rider = bearer_or_session_rider()
    if not rider:
        return jsonify({'error': 'Authentication required'}), 401
    if not rider['profile_completed']:
        return jsonify({'error': 'Complete your profile to view live tracking'}), 403
    rider_id = rider['id']

    ride = _accessible_ride(ride_id, rider_id)
    if not ride:
        abort(404)

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=DISPLAY_WINDOW_HOURS)
    rows = models.get_live_positions_rp(ride_id, since)

    # Build the plan-aware context ONCE per poll (route geometry + in-tenant plan),
    # then compute each rider plan-aware numbers from their position history. The
    # whole thing is fail-soft: a bad context degrades every rider to the base block.
    ctx = _ride_live_context(ride)

    # Plan selector (Feature 3): the allow-set is the sole source of resolvable
    # plans, so a crafted ?plan_id can never grade against an out-of-set plan. The
    # requested id is resolved strictly against it; a rejected id falls back to base.
    plan_options, allowed_custom_ids = _available_plans(
        ctx.get('base_plan_id'), ctx.get('base_plan_name'))
    applied_plan_id, _override_stops = _selected_plan_stops(
        request.args.get('plan_id'), ctx, allowed_custom_ids)

    positions = []
    for row in rows:
        recorded_at = row['recorded_at']
        if recorded_at is not None and recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        minutes_ago = (max(0, int((now - recorded_at).total_seconds() // 60))
                       if recorded_at is not None else None)

        history = []
        try:
            history = models.get_rider_position_history_rp(
                ride_id, row['rider_id'], since) or []
        except Exception:  # noqa: BLE001 — history is best-effort; fall back to base
            current_app.logger.exception(
                'live: history load failed for rider %s on ride %s',
                row['rider_id'], ride_id)
        try:
            telemetry = _rider_telemetry(row, ctx, now, history)
        except Exception:  # noqa: BLE001 — never 500 the poll on a telemetry bug
            current_app.logger.exception(
                'live: telemetry failed for rider %s on ride %s',
                row['rider_id'], ride_id)
            telemetry = _base_telemetry(row, history, now)

        positions.append({
            'rider_id': row['rider_id'],
            'name': (row['name'] or 'Rider').strip(),
            'lat': float(row['lat']),
            'lng': float(row['lng']),
            'status': DEFAULT_STATUS,
            'color': STATUS_COLORS.get(DEFAULT_STATUS, DEFAULT_COLOR),
            # Plan-timing dot color (ahead=green / behind=red / grey=unknown); falls
            # back to the status color when no plan is matched, so it is always safe.
            'plan_color': _plan_dot_color(telemetry),
            'recorded_at': recorded_at.isoformat() if recorded_at is not None else None,
            'minutes_ago': minutes_ago,
            'stale': (minutes_ago is not None and minutes_ago > STALE_AFTER_MINUTES),
            'source': row.get('source') or 'garmin',
            'telemetry': telemetry,
        })

    return jsonify({
        'ride_id': ride_id,
        'positions': positions,
        'stale_after_minutes': STALE_AFTER_MINUTES,
        'server_time': now.isoformat(),
        # Plan selector (Feature 3): the options the viewer may pick (base only for a
        # single-plan ride) and the plan actually APPLIED — 'base' or an int id (a
        # rejected id echoes as 'base').
        'plans': plan_options,
        'selected_plan_id': applied_plan_id,
    })


def _num_or_none(value, cast):
    """Best-effort cast for JSON output; None on failure (NUMERIC → native)."""
    if value is None:
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None
