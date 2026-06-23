"""Live rider location tracking routes (PR 1 — Garmin LiveTrack).

Club-login-only, opt-in. Three surfaces:
  GET/POST /live/settings        — rider opts in + registers a Garmin LiveTrack URL
  GET      /ride/<id>/live       — per-ride map (RWGPS route line + live rider dots)
  GET      /api/live/positions   — JSON: latest point per opted-in GOING rider

The poll cron that writes positions lives in routes/cron.py.
"""
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, jsonify, current_app, flash, abort)

from auth import profile_required, api_login_required
from models import (get_ride_by_id, get_live_tracking, set_live_tracking,
                    get_latest_positions_for_ride, insert_live_position)
from services.garmin_livetrack import parse_session
from services.rwgps import extract_rwgps_route_id, fetch_route

live_bp = Blueprint('live', __name__)

# Display tuning (see plan): show points from the last 24h; grey/fade dots
# whose latest point is older than 10 minutes.
DISPLAY_WINDOW_HOURS = 24
STALE_AFTER_MINUTES = 10

# RideStatus → dot color. Only GOING riders appear on the per-ride map today,
# but the map carries the full mapping for forward-compatibility.
STATUS_COLORS = {
    'GOING': '#16a34a',       # green
    'INTERESTED': '#2563eb',  # blue
    'MAYBE': '#d97706',       # amber
    'FINISHED': '#6b7280',    # grey
}
DEFAULT_COLOR = '#16a34a'

# Cap polyline payload — long brevet routes can have tens of thousands of points.
_MAX_POLYLINE_POINTS = 1000


def _build_route_polyline(ride):
    """Return a downsampled [[lng, lat], ...] polyline for the ride's RWGPS route.

    Fail-soft: returns None on any missing route / fetch error so the map still
    renders with rider dots only.
    """
    rwgps_url = (ride.get('rwgps_url_team') or ride.get('rwgps_url')) if ride else None
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
        # Always keep the final point so the line reaches the finish.
        if downsampled[-1] != coords[-1]:
            downsampled.append(coords[-1])
        coords = downsampled
    return coords


@live_bp.route('/live/settings', methods=['GET', 'POST'])
@profile_required
def live_settings():
    """Opt-in toggle + Garmin LiveTrack session registration for the current rider."""
    rider_id = session['rider_id']

    if request.method == 'POST':
        enabled = request.form.get('enabled') == 'on'
        session_url = (request.form.get('garmin_session_url') or '').strip()
        token = None
        if session_url:
            parsed = parse_session(session_url)
            if not parsed:
                flash('That does not look like a Garmin LiveTrack link. Expected '
                      'https://livetrack.garmin.com/session/.../token/...', 'warning')
                tracking = get_live_tracking(rider_id)
                return render_template('live_settings.html', tracking=tracking)
            token = parsed['token']

        # A Garmin link is optional — riders can opt in for the browser
        # beacon (Share my location) without registering a Garmin session.
        ok = set_live_tracking(rider_id, enabled, session_url or None, token)
        if ok:
            flash('Live tracking ' + ('enabled.' if enabled else 'disabled.'), 'success')
        else:
            flash('Could not save your live-tracking settings. Please try again.', 'danger')
        return redirect(url_for('live.live_settings'))

    tracking = get_live_tracking(rider_id)
    return render_template('live_settings.html', tracking=tracking)


@live_bp.route('/ride/<int:ride_id>/live')
@profile_required
def ride_live_map(ride_id):
    """Per-ride live map: RWGPS route line + live dots for opted-in GOING riders."""
    ride = get_ride_by_id(ride_id)
    if not ride:
        abort(404)

    mapbox_token = current_app.config.get('MAPBOX_ACCESS_TOKEN', '')
    route_polyline = _build_route_polyline(ride)

    return render_template(
        'live.html',
        ride=ride,
        mapbox_token=mapbox_token,
        route_polyline=route_polyline,
        stale_after_minutes=STALE_AFTER_MINUTES,
    )


@live_bp.route('/api/live/positions')
@api_login_required
def live_positions():
    """JSON: latest position per opted-in GOING rider for ?ride_id=.

    Club-only: requires a completed profile (session['rider_id']).
    """
    if not session.get('rider_id'):
        return jsonify({'error': 'Complete your profile to view live tracking'}), 403

    ride_id = request.args.get('ride_id', type=int)
    if not ride_id:
        return jsonify({'error': 'ride_id is required'}), 400

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=DISPLAY_WINDOW_HOURS)
    rows = get_latest_positions_for_ride(ride_id, since)

    positions = []
    for row in rows:
        recorded_at = row['recorded_at']
        # recorded_at is timestamptz (tz-aware); guard naive values just in case.
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        minutes_ago = max(0, int((now - recorded_at).total_seconds() // 60))
        status = row['status']
        positions.append({
            'rider_id': row['rider_id'],
            'name': (row['name'] or '').strip(),
            'lat': float(row['lat']),
            'lng': float(row['lng']),
            'status': status,
            'color': STATUS_COLORS.get(status, DEFAULT_COLOR),
            'recorded_at': recorded_at.isoformat(),
            'minutes_ago': minutes_ago,
            'stale': minutes_ago > STALE_AFTER_MINUTES,
        })

    return jsonify({
        'ride_id': ride_id,
        'positions': positions,
        'stale_after_minutes': STALE_AFTER_MINUTES,
        'server_time': now.isoformat(),
    })


@live_bp.route('/live/share')
@profile_required
def live_share():
    """Mobile page: stream this device's location to the club (browser beacon)."""
    rider_id = session['rider_id']
    tracking = get_live_tracking(rider_id)
    opted_in = bool(tracking and tracking.get('enabled'))
    return render_template('live_share.html', opted_in=opted_in)


@live_bp.route('/api/live/beacon', methods=['POST'])
@api_login_required
def live_beacon():
    """Accept a browser-geolocation position for the CURRENT rider only.

    Club-only (completed profile) and opt-in (rider must have enabled tracking).
    The rider is always taken from the session — any client-supplied rider id is
    ignored — and coordinates are validated/clamped before insert.
    """
    rider_id = session.get('rider_id')
    if not rider_id:
        return jsonify({'error': 'Complete your profile to share your location'}), 403

    tracking = get_live_tracking(rider_id)
    if not (tracking and tracking.get('enabled')):
        return jsonify({'error': 'Live tracking is off — enable it in settings first'}), 403

    data = request.get_json(silent=True) or {}
    lat = data.get('lat')
    lng = data.get('lng')
    accuracy = data.get('accuracy')
    if lat is None or lng is None:
        return jsonify({'error': 'lat and lng are required'}), 400

    now = datetime.now(timezone.utc)
    ok = insert_live_position(
        rider_id=rider_id,          # session only — client value never trusted
        lat=lat, lng=lng, accuracy=accuracy,
        recorded_at=now, source='beacon',
    )
    if not ok:
        return jsonify({'error': 'Invalid coordinates'}), 400

    return jsonify({'ok': True, 'recorded_at': now.isoformat()})
