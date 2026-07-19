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
from brevethub.decorators import current_rider, profile_required
from brevethub.shared.garmin_livetrack import parse_session
from brevethub.shared.rwgps import extract_rwgps_route_id, fetch_route

live_bp = Blueprint('live', __name__)

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
    """Public list of rides opted into live tracking (is_public = TRUE only)."""
    rides = models.get_public_rides()
    return render_template('live_list.html', rides=rides,
                           poll_seconds=LIVE_POLL_SECONDS)


@live_bp.route('/live/<int:ride_id>')
def live_map(ride_id):
    """Public per-ride live map. 404 for a private or unknown ride so a guest can
    never tell a private ride from a nonexistent one."""
    ride = models.get_public_ride(ride_id)
    if not ride:
        abort(404)
    return render_template('live_map.html', ride=ride,
                           poll_seconds=LIVE_POLL_SECONDS)


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
    return render_template('live_new.html', rides=rides)


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


def _build_route_polyline(ride):
    """Return a downsampled [[lng, lat], ...] polyline for the ride's RWGPS route.

    Fail-soft: returns None on any missing route / fetch error so the map still
    renders with rider dots only (never 500s). Credentials fall back to the
    BrevetHub config's RWGPS_* env inside the shared engine."""
    rwgps_url = ride.get('rwgps_url') if ride else None
    route_id = extract_rwgps_route_id(rwgps_url)
    if not route_id:
        return None
    try:
        route_data = fetch_route(route_id)
    except Exception as exc:  # noqa: BLE001 — fail-soft, route line is optional
        current_app.logger.warning('live: RWGPS route %s fetch failed: %s', route_id, exc)
        return None

    track_points = (route_data or {}).get('track_points') or []
    coords = [
        [float(tp['x']), float(tp['y'])]
        for tp in track_points
        if tp.get('x') is not None and tp.get('y') is not None
    ]
    if not coords:
        return None

    if len(coords) > _MAX_POLYLINE_POINTS:
        step = len(coords) // _MAX_POLYLINE_POINTS + 1
        downsampled = coords[::step]
        if downsampled[-1] != coords[-1]:
            downsampled.append(coords[-1])
        coords = downsampled
    return coords


@live_bp.route('/live/<int:ride_id>/map')
@profile_required
def live_ride_map(ride_id):
    """Member multi-rider Mapbox map for a ride: named dots + telemetry + the RWGPS
    route polyline. @profile_required + accessibility-gated (public OR own ride,
    else 404). Renders even when MAPBOX_ACCESS_TOKEN is unset (graceful
    "map unavailable" fallback — never a 500)."""
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

    return render_template(
        'live_ride_map.html',
        ride=ride,
        mapbox_token=current_app.config.get('MAPBOX_ACCESS_TOKEN') or '',
        route_polyline=_build_route_polyline(ride),
        poll_seconds=LIVE_POLL_SECONDS,
        stale_after_minutes=STALE_AFTER_MINUTES,
        opted_in=bool(tracking and tracking.get('enabled')),
        garmin_here=garmin_here,
        garmin_url=garmin_url,
    )


@live_bp.route('/live/<int:ride_id>/live-positions.json')
def live_member_positions(ride_id):
    """JSON: latest NAMED position + telemetry per opted-in rider attached to a ride.

    Auth ladder (JSON API — no redirects): no session rider → 401; a session rider
    whose profile is INCOMPLETE → 403 (OAuth sets rider_id before signup finishes,
    so this endpoint must enforce the SAME profile-completeness bar as the
    @profile_required member page — otherwise a half-signed-up account could read
    named locations/telemetry the gated UI never shows it); inaccessible (private +
    non-owner) or unknown ride → 404. The anonymous positions.json poll never
    selects a name. Each entry:
      {rider_id, name, lat, lng, status, color, recorded_at, minutes_ago, stale,
       source, telemetry:{speed, heart_rate, power, cadence}}"""
    rider = current_rider()
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

    positions = []
    for row in rows:
        recorded_at = row['recorded_at']
        if recorded_at is not None and recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        minutes_ago = (max(0, int((now - recorded_at).total_seconds() // 60))
                       if recorded_at is not None else None)
        positions.append({
            'rider_id': row['rider_id'],
            'name': (row['name'] or 'Rider').strip(),
            'lat': float(row['lat']),
            'lng': float(row['lng']),
            'status': DEFAULT_STATUS,
            'color': STATUS_COLORS.get(DEFAULT_STATUS, DEFAULT_COLOR),
            'recorded_at': recorded_at.isoformat() if recorded_at is not None else None,
            'minutes_ago': minutes_ago,
            'stale': (minutes_ago is not None and minutes_ago > STALE_AFTER_MINUTES),
            'source': row.get('source') or 'garmin',
            'telemetry': {
                'speed': _num_or_none(row.get('speed'), float),
                'heart_rate': _num_or_none(row.get('heart_rate'), int),
                'power': _num_or_none(row.get('power'), int),
                'cadence': _num_or_none(row.get('cadence'), int),
            },
        })

    return jsonify({
        'ride_id': ride_id,
        'positions': positions,
        'stale_after_minutes': STALE_AFTER_MINUTES,
        'server_time': now.isoformat(),
    })


def _num_or_none(value, cast):
    """Best-effort cast for JSON output; None on failure (NUMERIC → native)."""
    if value is None:
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None
