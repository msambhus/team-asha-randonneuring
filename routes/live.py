"""Live rider location tracking routes (PR 1 — Garmin LiveTrack).

Club-login-only, opt-in. Three surfaces:
  GET/POST /live/settings        — rider opts in + registers a Garmin LiveTrack URL
  GET      /ride/<id>/live       — per-ride map (RWGPS route line + live rider dots)
  GET      /api/live/positions   — JSON: latest point per opted-in GOING rider

The poll cron that writes positions lives in routes/cron.py.
"""
import math
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, jsonify, current_app, flash, abort, g)

from auth import profile_required, token_or_session_required
from cache import cache, CACHE_TIMEOUT
from models import (get_ride_by_id, get_live_tracking, set_live_tracking_enabled,
                    set_ride_garmin, clear_ride_garmin,
                    get_latest_positions_for_ride, insert_live_position,
                    get_rider_upcoming_signups, get_ride_plan_stops,
                    get_positions_for_rider_since, get_default_time_limit)
from services.garmin_livetrack import parse_session
from services.rwgps import extract_rwgps_route_id, fetch_route
from services import live_telemetry as tlm
from services.weather import (sample_track_points, fetch_route_weather,
                              calculate_bearing, headwind_component,
                              crosswind_component, classify_wind,
                              wind_arrow_rotation, wind_arrow_glyph)

live_bp = Blueprint('live', __name__)

M_TO_MI = 1 / 1609.344
KMH_TO_MPH = 0.621371
MS_TO_MPH = 2.236936
_MAX_CONTEXT_TRACK_POINTS = 2000

# Club-local timezone. Ride start_time values (e.g. "06:00") are wall-clock
# times in the Bay Area, so elapsed-time math must interpret them in Pacific
# time and convert to UTC — not treat "06:00" as 06:00 UTC.
CLUB_TZ = ZoneInfo('America/Los_Angeles')

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
    """Master opt-in toggle + privacy info. The Garmin LiveTrack link is set
    per-ride on each ride's live map (it changes every ride), not here."""
    rider_id = session['rider_id']

    if request.method == 'POST':
        enabled = request.form.get('enabled') == 'on'
        ok = set_live_tracking_enabled(rider_id, enabled)
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
    # The Garmin link is per-ride: only show it as linked here if it's pointed
    # at THIS ride (active_ride_id), so a link saved for another ride doesn't
    # look active on this one.
    garmin_here = bool(tracking and tracking.get('garmin_session_url')
                       and tracking.get('active_ride_id') == ride_id)
    garmin_url = tracking.get('garmin_session_url') if garmin_here else ''

    return render_template(
        'live.html',
        ride=ride,
        mapbox_token=mapbox_token,
        route_polyline=route_polyline,
        stale_after_minutes=STALE_AFTER_MINUTES,
        opted_in=opted_in,
        garmin_here=garmin_here,
        garmin_url=garmin_url,
    )


@live_bp.route('/ride/<int:ride_id>/live/garmin', methods=['POST'])
@profile_required
def ride_garmin_link(ride_id):
    """Register (or clear) this rider's Garmin LiveTrack link FOR THIS RIDE.

    Garmin mints a fresh session each ride, so the link lives on the ride, not in
    global settings. Saving opts the rider in and points tracking at this ride;
    clearing removes it (master opt-in untouched, so the beacon still works).
    """
    ride = get_ride_by_id(ride_id)
    if not ride:
        abort(404)
    rider_id = session['rider_id']

    action = request.form.get('action', 'save')
    if action == 'clear':
        clear_ride_garmin(rider_id, ride_id)
        flash('Garmin LiveTrack link removed for this ride.', 'success')
        return redirect(url_for('live.ride_live_map', ride_id=ride_id))

    session_url = (request.form.get('garmin_session_url') or '').strip()
    parsed = parse_session(session_url) if session_url else None
    if not parsed:
        flash('That does not look like a Garmin LiveTrack link. Expected '
              'https://livetrack.garmin.com/session/.../token/...', 'warning')
        return redirect(url_for('live.ride_live_map', ride_id=ride_id))

    ok = set_ride_garmin(rider_id, ride_id, session_url, parsed['token'])
    flash('Garmin LiveTrack linked for this ride — you should appear within a few minutes.'
          if ok else 'Could not save your Garmin link. Please try again.',
          'success' if ok else 'danger')
    return redirect(url_for('live.ride_live_map', ride_id=ride_id))


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
            cw = crosswind_component(ws[idx], wd[idx], bearing)
            out.append({'dist_m': s['distance_m'], 'headwind_kmh': hw,
                        'crosswind_kmh': cw})
        return out or None
    except Exception:
        return None


@cache.memoize(CACHE_TIMEOUT)
def _ride_live_context(ride_id):
    """Per-ride context for telemetry, computed ONCE and cached (~5 min) so the
    per-poll path never re-fetches RWGPS / weather. Returns a plain dict.

    Keys: track [{lat,lng,dist_m}], cum_ascent_ft[], total_dist_m,
    total_ascent_ft, plan_stops [{distance_miles,cum_time_min}], wind_by_dist,
    ride_start_iso, time_limit_min, has_route, has_plan.
    """
    ride = get_ride_by_id(ride_id)
    ctx = {'track': [], 'cum_ascent_ft': [], 'total_dist_m': None,
           'total_ascent_ft': None, 'plan_stops': [], 'wind_by_dist': None,
           'ride_start_iso': None, 'time_limit_min': None,
           'has_route': False, 'has_plan': False}
    if not ride:
        return ctx

    # Overall brevet time limit (for "time left" = limit − elapsed; e.g. 40h for
    # a 600). Prefer the event's own time_limit_hours; else the standard ACP
    # allowance for the distance.
    limit_h = ride.get('time_limit_hours')
    if not limit_h and ride.get('distance_km'):
        limit_h = get_default_time_limit(ride['distance_km'])
    try:
        ctx['time_limit_min'] = round(float(limit_h) * 60) if limit_h else None
    except (TypeError, ValueError):
        ctx['time_limit_min'] = None

    # Ride start = ride date + plan start_time (for elapsed/plan comparison).
    # start_time is Bay-Area wall-clock ("06:00" = 6 AM Pacific), so build it in
    # CLUB_TZ and convert to UTC; treating it as UTC made elapsed ~7-8h too large.
    try:
        start_t = ride.get('plan_start_time') or ride.get('start_time') or '06:00'
        hh, mm = str(start_t).split(':')[:2]
        d = ride['date']
        if isinstance(d, str):
            d = date.fromisoformat(d)
        local_start = datetime(d.year, d.month, d.day, int(hh), int(mm),
                               tzinfo=CLUB_TZ)
        ctx['ride_start_iso'] = local_start.astimezone(timezone.utc).isoformat()
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

    # Use the rider's recent course over ground so an out-and-back / looped
    # route snaps them to the leg they're actually travelling, not the one
    # alongside it going the other way.
    heading = tlm.course_over_ground(history)
    dist_m, idx, off_by_m = tlm.project_to_route(lat, lng, ctx['track'],
                                                 heading_deg=heading)
    on_route = (dist_m is not None and off_by_m is not None
                and off_by_m <= tlm.ON_ROUTE_MAX_M)
    if not on_route:
        base['on_route'] = False
        return base

    remaining_m = tlm.remaining_distance_m(ctx['total_dist_m'], dist_m)
    ascent_done, ascent_left = tlm.ascent_split(ctx['cum_ascent_ft'], idx, ctx['total_ascent_ft'])
    hw_done, hw_ahead = tlm.headwinds_split(ctx.get('wind_by_dist'), dist_m)
    cw_done, cw_ahead = tlm.crosswinds_split(ctx.get('wind_by_dist'), dist_m)
    tuf = tlm.toughness_remaining(ascent_left, remaining_m)
    dist_mi = dist_m * M_TO_MI
    remaining_mi = (remaining_m or 0) * M_TO_MI

    # Time left = the brevet's overall time limit minus elapsed (e.g. 40h for a
    # 600), not a pace ETA. Clamped at 0 once the time limit is blown.
    time_left_min = None
    limit_min = ctx.get('time_limit_min')
    if limit_min is not None and elapsed_min is not None:
        time_left_min = max(0, limit_min - elapsed_min)

    delta = tlm.plan_delta(dist_mi, elapsed_min, ctx.get('plan_stops'))

    _WIND_SHORT = {'headwind': 'head', 'tailwind': 'tail', 'crosswind': 'cross'}

    def wind_descriptor(head_kmh, cross_kmh):
        """'↓ 8 mph head' — speed in mph, head/cross/tail, and a direction arrow.
        Returns (label, speed_mph) where speed_mph is the TOTAL wind magnitude
        (hypot of head+cross), i.e. what the label shows — the headwind_*_mph
        fields below carry this magnitude, not the along-track component.
        Crosswind defaults to 0 so a head/tail-only context (legacy cache) still
        classifies. 'calm' below ~1 mph."""
        if head_kmh is None:
            return None, None
        cross = cross_kmh or 0.0
        speed_mph = round(math.hypot(head_kmh, cross) * KMH_TO_MPH, 1)
        if speed_mph < 1:
            return 'calm', speed_mph
        glyph = wind_arrow_glyph(wind_arrow_rotation(head_kmh, cross))
        short = _WIND_SHORT[classify_wind(head_kmh, cross)]
        return f'{glyph} {speed_mph:g} mph {short}', speed_mph

    now_block['distance_mi'] = round(dist_mi, 1)
    now_block['ascent_done_ft'] = ascent_done
    wind_done_label, wind_done_mph = wind_descriptor(hw_done, cw_done)
    now_block['headwind_done_mph'] = wind_done_mph
    now_block['headwind_done_label'] = wind_done_label
    wind_ahead_label, wind_ahead_mph = wind_descriptor(hw_ahead, cw_ahead)

    return {
        'on_route': True,
        'now': now_block,
        'remaining': {
            'distance_mi': round(remaining_mi, 1),
            'ascent_left_ft': ascent_left,
            'headwind_ahead_mph': wind_ahead_mph,
            'headwind_ahead_label': wind_ahead_label,
            'time_left_min': time_left_min,
            'toughness': tuf,
        },
        'plan': ({'delta_min': delta,
                  'status': 'ahead' if delta > 2 else ('behind' if delta < -2 else 'on')}
                 if delta is not None else None),
        'detailed_after_ride': True,   # power / pedaling-vs-coasting come from Strava post-ride
    }


@live_bp.route('/api/live/positions')
@token_or_session_required
def live_positions():
    """JSON: latest position + live telemetry per opted-in GOING rider for ?ride_id=.

    Club-only: requires a completed profile. Auth is a web session OR a mobile
    Bearer token (g.rider_id, set by token_or_session_required). The heavy
    route/weather context is cached per ride; only per-rider numbers are
    recomputed each poll.
    """
    if not g.rider_id:
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
            row['rider_id'], now - timedelta(hours=DISPLAY_WINDOW_HOURS),
            ride_id=ride_id)
        telemetry = None
        try:
            telemetry = _rider_telemetry(row, ctx, now, history)
        except Exception:
            current_app.logger.exception('live telemetry failed for rider %s', row['rider_id'])

        trail = tlm.build_trail(history, track)   # on-route breadcrumb of where they rode

        # Off-route riders are still shown on the map (you can see where everyone
        # is) — only the route-relative telemetry is suppressed (on_route=False),
        # handled in _rider_telemetry.
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
            # How this rider's latest point was reported: 'garmin' (LiveTrack
            # device, works screen-off) or 'beacon' (this phone's browser,
            # needs the screen on). Drives the map's source badge/popup.
            'source': row.get('source') or 'beacon',
            'telemetry': telemetry,
            'trail': trail,
        })

    return jsonify({
        'ride_id': ride_id,
        'positions': positions,
        'stale_after_minutes': STALE_AFTER_MINUTES,
        'server_time': now.isoformat(),
    })


@live_bp.route('/api/live/rides')
@token_or_session_required
def live_rides():
    """JSON: the current rider's upcoming rides — for the mobile app's ride picker.

    Auth: web session OR mobile Bearer token. Returns a slim list so the app can
    choose which ride's live map to open / share on. (The full brevet calendar is
    a later milestone; this is just enough to make live tracking reachable.)
    """
    if not g.rider_id:
        return jsonify({'error': 'Complete your profile to view rides'}), 403
    from models import get_rider_upcoming_signups
    rides = get_rider_upcoming_signups(g.rider_id)
    out = [{
        'id': r['id'],
        'name': (r['name'] or '').strip(),
        'date': str(r['date']) if r.get('date') else None,
        'distance_km': r.get('distance_km'),
        'signup_status': r.get('signup_status'),
    } for r in rides]
    return jsonify({'rides': out})


@live_bp.route('/api/calendar')
@token_or_session_required
def api_calendar():
    """JSON: the upcoming brevet calendar — the mobile app's calendar tab.

    Auth: web session OR mobile Bearer token. Read-only; reuses
    get_all_upcoming_events so it shows the FULL upcoming calendar (Team Asha
    rides AND the external club brevets the team rides), matching the website's
    /upcoming page. (get_upcoming_rides is TA-club-only, which left the app
    calendar empty whenever Team Asha had no self-hosted upcoming rides.)
    """
    if not g.rider_id:
        return jsonify({'error': 'Complete your profile to view the calendar'}), 403
    from models import get_all_upcoming_events
    rides = get_all_upcoming_events()
    out = [{
        'id': r['id'],
        'name': (r.get('route_name') or r.get('name') or '').strip(),
        'date': r.get('date_str') or (str(r['date']) if r.get('date') else None),
        'distance_km': r.get('distance_km'),
        'ride_type': r.get('ride_type'),
        'start_location': r.get('start_location'),
        'club_name': r.get('club_name'),
        'signup_count': r.get('signup_count'),
        'is_team_ride': bool(r.get('is_team_ride')),
    } for r in rides]
    return jsonify({'rides': out})


@cache.memoize(CACHE_TIMEOUT)
def _ride_route_polyline(ride_id):
    """Cached [[lng,lat],...] RWGPS route line for a ride (static per ride).
    Returns None when the ride is missing or has no resolvable route."""
    ride = get_ride_by_id(ride_id)
    if not ride:
        return None
    return _build_route_polyline(ride)


@live_bp.route('/api/ride/<int:ride_id>/route')
@token_or_session_required
def api_ride_route(ride_id):
    """JSON: the RWGPS route polyline for a ride — the mobile map's route line.

    Auth: web session OR mobile Bearer token. Reuses _build_route_polyline (the
    same source the web live map draws). The polyline is large + static, so it's
    a separate cached endpoint rather than a field on the 20s position poll.
    Fail-soft: returns an empty polyline (not 404) so the map still renders dots.
    """
    if not g.rider_id:
        return jsonify({'error': 'Complete your profile to view the route'}), 403
    polyline = _ride_route_polyline(ride_id)
    return jsonify({'ride_id': ride_id, 'polyline': polyline or []})


def _resolve_base_plan(ride):
    """The base ride_plan for a ride: by ride_plan_id (FK) else by route-name match
    — the SAME resolution the web uses (services.plan_match), so a ride with no FK
    still finds its plan (e.g. the SCR 600k brevet whose ride_plan_id is null)."""
    from models import get_ride_plan_by_slug, get_all_ride_plans
    from services.plan_match import match_plan
    slug = ride.get('plan_slug')
    if slug:
        p = get_ride_plan_by_slug(slug)
        if p:
            return p
    m = match_plan(ride.get('name'), get_all_ride_plans())
    return get_ride_plan_by_slug(m['slug']) if m else None


def _emit_plan_stop(d, base_dt):
    """Serialize a stop dict that already carries computed timing fields
    (cum_time_min / arrival_time_min / time_bank_min / seg_dist / ft_per_mi)."""
    arrival = int(d.get('arrival_time_min') or 0)
    return {
        'stop_order': d.get('stop_order'),
        'location': (d.get('location') or '').strip(),
        'stop_type': d.get('stop_type') or 'waypoint',
        'stop_name': (d.get('stop_name') or '').strip() or None,
        'notes': (d.get('notes') or '').strip() or None,
        'distance_mi': round(float(d.get('distance_miles') or 0), 1),
        'seg_dist_mi': round(float(d.get('seg_dist') or 0), 1),
        'elevation_gain_ft': int(d.get('elevation_gain') or 0),
        'ft_per_mi': int(d.get('ft_per_mi') or 0),
        'segment_time_min': int(d.get('segment_time_min') or 0),
        'stop_duration_min': int(d.get('stop_duration_min') or 0),
        'cum_time_min': int(d.get('cum_time_min') or 0),
        'arrival_time_min': arrival,
        'eta': (base_dt + timedelta(minutes=arrival)).strftime('%-I:%M %p'),
        'time_bank_min': d.get('time_bank_min'),
        'is_custom_stop': bool(d.get('is_custom_stop')),
        'is_modified': bool(d.get('is_modified')),
    }


def _compute_base_timing(raw_stops, cutoff_hours, total_mi):
    """Add cum/arrival/seg_dist/ft_per_mi/time_bank to base ride_plan_stop rows
    (the web ride_plan_detail formulas). The custom path uses the custom-plan
    service's recalculate instead; both feed _emit_plan_stop."""
    out = []
    cum_time = 0
    prev_mi = 0.0
    for s in raw_stops:
        d = dict(s)
        dist_mi = float(d.get('distance_miles') or 0)
        seg_time = int(d.get('segment_time_min') or 0)
        stop_dur = int(d.get('stop_duration_min') or 0)
        elev = int(d.get('elevation_gain') or 0)
        seg_dist = round(dist_mi - prev_mi, 1)
        d['seg_dist'] = seg_dist
        d['ft_per_mi'] = int(round(elev / seg_dist)) if elev and seg_dist > 0 else 0
        cum_time += seg_time + stop_dur
        d['cum_time_min'] = cum_time
        d['arrival_time_min'] = cum_time - stop_dur
        d['time_bank_min'] = None
        if cutoff_hours and total_mi > 0 and dist_mi:
            bookend = round((dist_mi / total_mi) * cutoff_hours * 60)
            d['time_bank_min'] = bookend - d['arrival_time_min']
        out.append(d)
        prev_mi = dist_mi
    return out


@live_bp.route('/api/ride/<int:ride_id>/weather')
@token_or_session_required
def api_ride_weather(ride_id):
    """JSON: the weather forecast for a ride's route — mirrors the web /weather page.

    Auth: web session OR mobile Bearer token. Resolves the ride to its RWGPS route +
    start datetime (the plan's start time, else 07:00) and reuses build_weather_payload
    — the SAME pipeline the web /api/weather-map uses — so the mobile screen renders
    the identical table / wind-map / charts. Returns {available: false, reason, message}
    (HTTP 200) for rides with no route, no date, in the past, or beyond Open-Meteo's
    16-day forecast horizon, so the app can show a friendly note instead of an error.
    """
    if not g.rider_id:
        return jsonify({'error': 'Complete your profile to view weather'}), 403

    from datetime import date as _date, time as _time
    from routes.weather import build_weather_payload  # local: avoids import cycle

    ride = get_ride_by_id(ride_id)
    if not ride:
        return jsonify({'error': 'Ride not found'}), 404

    route_id = extract_rwgps_route_id(ride.get('rwgps_url_team') or ride.get('rwgps_url'))
    if not route_id:
        return jsonify({'available': False, 'reason': 'no_route',
                        'message': 'No route is attached to this ride yet.'})

    ride_date = ride.get('date')
    if not ride_date:
        return jsonify({'available': False, 'reason': 'no_date',
                        'message': 'This ride has no date yet.'})
    if ride_date < _date.today():
        return jsonify({'available': False, 'reason': 'past_ride',
                        'message': 'This ride has already happened.'})

    # Start datetime = ride date at the ride's start time (fallback 07:00 local).
    start_str = ride.get('start_time') or ride.get('plan_start_time') or '07:00'
    try:
        parts = str(start_str).split(':')
        start_dt = datetime.combine(ride_date, _time(int(parts[0]), int(parts[1])))
    except (ValueError, TypeError, IndexError):
        start_dt = datetime.combine(ride_date, _time(7, 0))

    if start_dt > datetime.now() + timedelta(days=16):
        return jsonify({'available': False, 'reason': 'forecast_horizon',
                        'message': 'Weather forecast opens within 16 days of the ride.',
                        'ride_date': str(ride_date)})

    # Resolve the plan the SAME way as the plan screen (FK → name match) so the
    # weather timing follows it — and the rider's custom plan when present (the
    # rider_id makes build_weather_payload prefer the custom plan's stop timing).
    plan = _resolve_base_plan(ride)
    payload, err = build_weather_payload(
        route_id, start_dt, plan_slug=(plan['slug'] if plan else None), rider_id=g.rider_id)
    if err:
        body, status = err
        return jsonify(body), status
    payload['available'] = True
    return jsonify(payload)


@live_bp.route('/api/ride/<int:ride_id>/plan')
@token_or_session_required
def api_ride_plan(ride_id):
    """JSON: the ride plan (stops + timing) for a ride — mirrors the web plan page.

    Auth: web session OR mobile Bearer token. Resolves the ride to its ride_plan,
    computes per-stop cumulative time / arrival ETA / time bank with the same
    formulas as the web ride_plan_detail, and best-effort attaches per-stop wind +
    temperature (the existing fetch_stop_wind, when the route + forecast allow).
    Returns {available: false, reason} when the ride has no plan/stops.
    """
    if not g.rider_id:
        return jsonify({'error': 'Complete your profile to view the plan'}), 403

    from datetime import date as _date, time as _time
    from models import get_ride_plan_stops, get_custom_plan
    from services.weather import fetch_stop_wind

    ride = get_ride_by_id(ride_id)
    if not ride:
        return jsonify({'error': 'Ride not found'}), 404

    # Resolve the base plan the same way the web does (FK → route-name match).
    plan = _resolve_base_plan(ride)
    if not plan:
        return jsonify({'available': False, 'reason': 'no_plan',
                        'message': 'No ride plan is published for this ride yet.'})
    plan_slug = plan['slug']

    # Prefer the rider's own custom plan (default), like the web; ?view=base forces base.
    custom = get_custom_plan(g.rider_id, plan['id'])
    use_custom = bool(custom) and (request.args.get('view') or '').lower() != 'base'

    # Canonical event-level fields for cutoff / start (migration 018 deprecated the
    # ride_plan.cutoff_hours / start_time columns); fall back to the plan.
    cutoff_raw = ride.get('time_limit_hours') or plan.get('cutoff_hours')
    cutoff_hours = float(cutoff_raw) if cutoff_raw else None
    total_mi = float(plan.get('total_distance_miles') or 0)
    start_str = (ride.get('start_time') or plan.get('start_time')
                 or ride.get('plan_start_time') or '07:00')
    try:
        hh, mm = (int(x) for x in str(start_str).split(':')[:2])
        start_clock = _time(hh, mm)
    except (ValueError, TypeError):
        start_clock = _time(7, 0)
    base_dt = datetime.combine(ride.get('date') or _date.today(), start_clock)

    if use_custom:
        # Merge the rider's overrides onto the base stops and recompute timing the
        # SAME way the web custom plan view does (services/custom_plan_service).
        from services.custom_plan_service import (get_merged_plan_stops,
                                                  recalculate_cumulative_values)
        merged, meta = get_merged_plan_stops(custom['id'])
        # Pass the canonical cutoff (ride.time_limit_hours) + plan total so the time bank
        # is computed even when the custom plan name carries no distance class. (The web
        # custom views recompute time_bank inline from the base-plan name; keep that path
        # in sync via tests — TODO: consolidate onto this service in a follow-up.)
        raw = recalculate_cumulative_values(merged or [], meta or custom,
                                            cutoff_hours=cutoff_hours, total_mi=total_mi)
    else:
        raw = _compute_base_timing(get_ride_plan_stops(plan['id']), cutoff_hours, total_mi)

    if not raw:
        return jsonify({'available': False, 'reason': 'no_stops',
                        'message': 'This ride plan has no stops yet.'})

    stops = [_emit_plan_stop(d, base_dt) for d in raw]

    # Best-effort per-stop wind + temperature (same service as the plan web page).
    try:
        route_id = extract_rwgps_route_id(
            plan.get('rwgps_url_team') or plan.get('rwgps_url')
            or ride.get('rwgps_url_team') or ride.get('rwgps_url'))
        track = (fetch_route(route_id) or {}).get('track_points') if route_id else None
        if track:
            wind_stops = [{'distance_miles': st['distance_mi'],
                           'arrival_time_min': st['arrival_time_min']} for st in stops]
            winds = fetch_stop_wind(wind_stops, track, plan_slug, start_str, cache=cache)
            for st, w in zip(stops, winds or []):
                if w:
                    st['wind_speed_mph'] = w.get('wind_speed_mph')
                    st['wind_label'] = w.get('label') or w.get('wind_type')
                    st['wind_direction_deg'] = w.get('wind_direction_deg')
                    st['temperature_f'] = w.get('temperature_f')
    except Exception:
        current_app.logger.warning('ride plan %s: stop wind unavailable', plan_slug)

    return jsonify({
        'available': True,
        'plan': {
            'name': plan.get('name'),
            'slug': plan.get('slug'),
            'total_distance_mi': round(total_mi, 1) if total_mi else None,
            'total_elevation_ft': plan.get('total_elevation_ft'),
            'distance_km': ride.get('distance_km') or plan.get('distance_km'),
            'cutoff_hours': cutoff_hours,
            'start_time': start_str,
            'overall_ft_per_mile': (round(float(plan['overall_ft_per_mile']))
                                    if plan.get('overall_ft_per_mile') else None),
        },
        'has_custom': bool(custom),
        'using_custom': use_custom,
        'custom_name': custom.get('name') if custom else None,
        'ride_date': str(ride['date']) if ride.get('date') else None,
        'stops': stops,
    })


@live_bp.route('/api/me/season')
@token_or_session_required
def api_my_season():
    """JSON: the signed-in rider's current-season progress — the app's "My Season" tab.

    Auth: web session OR mobile Bearer token. Read-only; assembles the existing
    season / SR / R-12 / Eddington helpers for g.rider_id + the current season.
    No new award computation, no migration.
    """
    if not g.rider_id:
        return jsonify({'error': 'Complete your profile to view your season'}), 403

    from models import (get_current_season, get_rider_season_stats,
                        get_rider_season_elevation_ft, get_rider_career_stats,
                        detect_sr_for_rider_season, get_sr_distances_done,
                        get_sr_counts_by_tier, get_rider_finished_rides_for_season,
                        get_r12_current_streak, get_strava_connection)
    import html as _html

    rider_id = g.rider_id
    season = get_current_season()
    if not season:
        return jsonify({'error': 'No current season set'}), 404

    season_id = season['id']

    # Season totals (current season uses date-filtered SR, mirroring the web profile).
    stats = get_rider_season_stats(rider_id, season_id)
    elevation_ft = get_rider_season_elevation_ft(rider_id, season_id)
    sr_count = detect_sr_for_rider_season(rider_id, season_id, date_filter=True)
    distances_done = get_sr_distances_done(rider_id, season_id, date_filter=True)
    sr_counts = get_sr_counts_by_tier(rider_id, season_id, date_filter=True)

    # Which rides the rider finished this season (newest first). Names come from
    # web scraping, so unescape HTML entities (mirrors the clean_name filter).
    rides_done = [{
        'id': r['id'],
        'name': _html.unescape(str(r['name'] or '')).replace('\xa0', ' ').strip(),
        'date': str(r['date']) if r.get('date') else None,
        'distance_km': r.get('distance_km'),
    } for r in get_rider_finished_rides_for_season(rider_id, season_id)]

    # R-12: current consecutive-month streak + whether it's still alive.
    r12 = get_r12_current_streak(rider_id)

    # Career totals (KMs, all seasons).
    career = get_rider_career_stats(rider_id)

    # Eddington: stored value (miles) + badge, mirroring the web profile. Skip the
    # optional live-recalc here to keep this a fast per-rider call.
    eddington = None
    conn = get_strava_connection(rider_id)
    if conn and conn.get('eddington_number_miles'):
        from services.eddington import get_eddington_badge_level
        value = conn['eddington_number_miles']
        eddington = {'value': value, 'badge': get_eddington_badge_level(value)}

    return jsonify({
        'season': {'name': season.get('name')},
        'stats': {
            'distance_km': round(stats['kms'] or 0),
            'rides': stats['rides'] or 0,
            'elevation_ft': elevation_ft,
        },
        'sr': {
            'has_sr': sr_count >= 1,
            'distances_done': distances_done,
            'counts': {str(k): v for k, v in sr_counts.items()},
        },
        'rides_done': rides_done,
        'r12': {
            'months': r12['months'],
            'active': r12['active'],
        },
        'career': {'distance_km': round(career['total_kms'] or 0)},
        'eddington': eddington,
    })


@live_bp.route('/live/share')
@profile_required
def live_share():
    """Mobile page: stream this device's location to the club (browser beacon)."""
    rider_id = session['rider_id']
    tracking = get_live_tracking(rider_id)
    opted_in = bool(tracking and tracking.get('enabled'))
    return render_template('live_share.html', opted_in=opted_in)


@live_bp.route('/api/live/sharing', methods=['GET'])
@token_or_session_required
def live_sharing_status():
    """Read the current rider's live-tracking opt-in flag.

    Lets the mobile Settings toggle reflect the real server-side state on open
    (it's the account-level consent gate the per-ride beacon depends on).
    Auth: web session OR mobile Bearer token.
    """
    rider_id = g.rider_id
    if not rider_id:
        return jsonify({'error': 'Complete your profile to share your location'}), 403
    tracking = get_live_tracking(rider_id)
    return jsonify({'enabled': bool(tracking and tracking.get('enabled'))})


@live_bp.route('/api/live/sharing', methods=['POST'])
@token_or_session_required
def live_sharing_toggle():
    """Turn the current rider's live tracking on/off (the opt-in flag).

    Lets the rider start sharing from the beacon UI in one tap — no detour to the
    Garmin settings page. Preserves any registered Garmin session. The act of
    tapping "Start sharing" (with the on-page privacy note) is the consent.
    Auth: web session OR mobile Bearer token.
    """
    rider_id = g.rider_id
    if not rider_id:
        return jsonify({'error': 'Complete your profile to share your location'}), 403
    enabled = bool((request.get_json(silent=True) or {}).get('enabled'))
    ok = set_live_tracking_enabled(rider_id, enabled)
    return jsonify({'ok': ok, 'enabled': enabled})


@live_bp.route('/api/live/beacon', methods=['POST'])
@token_or_session_required
def live_beacon():
    """Accept a geolocation position for the CURRENT rider only.

    Club-only (completed profile) and opt-in (rider must have enabled tracking).
    Auth is a web session OR a mobile Bearer token; the rider is always taken
    from that trusted identity (g.rider_id) — any client-supplied rider id is
    ignored — and coordinates are validated/clamped before insert.
    """
    rider_id = g.rider_id
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

    # Beacon points are per-ride too: take the ride from the page that's sharing
    # (the ride map sends it), falling back to the rider's active Garmin ride.
    # Without a ride the point can't be shown on any map, so require one.
    try:
        ride_id = int(data.get('ride_id'))
    except (TypeError, ValueError):
        ride_id = tracking.get('active_ride_id')
    if not ride_id:
        return jsonify({'error': 'Open a ride\'s live map to share for that ride'}), 400

    now = datetime.now(timezone.utc)
    ok = insert_live_position(
        rider_id=rider_id,          # session only — client value never trusted
        lat=lat, lng=lng, accuracy=accuracy, speed=speed,
        recorded_at=now, source='beacon', ride_id=ride_id,
    )
    if not ok:
        return jsonify({'error': 'Invalid coordinates'}), 400

    return jsonify({'ok': True, 'recorded_at': now.isoformat()})
