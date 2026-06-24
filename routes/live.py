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
from cache import cache, CACHE_TIMEOUT
from models import (get_ride_by_id, get_live_tracking, set_live_tracking,
                    get_latest_positions_for_ride, insert_live_position,
                    get_rider_upcoming_signups, get_ride_plan_stops,
                    get_positions_for_rider_since)
from services.garmin_livetrack import parse_session
from services.rwgps import extract_rwgps_route_id, fetch_route
from services import live_telemetry as tlm
from services.weather import (sample_track_points, fetch_route_weather,
                              calculate_bearing, headwind_component, wind_label)

live_bp = Blueprint('live', __name__)

M_TO_MI = 1 / 1609.344
KMH_TO_MPH = 0.621371
MS_TO_MPH = 2.236936
_MAX_CONTEXT_TRACK_POINTS = 2000

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


@live_bp.route('/live')
@profile_required
def live_hub():
    """Live tracking hub: share from this phone, set up Garmin, or open a ride's map."""
    rider_id = session['rider_id']
    tracking = get_live_tracking(rider_id)
    upcoming = get_rider_upcoming_signups(rider_id)
    return render_template(
        'live_hub.html',
        opted_in=bool(tracking and tracking.get('enabled')),
        has_garmin=bool(tracking and tracking.get('garmin_session_token')),
        upcoming=upcoming,
    )


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
    tracking = get_live_tracking(session['rider_id'])
    opted_in = bool(tracking and tracking.get('enabled'))

    return render_template(
        'live.html',
        ride=ride,
        mapbox_token=mapbox_token,
        route_polyline=route_polyline,
        stale_after_minutes=STALE_AFTER_MINUTES,
        opted_in=opted_in,
    )


def _build_wind_by_dist(track_points):
    """Best-effort [{dist_m, headwind_kmh}] using CURRENT wind sampled along the
    route. Returns None on any failure (headwinds then degrade gracefully)."""
    try:
        samples = sample_track_points(track_points)
        if len(samples) < 2:
            return None
        forecasts = fetch_route_weather(samples)
        if not forecasts or len(forecasts) != len(samples):
            return None
        out = []
        for i, (s, fc) in enumerate(zip(samples, forecasts)):
            hourly = (fc or {}).get('hourly') or {}
            times = hourly.get('time') or []
            ws = hourly.get('wind_speed_10m') or []
            wd = hourly.get('wind_direction_10m') or []
            if not times or not ws or not wd:
                continue
            offset = (fc or {}).get('utc_offset_seconds') or 0
            now_local = datetime.now(timezone.utc) + timedelta(seconds=offset)
            idx, best = 0, None
            for j, t in enumerate(times):
                try:
                    dt = datetime.fromisoformat(t)
                except ValueError:
                    continue
                diff = abs((dt.replace(tzinfo=None) - now_local.replace(tzinfo=None)).total_seconds())
                if best is None or diff < best:
                    best, idx = diff, j
            if idx >= len(ws) or idx >= len(wd):
                continue   # partial hourly payload — skip this sample
            nxt = samples[i + 1] if i + 1 < len(samples) else samples[i - 1]
            bearing = calculate_bearing(s['lat'], s['lng'], nxt['lat'], nxt['lng'])
            if i + 1 >= len(samples):
                bearing = (bearing + 180) % 360
            hw = headwind_component(ws[idx], wd[idx], bearing)
            out.append({'dist_m': s['distance_m'], 'headwind_kmh': hw})
        return out or None
    except Exception:
        return None


@cache.memoize(CACHE_TIMEOUT)
def _ride_live_context(ride_id):
    """Per-ride context for telemetry, computed ONCE and cached (~5 min) so the
    per-poll path never re-fetches RWGPS / weather. Returns a plain dict.

    Keys: track [{lat,lng,dist_m}], cum_ascent_ft[], total_dist_m,
    total_ascent_ft, plan_stops [{distance_miles,cum_time_min}], wind_by_dist,
    ride_start_iso, has_route, has_plan.
    """
    ride = get_ride_by_id(ride_id)
    ctx = {'track': [], 'cum_ascent_ft': [], 'total_dist_m': None,
           'total_ascent_ft': None, 'plan_stops': [], 'wind_by_dist': None,
           'ride_start_iso': None, 'has_route': False, 'has_plan': False}
    if not ride:
        return ctx

    # Ride start = ride date + plan start_time (for elapsed/plan comparison).
    try:
        start_t = ride.get('plan_start_time') or ride.get('start_time') or '07:00'
        hh, mm = str(start_t).split(':')[:2]
        d = ride['date']
        if isinstance(d, str):
            d = date.fromisoformat(d)
        ctx['ride_start_iso'] = datetime(d.year, d.month, d.day, int(hh), int(mm),
                                         tzinfo=timezone.utc).isoformat()
    except Exception:
        ctx['ride_start_iso'] = None

    # Plan stops for on/behind-plan comparison.
    try:
        if ride.get('ride_plan_id'):
            ctx['plan_stops'] = [
                {'distance_miles': float(s['distance_miles']),
                 'cum_time_min': float(s['cum_time_min'])}
                for s in get_ride_plan_stops(ride['ride_plan_id'])
                if s.get('distance_miles') is not None and s.get('cum_time_min') is not None
            ]
            ctx['has_plan'] = len(ctx['plan_stops']) >= 2
    except Exception:
        ctx['plan_stops'] = []

    # Route geometry: track + cumulative ascent (downsampled for the cache).
    rwgps_url = ride.get('rwgps_url_team') or ride.get('rwgps_url')
    route_id = extract_rwgps_route_id(rwgps_url)
    if route_id:
        try:
            route = fetch_route(route_id)
            tps = [tp for tp in (route.get('track_points') or [])
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
                                  'dist_m': float(tp.get('d') or 0)})
                    cum_ascent.append(round(cum))
                ctx['track'] = track
                ctx['cum_ascent_ft'] = cum_ascent
                ctx['total_dist_m'] = track[-1]['dist_m'] if track else None
                ctx['total_ascent_ft'] = cum_ascent[-1] if cum_ascent else None
                ctx['has_route'] = True
                ctx['wind_by_dist'] = _build_wind_by_dist(tps)
        except Exception as exc:  # noqa: BLE001 — telemetry is best-effort
            current_app.logger.warning('live ctx: route %s failed: %s', route_id, exc)
    return ctx


def _rider_telemetry(row, ctx, now, history):
    """Assemble the telemetry block for one rider.

    Source-agnostic fields (speed, activity, moving/stopped, elapsed, HR/power)
    are always included. Route-relative fields (distance done/left, ascent,
    headwinds, toughness, plan delta) are only included when the rider is
    actually ON the route — otherwise `on_route` is False and they are omitted
    so we never report a bogus mileage from snapping to the nearest line.
    """
    lat, lng = float(row['lat']), float(row['lng'])

    moving_min, stopped_min = tlm.moving_stopped(history)
    speed_ms = tlm.latest_speed_ms(history)
    if speed_ms is None and row.get('speed') is not None:
        try:
            speed_ms = float(row['speed'])
        except (TypeError, ValueError):
            speed_ms = None

    # Elapsed is only meaningful once the ride has actually started.
    elapsed_min = None
    start = None
    if ctx.get('ride_start_iso'):
        try:
            start = datetime.fromisoformat(ctx['ride_start_iso'])
        except ValueError:
            start = None
    if start is not None and start <= now:
        elapsed_min = round((now - start).total_seconds() / 60)

    now_block = {
        'speed_mph': round(speed_ms * MS_TO_MPH, 1) if speed_ms is not None else None,
        'activity': tlm.activity_from_speed(speed_ms),
        'elapsed_min': elapsed_min,
        'moving_min': moving_min,
        'stopped_min': stopped_min,
        'heart_rate': row.get('heart_rate'),
        'power': row.get('power'),
        'cadence': row.get('cadence'),
    }
    base = {'on_route': None, 'now': now_block, 'remaining': None,
            'plan': None, 'detailed_after_ride': True}

    if not ctx.get('has_route'):
        return base

    dist_m, idx, off_by_m = tlm.project_to_route(lat, lng, ctx['track'])
    on_route = (dist_m is not None and off_by_m is not None
                and off_by_m <= tlm.ON_ROUTE_MAX_M)
    if not on_route:
        base['on_route'] = False
        return base

    remaining_m = tlm.remaining_distance_m(ctx['total_dist_m'], dist_m)
    ascent_done, ascent_left = tlm.ascent_split(ctx['cum_ascent_ft'], idx, ctx['total_ascent_ft'])
    hw_done, hw_ahead = tlm.headwinds_split(ctx.get('wind_by_dist'), dist_m)
    tuf = tlm.toughness_remaining(ascent_left, remaining_m)
    dist_mi = dist_m * M_TO_MI
    remaining_mi = (remaining_m or 0) * M_TO_MI

    # Time-left estimate from the rider's own moving average.
    time_left_min = None
    avg_mph = (dist_mi / (moving_min / 60.0)) if moving_min else None
    if avg_mph and avg_mph > 1:
        time_left_min = round(remaining_mi / avg_mph * 60)

    delta = tlm.plan_delta(dist_mi, elapsed_min, ctx.get('plan_stops'))

    def mph(kmh):
        return round(kmh * KMH_TO_MPH, 1) if kmh is not None else None

    now_block['distance_mi'] = round(dist_mi, 1)
    now_block['ascent_done_ft'] = ascent_done
    now_block['headwind_done_mph'] = mph(hw_done)
    now_block['headwind_done_label'] = wind_label(hw_done) if hw_done is not None else None

    return {
        'on_route': True,
        'now': now_block,
        'remaining': {
            'distance_mi': round(remaining_mi, 1),
            'ascent_left_ft': ascent_left,
            'headwind_ahead_mph': mph(hw_ahead),
            'headwind_ahead_label': wind_label(hw_ahead) if hw_ahead is not None else None,
            'time_left_min': time_left_min,
            'toughness': tuf,
        },
        'plan': ({'delta_min': delta,
                  'status': 'ahead' if delta > 2 else ('behind' if delta < -2 else 'on')}
                 if delta is not None else None),
        'detailed_after_ride': True,   # power / pedaling-vs-coasting come from Strava post-ride
    }


@live_bp.route('/api/live/positions')
@api_login_required
def live_positions():
    """JSON: latest position + live telemetry per opted-in GOING rider for ?ride_id=.

    Club-only: requires a completed profile (session['rider_id']). The heavy
    route/weather context is cached per ride; only per-rider numbers are
    recomputed each poll.
    """
    if not session.get('rider_id'):
        return jsonify({'error': 'Complete your profile to view live tracking'}), 403

    ride_id = request.args.get('ride_id', type=int)
    if not ride_id:
        return jsonify({'error': 'ride_id is required'}), 400

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=DISPLAY_WINDOW_HOURS)
    rows = get_latest_positions_for_ride(ride_id, since)

    ctx = _ride_live_context(ride_id) if rows else None

    has_route = bool(ctx and ctx.get('has_route'))
    track = ctx.get('track') if has_route else None

    positions = []
    for row in rows:
        recorded_at = row['recorded_at']
        # recorded_at is timestamptz (tz-aware); guard naive values just in case.
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        minutes_ago = max(0, int((now - recorded_at).total_seconds() // 60))
        status = row['status']

        history = get_positions_for_rider_since(
            row['rider_id'], now - timedelta(hours=DISPLAY_WINDOW_HOURS))
        telemetry = None
        try:
            telemetry = _rider_telemetry(row, ctx, now, history)
        except Exception:
            current_app.logger.exception('live telemetry failed for rider %s', row['rider_id'])

        trail = tlm.build_trail(history, track)   # on-route breadcrumb of where they rode

        # Hide a rider only when they're off-route AND have no on-route history
        # — so an off-route session (e.g. testing from home) is hidden, but a
        # momentary GPS bounce on a real ride doesn't make the rider vanish.
        if has_route and telemetry and telemetry.get('on_route') is False and not trail:
            continue
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
            'telemetry': telemetry,
            'trail': trail,
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
    speed = data.get('speed')   # m/s from the Geolocation API, when available
    if lat is None or lng is None:
        return jsonify({'error': 'lat and lng are required'}), 400

    now = datetime.now(timezone.utc)
    ok = insert_live_position(
        rider_id=rider_id,          # session only — client value never trusted
        lat=lat, lng=lng, accuracy=accuracy, speed=speed,
        recorded_at=now, source='beacon',
    )
    if not ok:
        return jsonify({'error': 'Invalid coordinates'}), 400

    return jsonify({'ok': True, 'recorded_at': now.isoformat()})
