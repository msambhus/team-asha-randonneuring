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
from datetime import datetime

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)

from brevethub import models
from brevethub.decorators import profile_required
from brevethub.shared.garmin_livetrack import parse_session

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
    flash('Garmin LiveTrack linked for this ride — you should appear within a few '
          'minutes.' if ok else 'Could not save your Garmin link. Please try again.',
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
