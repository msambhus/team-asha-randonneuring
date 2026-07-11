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

from auth import profile_required, token_or_session_required, resolve_identity
from cache import cache, CACHE_TIMEOUT
from models import (get_ride_by_id, get_live_tracking, set_live_tracking_enabled,
                    set_ride_garmin, clear_ride_garmin,
                    get_latest_positions_for_ride, insert_live_position,
                    get_rider_upcoming_signups, get_ride_plan_stops,
                    get_positions_for_rider_since, get_default_time_limit,
                    get_or_create_ride_invite, get_valid_ride_invite, RideStatus)
from services.garmin_livetrack import parse_session
from services.rwgps import extract_rwgps_route_id, fetch_route
from services import live_telemetry as tlm
from services.weather import (sample_track_points, fetch_route_weather,
                              calculate_bearing, headwind_component,
                              crosswind_component, classify_wind,
                              wind_arrow_rotation, wind_arrow_glyph,
                              build_arrival_interpolator, build_weather_segments,
                              build_chart_data)

live_bp = Blueprint('live', __name__)

M_TO_MI = 1 / 1609.344
KMH_TO_MPH = 0.621371
MS_TO_MPH = 2.236936
_MAX_CONTEXT_TRACK_POINTS = 2000

# Club-local timezone. Ride start_time values (e.g. "06:00") are wall-clock
# times in the Bay Area, so elapsed-time math must interpret them in Pacific
# time and convert to UTC — not treat "06:00" as 06:00 UTC.
CLUB_TZ = ZoneInfo('America/Los_Angeles')

# A live-map invite code stays usable until this long AFTER the ride's own time
# limit (when the ride "is supposed to be over"), so it covers the whole event
# (a 600k runs ~40h) plus time to review — not a fixed UTC-midnight cutoff.
INVITE_BUFFER_HOURS = 48


def _ride_start_utc(ride):
    """The ride's start as a tz-aware UTC datetime, or None.

    start_time is Bay-Area wall-clock ("06:00" = 6 AM Pacific), so it is built in
    CLUB_TZ then converted to UTC — treating "06:00" as UTC would be ~7-8h off."""
    try:
        start_t = ride.get('plan_start_time') or ride.get('start_time') or '06:00'
        hh, mm = (int(x) for x in str(start_t).split(':')[:2])
        d = ride['date']
        if isinstance(d, str):
            d = date.fromisoformat(d)
        return datetime(d.year, d.month, d.day, hh, mm,
                        tzinfo=CLUB_TZ).astimezone(timezone.utc)
    except Exception:
        return None

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

# Map-dot colors by PLAN TIMING (ahead/behind), which override the signup-status
# color on the live map so you can see at a glance who's on pace. Green = ahead or
# on plan, red = behind, grey = we can't grade pace (off-route / finished / no plan
# matched). The detail-card badge is computed from the SAME telemetry, so the dot
# and the badge agree on ahead/behind.
PLAN_AHEAD_COLOR = '#16a34a'    # green — ahead of or on plan
PLAN_BEHIND_COLOR = '#dc2626'   # red — behind plan
PLAN_UNKNOWN_COLOR = '#6b7280'  # grey — pace can't be graded


def _plan_dot_color(status, telemetry):
    """Dot color from plan timing. Precedence: finished/off-route → grey;
    behind → red; ahead/on → green; and when no plan is resolved → fall back to
    the signup-status color (so rides without a plan don't regress).

    Staleness is deliberately NOT greyed here: the map already dims a stale rider
    (marker opacity / .stale class) and the detail card keeps showing their
    last-known ahead/behind badge — so the dot keeps that color too and the two
    stay in agreement. Off-route is grey because pace can't be graded; the card
    labels that case explicitly as "Off route"."""
    if status == RideStatus.FINISHED.value:
        return PLAN_UNKNOWN_COLOR
    if telemetry is not None and telemetry.get('on_route') is False:
        return PLAN_UNKNOWN_COLOR
    plan = (telemetry or {}).get('plan')
    if plan and plan.get('status'):
        return PLAN_BEHIND_COLOR if plan['status'] == 'behind' else PLAN_AHEAD_COLOR
    return STATUS_COLORS.get(status, DEFAULT_COLOR)


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
def ride_live_map(ride_id):
    """Per-ride live map: RWGPS route line + live dots for opted-in GOING riders.

    Open to logged-in club members, OR to an unauthenticated guest who entered a
    valid invite code for THIS ride at /live/join (read-only — member controls
    are hidden)."""
    is_member = bool(session.get('rider_id'))
    is_guest = (not is_member) and (_guest_ride_id() == ride_id)
    if not is_member and not is_guest:
        # A half-logged-in member finishes profile setup; everyone else is sent
        # to the guest join page to enter an invite code.
        if session.get('user_id'):
            return redirect(url_for('auth.setup_profile'))
        return redirect(url_for('live.live_join'))

    ride = get_ride_by_id(ride_id)
    if not ride:
        abort(404)

    mapbox_token = current_app.config.get('MAPBOX_ACCESS_TOKEN', '')
    route_polyline = _build_route_polyline(ride)
    opted_in = garmin_here = False
    garmin_url = ''
    if is_member:
        tracking = get_live_tracking(session['rider_id'])
        opted_in = bool(tracking and tracking.get('enabled'))
        # The Garmin link is per-ride: only show it as linked here if it's
        # pointed at THIS ride (active_ride_id), so a link saved for another
        # ride doesn't look active on this one.
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
        is_guest=is_guest,
    )


@live_bp.route('/ride/<int:ride_id>/live/invite', methods=['POST'])
@profile_required
def ride_live_invite(ride_id):
    """Mint (or return) a shareable invite code for this ride's live map so a
    member can let non-members follow along without logging in."""
    ride = get_ride_by_id(ride_id)
    if not ride:
        return jsonify({'error': 'Ride not found'}), 404
    # Expire the code a buffer after the ride's OWN time limit, so it stays valid
    # for the whole event (a 600k runs ~40h — a ride-day-only window died before
    # the cutoff) plus time to review afterward. Falls back to ride-day + 2 days
    # only if the start can't be resolved.
    start_utc = _ride_start_utc(ride)
    limit_h = (ride.get('time_limit_hours')
               or get_default_time_limit(ride.get('distance_km') or 0) or 24)
    if start_utc is not None:
        expires_at = start_utc + timedelta(hours=float(limit_h) + INVITE_BUFFER_HOURS)
    else:
        d = ride['date']
        if isinstance(d, str):
            d = date.fromisoformat(d)
        expires_at = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=2)
    code = get_or_create_ride_invite(ride_id, session['rider_id'], expires_at)
    if not code:
        return jsonify({'error': 'Could not create an invite code'}), 500
    return jsonify({
        'code': code,
        # Code embedded in the link so sharing is one click — no typing.
        'join_url': url_for('live.live_join', code=code, _external=True),
        'expires_at': expires_at.isoformat(),
    })


@live_bp.route('/live/join', methods=['GET', 'POST'])
def live_join():
    """Public page: a guest joins a ride's live map with an invite code.

    No authentication. The code can arrive in the shared link (?code=...) for a
    one-click join, or be typed into the form. On a valid code the ride grant is
    stored in the guest's session and they're sent to that ride's read-only map.
    The session is made permanent (30-day cookie) so mobile browsers/PWAs don't
    drop it on backgrounding and force a re-entry — actual access is still
    bounded by the code's own expiry, which _guest_ride_id() re-checks each
    request."""
    submitted = (request.form.get('code') if request.method == 'POST'
                 else request.args.get('code'))
    is_member = bool(session.get('rider_id'))
    if submitted:
        inv = get_valid_ride_invite(submitted)
        if inv:
            # A valid share link should ALWAYS open the ride it points at —
            # including for logged-in members. (Previously members were bounced
            # to the hub before the code was even read, so a shared link never
            # opened the ride — it looked like a "share your location" prompt.)
            # Guests also get a read-only session grant for that ride.
            if not is_member:
                session.permanent = True
                session['live_guest'] = {'code': inv['code'], 'ride_id': inv['ride_id']}
            return redirect(url_for('live.ride_live_map', ride_id=inv['ride_id']))
        flash('That code is invalid or has expired.', 'warning')
    # No / invalid code: members go to their live hub; guests get the join form.
    if is_member:
        return redirect(url_for('live.live_hub'))
    return render_template('live_join.html', code=(submitted or ''))


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
            temps = hourly.get('temperature_2m') or []
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
            # Temperature (°F) sampled at the same hour — for the live temperature
            # chart. Best-effort: absent when the hourly payload omits it.
            temp_f = None
            if idx < len(temps) and temps[idx] is not None:
                temp_f = round(float(temps[idx]) * 9 / 5 + 32, 1)
            out.append({'dist_m': s['distance_m'], 'headwind_kmh': hw,
                        'crosswind_kmh': cw, 'temperature_f': temp_f})
        return out or None
    except Exception:
        return None


# Sample interval (m) for the live route-ahead charts — matches the weather page's
# map interval so the same route yields the same forecast points.
_LIVE_CHART_INTERVAL_M = 15000


def _ride_start_local(ride):
    """Ride start as a NAIVE local datetime (ride-day + start clock), the way the
    weather page times its forecast points. Open-Meteo returns local-time hourly
    arrays, so the live charts must be timed from a naive local start (NOT the UTC
    start used for elapsed math) or the arrival-hour selection would be offset by
    the UTC-offset. Returns None when the ride has no resolvable date."""
    if not ride:
        return None
    try:
        d = ride['date']
        if isinstance(d, str):
            d = date.fromisoformat(d)
        if d is None:
            return None
        start_t = ride.get('plan_start_time') or ride.get('start_time') or '06:00'
        hh, mm = (int(x) for x in str(start_t).split(':')[:2])
        return datetime(d.year, d.month, d.day, hh, mm)
    except Exception:
        return None


def _build_live_chart_data(track_points, plan_stops, start_dt):
    """Route-ahead chart series for the live page, sourced from the SAME time-aware
    weather pipeline the weather page uses — sample_track_points → fetch_route_weather
    → arrival-hour selection (build_weather_segments) → build_chart_data — so the live
    charts and the weather page can never diverge (item 5). Each point is timed against
    the ride's BASE plan (plan_stops) when available, else a flat speed, exactly like
    routes/weather.py's build_weather_payload.

    Returns {labels, elevation_ft, headwind_mph, temperature_f} (aligned arrays,
    distance in mi) or None when the route is too short / the forecast is unavailable,
    so the caller hides the charts (today's graceful-degradation behavior)."""
    if not track_points:
        return None
    samples = sample_track_points(track_points, interval_m=_LIVE_CHART_INTERVAL_M)
    if len(samples) < 2:
        return None
    forecasts = fetch_route_weather(samples)
    if not forecasts or len(forecasts) < 2:
        return None
    bearings = [calculate_bearing(samples[i]['lat'], samples[i]['lng'],
                                  samples[i + 1]['lat'], samples[i + 1]['lng'])
                for i in range(len(samples) - 1)]
    arrival_fn = (build_arrival_interpolator(plan_stops, start_dt)
                  if plan_stops and start_dt else None)
    segments = build_weather_segments(
        samples, forecasts, bearings, start_dt or datetime.now(),
        track_points=track_points, arrival_fn=arrival_fn)
    if len(segments) < 2:
        return None
    cd = build_chart_data(segments)
    return {
        'labels': cd['labels'],
        'elevation_ft': cd['elevation_ft'],
        'headwind_mph': cd['headwind_mph'],
        'temperature_f': cd['temperature_f'],
    }


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
           'has_route': False, 'has_plan': False, 'chart_data': None,
           # Base plan id + timing inputs so the per-rider custom plan (if any) can
           # be merged + retimed the SAME way the web plan page does (_rider_plan_stops).
           'base_plan_id': None, 'plan_cutoff_hours': None, 'plan_total_mi': 0.0}
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

    # Ride start (Bay-Area wall-clock → UTC) for elapsed/plan comparison.
    start_utc = _ride_start_utc(ride)
    ctx['ride_start_iso'] = start_utc.isoformat() if start_utc else None

    # Base plan for on/behind-plan comparison. Resolve it the SAME way the web plan
    # page does — FK (ride_plan_id) THEN route-name match (services.plan_match) — so
    # a ride with no FK (e.g. the SCR 600k) still gets a plan, and time it with the
    # web formulas so the live delta matches the plan page. Per-rider custom plans
    # are layered on later in _rider_plan_stops (they're per-rider, not per-ride).
    try:
        plan = _resolve_base_plan(ride)
        if plan:
            cutoff_raw = ride.get('time_limit_hours') or plan.get('cutoff_hours')
            ctx['plan_cutoff_hours'] = float(cutoff_raw) if cutoff_raw else None
            ctx['plan_total_mi'] = float(plan.get('total_distance_miles') or 0)
            ctx['base_plan_id'] = plan['id']
            base_raw = _compute_base_timing(
                get_ride_plan_stops(plan['id']), ctx['plan_cutoff_hours'], ctx['plan_total_mi'])
            ctx['plan_stops'] = [
                {'distance_miles': float(s['distance_miles']),
                 'cum_time_min': float(s['cum_time_min']),
                 # arrival_time_min (= cum − stop_duration) is the REACHING time at
                 # the control, carried through so next_control's ETA is arrival,
                 # not departure. _compute_base_timing always sets it.
                 'arrival_time_min': (float(s['arrival_time_min'])
                                      if s.get('arrival_time_min') is not None else None),
                 'location': s.get('location'),
                 'stop_type': s.get('stop_type')}
                for s in base_raw
                if s.get('distance_miles') is not None and s.get('cum_time_min') is not None
            ]
            ctx['has_plan'] = len(ctx['plan_stops']) >= 2
    except Exception:
        current_app.logger.warning('live ctx: plan resolution failed for ride %s',
                                   ride_id, exc_info=True)
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
                                  'dist_m': float(tp.get('d') or 0),
                                  # Elevation (m) for live grade; None when the
                                  # route has no profile so grade reads "—".
                                  'e_m': float(tp['e']) if tp.get('e') is not None else None})
                    cum_ascent.append(round(cum))
                ctx['track'] = track
                ctx['cum_ascent_ft'] = cum_ascent
                ctx['total_dist_m'] = track[-1]['dist_m'] if track else None
                ctx['total_ascent_ft'] = cum_ascent[-1] if cum_ascent else None
                ctx['has_route'] = True
                # Per-rider "wind done / ahead" labels keep sampling CURRENT
                # conditions along the route (unchanged — a separate concern from
                # the route-ahead charts).
                ctx['wind_by_dist'] = _build_wind_by_dist(tps)
                # Route-ahead charts (elevation / headwind / temperature) now come
                # from the SAME time-aware weather pipeline as the weather page,
                # timed against the BASE plan's arrival schedule (item 5). Static
                # per ride, so it rides the cached context; the per-poll path only
                # adds each rider's current-position marker on top.
                try:
                    ctx['chart_data'] = _build_live_chart_data(
                        tps, ctx['plan_stops'], _ride_start_local(ride))
                except Exception as cexc:  # noqa: BLE001 — charts are best-effort
                    current_app.logger.warning(
                        'live ctx: chart_data failed for ride %s: %s', ride_id, cexc)
                    ctx['chart_data'] = None
        except Exception as exc:  # noqa: BLE001 — telemetry is best-effort
            current_app.logger.warning('live ctx: route %s failed: %s', route_id, exc)
    return ctx


def _as_utc(dt):
    """Treat a naive datetime as UTC so it can be compared with tz-aware times
    (DB timestamptz values are already aware; this just guards naive ones)."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _guest_ride_id():
    """ride_id an unauthenticated guest may view via a live invite code, else None.

    The grant is stashed in the session at /live/join, but the code is
    re-validated here on every request so an expired/removed code stops working
    immediately (the session flag alone is never trusted)."""
    grant = session.get('live_guest')
    if not grant or not grant.get('code'):
        return None
    inv = get_valid_ride_invite(grant['code'])
    return inv['ride_id'] if inv else None


def _merge_custom_stops(custom_plan_id, ctx, meta=None):
    """Merge + retime a custom plan (by id) into the ctx['plan_stops'] shape, the
    SAME way the web plan page does. Returns a list of
    {distance_miles, cum_time_min, arrival_time_min, location, stop_type} or None
    when the plan yields fewer than 2 usable stops (can't grade pace)."""
    from services.custom_plan_service import (get_merged_plan_stops,
                                              recalculate_cumulative_values)
    merged, merged_meta = get_merged_plan_stops(custom_plan_id)
    raw = recalculate_cumulative_values(
        merged or [], merged_meta or meta or {},
        cutoff_hours=ctx.get('plan_cutoff_hours'), total_mi=ctx.get('plan_total_mi') or 0)
    stops = [
        {'distance_miles': float(s['distance_miles']), 'cum_time_min': float(s['cum_time_min']),
         # arrival_time_min (= cum − stop_duration) for the arrival-based ETA;
         # recalculate_cumulative_values sets it the same way the base path does.
         'arrival_time_min': (float(s['arrival_time_min'])
                              if s.get('arrival_time_min') is not None else None),
         'location': s.get('location'), 'stop_type': s.get('stop_type')}
        for s in (raw or [])
        if s.get('distance_miles') is not None and s.get('cum_time_min') is not None
    ]
    return stops if len(stops) >= 2 else None


def _rider_plan_stops(ctx, rider_id):
    """Plan stops to grade THIS rider against: their own custom plan if they have
    one (merged + retimed the SAME way the web plan page does), else the ride's
    base plan (ctx['plan_stops']). Returns a list of {distance_miles, cum_time_min}
    for tlm.plan_delta. Best-effort: any failure falls back to the base plan."""
    base = ctx.get('plan_stops') or []
    base_plan_id = ctx.get('base_plan_id')
    if not base_plan_id or not rider_id:
        return base
    try:
        from models import get_custom_plan
        custom = get_custom_plan(rider_id, base_plan_id)
        if not custom:
            return base
        stops = _merge_custom_stops(custom['id'], ctx, meta=custom)
        return stops if stops else base
    except Exception:
        current_app.logger.warning('live: custom plan stops failed for rider %s', rider_id)
        return base


# ── Plan selector: authorization allow-set + selected-plan resolution ───────
# The live positions endpoint is reachable by any logged-in rider AND by
# unauthenticated guests holding an invite code, so a viewer must never be able to
# resolve a private plan they aren't allowed to see. Every selectable plan is
# assembled here into an allow-set; the resolver refuses any id outside it.

# Sentinel selector value: "grade each rider against their OWN custom plan" (the
# behavior that used to be the default). Distinct from a numeric plan id.
PLAN_OWN = 'own'
PLAN_BASE = 'base'


def _own_lens_available(allowed_custom_ids, viewer_rider_id):
    """Whether the 'own' (each-rider's-own) lens may be OFFERED and RESOLVED.

    'own' grades every rider against THEIR OWN (possibly private) custom plan, so it
    must be available only to a logged-in member who already has at least one VISIBLE
    custom plan (public or their own). Gating both the selector offer AND the resolver
    on this single predicate is what stops a crafted ?plan_id=own from bypassing the
    allow-set into per-rider private-plan grading when 'own' was deliberately withheld."""
    return bool(allowed_custom_ids and viewer_rider_id)


def _available_plans(base_plan_id, viewer_rider_id):
    """Assemble the AUTHORIZATION allow-set AND the selector's option list in one.

    Returns (options, allowed_custom_ids):
      options: [{'id': 'base'|'own'|<int>, 'name', 'owner', 'is_custom'}] for the
               dropdown — base first, then each allowed named custom plan, then the
               'own' (each-rider's-own) sentinel. Only base when the ride has no
               custom plans (single-plan ride → no selector).
      allowed_custom_ids: the set of int custom-plan ids the viewer may resolve.

    Membership (never leaks a private plan):
      - base            — always
      - public custom   — every public custom plan for this base plan
      - own custom      — the viewer's OWN custom plan, only for a logged-in rider
      - 'own' sentinel  — offered whenever any custom plan is visible
    A guest (viewer_rider_id is None) gets base + public plans only.
    """
    options = [{'id': PLAN_BASE, 'name': 'Base plan', 'owner': None, 'is_custom': False}]
    allowed_custom_ids = set()
    if not base_plan_id:
        return options, allowed_custom_ids

    seen = set()
    try:
        from models import get_public_custom_plans
        for cp in (get_public_custom_plans(base_plan_id) or []):
            cid = cp.get('id')
            if cid is None or cid in seen:
                continue
            seen.add(cid)
            allowed_custom_ids.add(cid)
            owner = (cp.get('first_name') or '').strip() or None
            options.append({'id': cid, 'name': (cp.get('name') or 'Custom plan'),
                            'owner': owner, 'is_custom': True})
    except Exception:
        current_app.logger.warning('live: public custom plans lookup failed for base %s',
                                   base_plan_id, exc_info=True)

    # The viewer's OWN custom plan (members only) — added when not already public.
    if viewer_rider_id:
        try:
            from models import get_custom_plan
            own = get_custom_plan(viewer_rider_id, base_plan_id)
            if own and own.get('id') is not None and own['id'] not in seen:
                seen.add(own['id'])
                allowed_custom_ids.add(own['id'])
                options.append({'id': own['id'], 'name': (own.get('name') or 'My plan'),
                                'owner': None, 'is_custom': True})
        except Exception:
            current_app.logger.warning('live: own custom plan lookup failed for rider %s',
                                       viewer_rider_id, exc_info=True)

    # Offer the "each rider's own plan" lens only when it is actually available (a
    # member with at least one visible custom plan). The resolver gates on the SAME
    # predicate, so a lens that isn't offered here can never be resolved. Guests still
    # see base + public named plans; a ride with no visible custom plan is effectively
    # single-plan (no selector shown).
    if _own_lens_available(allowed_custom_ids, viewer_rider_id):
        options.append({'id': PLAN_OWN, 'name': "Each rider's own plan",
                        'owner': None, 'is_custom': False})
    return options, allowed_custom_ids


def _selected_plan_stops(requested_plan_id, ctx, allowed_custom_ids, is_member=False):
    """Resolve the requested plan_id STRICTLY from the allow-set. Returns
    (applied_id, override_stops):

      - applied_id: the value actually applied — 'base', 'own', or an int id. A
        rejected/unknown id falls back to 'base' (surfaced, never silent misgrading).
      - override_stops: the plan stops every rider is graded against (base or the
        selected custom plan), or None for 'own' (each rider keeps their own plan).

    Any value the viewer isn't allowed to resolve — a numeric id NOT in
    allowed_custom_ids (a private plan owned by someone else), an unknown/malformed
    id, or the 'own' lens requested by a GUEST (is_member False) — is refused and
    logged, so no private plan (named or per-rider) can leak through the query string.
    'own' grades every rider against their OWN (possibly private) custom plan, so it
    is members-only; a guest gets the base plan instead."""
    base_stops = ctx.get('plan_stops') if ctx else None

    if requested_plan_id is None or requested_plan_id == '' or requested_plan_id == PLAN_BASE:
        return PLAN_BASE, base_stops
    if requested_plan_id == PLAN_OWN:
        # 'own' is resolvable ONLY when it was actually offered — a member with at
        # least one visible custom plan (the same predicate _available_plans offers it
        # on). A guest, or a member for whom 'own' was withheld (no visible custom
        # plan), gets the base plan — so a crafted ?plan_id=own can never fall into
        # per-rider grading that reads other riders' private custom plans.
        if not _own_lens_available(allowed_custom_ids, is_member):
            current_app.logger.warning("live: rejected 'own' lens not in allow-set → base fallback")
            return PLAN_BASE, base_stops
        return PLAN_OWN, None

    try:
        pid = int(requested_plan_id)
    except (TypeError, ValueError):
        current_app.logger.warning('live: rejected malformed plan_id %r → base fallback',
                                   requested_plan_id)
        return PLAN_BASE, base_stops

    if pid not in allowed_custom_ids:
        # IDOR guard: an id the viewer isn't allowed to see never resolves.
        current_app.logger.warning('live: rejected out-of-allowset plan_id %s → base fallback', pid)
        return PLAN_BASE, base_stops

    try:
        stops = _merge_custom_stops(pid, ctx)
        if stops:
            return pid, stops
    except Exception:
        current_app.logger.warning('live: selected plan %s merge failed → base fallback', pid,
                                   exc_info=True)
    return PLAN_BASE, base_stops


def _upcoming_controls(plan_stops, leader_dist_mi, start_utc):
    """One shared, ride-level list of the applied plan's future controls (item 2).

    Future = ahead of the furthest-along on-route rider (leader_dist_mi); when no
    rider is on route yet, every control (bar the start) is upcoming. Each entry
    carries the plan's ARRIVAL ETA in club-local time. Ride-level, so it is computed
    once — never per rider."""
    if not plan_stops:
        return []
    out = []
    for s in plan_stops:
        dm, ct = s.get('distance_miles'), s.get('cum_time_min')
        if dm is None or ct is None:
            continue
        if (s.get('stop_type') or '').lower() == 'start':
            continue
        dm = float(dm)
        if leader_dist_mi is not None and dm <= leader_dist_mi + tlm.NEXT_CONTROL_EPS_MI:
            continue
        arrival = s.get('arrival_time_min')
        arrival = round(float(arrival)) if arrival is not None else round(float(ct))
        eta_iso = eta_label = None
        if start_utc is not None:
            eta_dt = start_utc + timedelta(minutes=arrival)
            eta_iso = eta_dt.isoformat()
            eta_label = eta_dt.astimezone(CLUB_TZ).strftime('%I:%M %p').lstrip('0')
        out.append({
            'name': s.get('location') or None,
            'type': s.get('stop_type') or None,
            'distance_mi': round(dm, 1),
            'arrival_time_min': arrival,
            'eta_iso': eta_iso,
            'eta_label': eta_label,
        })
    out.sort(key=lambda c: c['distance_mi'])
    return out


def _rider_telemetry(row, ctx, now, history, plan_stops=None):
    """Assemble the telemetry block for one rider.

    Source-agnostic fields (speed, activity, moving/stopped, elapsed, HR/power)
    are always included. Route-relative fields (distance done/left, ascent,
    headwinds, toughness, plan delta) are only included when the rider is
    actually ON the route — otherwise `on_route` is False and they are omitted
    so we never report a bogus mileage from snapping to the nearest line.
    """
    lat, lng = float(row['lat']), float(row['lng'])

    # Ride start (Pacific→UTC) gates BOTH elapsed and moving/stopped: a Garmin
    # session that began before the official start (warm-up / early recording)
    # must not count, otherwise moving time can exceed elapsed time.
    elapsed_min = None
    start = None
    if ctx.get('ride_start_iso'):
        try:
            start = datetime.fromisoformat(ctx['ride_start_iso'])
        except ValueError:
            start = None
    if start is not None and start <= now:
        elapsed_min = round((now - start).total_seconds() / 60)

    # Moving/stopped only over history at/after the ride start (≤ elapsed).
    ride_history = history
    if start is not None:
        ride_history = [h for h in history if _as_utc(h['recorded_at']) >= start]
    moving_min, stopped_min = tlm.moving_stopped(ride_history)
    # Make moving + stopped reconcile to elapsed: anything since the start that
    # isn't moving (true stops, data gaps, time before the first fix) is stopped.
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
        'heart_rate': row.get('heart_rate'),
        'power': row.get('power'),
        'cadence': row.get('cadence'),
    }
    base = {'on_route': None, 'now': now_block, 'remaining': None,
            'plan': None, 'detailed_after_ride': True}

    if not ctx.get('has_route'):
        return base

    # Project the rider's whole trajectory (since the ride start) onto the route
    # in time order, so an out-and-back / looped route that passes the same place
    # more than once resolves to the leg they're actually on and the distance is
    # monotonic (never jumps backward). Falls back to a stateless match only when
    # there's no in-ride trajectory yet (ride_history empty).
    dist_m, idx, off_by_m = tlm.project_history_to_route(ride_history, ctx['track'])
    if dist_m is None:
        dist_m, idx, off_by_m = tlm.project_to_route(lat, lng, ctx['track'])
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

    # Grade against this rider's plan (custom if they have one, else base).
    delta = tlm.plan_delta(dist_mi, elapsed_min,
                           plan_stops if plan_stops is not None else ctx.get('plan_stops'))

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
    # Current grade (%) from the route's elevation profile at the rider's
    # position — Garmin LiveTrack sends altitude but no per-point gradient.
    now_block['grade_pct'] = tlm.grade_at(ctx.get('track'), idx)
    # Average speeds over the ride so far: elapsed (wall-clock, includes stops)
    # and moving-only. Complements the instantaneous speed_mph above.
    now_block['avg_elapsed_speed_mph'] = (
        round(dist_mi / (elapsed_min / 60.0), 1) if elapsed_min and elapsed_min > 0 else None)
    now_block['avg_moving_speed_mph'] = (
        round(dist_mi / (moving_min / 60.0), 1) if moving_min and moving_min > 0 else None)
    now_block['ascent_done_ft'] = ascent_done
    wind_done_label, wind_done_mph = wind_descriptor(hw_done, cw_done)
    now_block['headwind_done_mph'] = wind_done_mph
    now_block['headwind_done_label'] = wind_done_label
    wind_ahead_label, wind_ahead_mph = wind_descriptor(hw_ahead, cw_ahead)

    # Next waypoint/control ahead, with the plan's expected arrival time there.
    nc = tlm.next_control(dist_mi,
                          plan_stops if plan_stops is not None else ctx.get('plan_stops'))
    next_control_block = None
    if nc:
        # ETA is the plan's ARRIVAL (reaching) time at the control — arrival_time_min,
        # not cum_time_min, so a control with a break shows when you get there, not
        # when you leave.
        arrival_min = nc.get('arrival_time_min')
        eta_iso = eta_label = None
        if start is not None and arrival_min is not None:
            eta_dt = start + timedelta(minutes=arrival_min)
            eta_iso = eta_dt.isoformat()
            eta_label = eta_dt.astimezone(CLUB_TZ).strftime('%I:%M %p').lstrip('0')
        # Speed the rider must hold to make the plan's arrival. behind=True when that
        # time has already passed → renderers show an em-dash / "behind", never a
        # negative or divide-by-zero value.
        req_mph, behind = tlm.required_speed_mph(
            nc.get('dist_to_go_mi'), arrival_min, elapsed_min)
        next_control_block = {
            'name': nc.get('location'),
            'type': nc.get('stop_type'),
            'distance_mi': nc.get('distance_miles'),
            'dist_to_go_mi': nc.get('dist_to_go_mi'),
            'arrival_time_min': arrival_min,
            'eta_iso': eta_iso,
            'eta_label': eta_label,
            'required_mph': req_mph,
            'behind': behind,
        }

    # Speed to reach the FINISH on time (item 3), alongside the speed-to-next-control
    # above. Both use the SAME plan (the applied/selected plan) and the same
    # required_speed_mph helper — behind → required_mph None + behind True (em-dash).
    active_stops = plan_stops if plan_stops is not None else ctx.get('plan_stops')
    fin = tlm.finish_stop(active_stops)
    finish_block = None
    if fin:
        fin_arrival = fin.get('arrival_time_min')
        dist_to_finish = round(max(0.0, fin['distance_miles'] - dist_mi), 1)
        fin_req_mph, fin_behind = tlm.required_speed_mph(
            dist_to_finish, fin_arrival, elapsed_min)
        fin_eta_iso = fin_eta_label = None
        if start is not None and fin_arrival is not None:
            fin_eta_dt = start + timedelta(minutes=fin_arrival)
            fin_eta_iso = fin_eta_dt.isoformat()
            fin_eta_label = fin_eta_dt.astimezone(CLUB_TZ).strftime('%I:%M %p').lstrip('0')
        finish_block = {
            'name': fin.get('location'),
            'type': fin.get('stop_type'),
            'distance_mi': fin.get('distance_miles'),
            'dist_to_go_mi': dist_to_finish,
            'arrival_time_min': fin_arrival,
            'eta_iso': fin_eta_iso,
            'eta_label': fin_eta_label,
            'required_mph': fin_req_mph,
            'behind': fin_behind,
        }

    # Time banked, shown BOTH ways: vs the brevet CUTOFF (OTL margin at the rider's
    # current distance) and vs the PLAN (= plan delta). Both are surfaced explicitly
    # in addition to the ahead/behind badge (which stays driven by plan.status).
    banked_cutoff = tlm.time_banked_cutoff_min(
        dist_mi, elapsed_min, ctx.get('plan_total_mi'), ctx.get('plan_cutoff_hours'))

    return {
        'on_route': True,
        'now': now_block,
        'next_control': next_control_block,
        'finish': finish_block,
        'remaining': {
            'distance_mi': round(remaining_mi, 1),
            'ascent_left_ft': ascent_left,
            'headwind_ahead_mph': wind_ahead_mph,
            'headwind_ahead_label': wind_ahead_label,
            'time_left_min': time_left_min,
            'toughness': tuf,
        },
        'time_banked_cutoff_min': banked_cutoff,
        'time_banked_plan_min': delta,   # = plan.delta_min, surfaced explicitly
        'plan': ({'delta_min': delta,
                  'banked_min': delta,   # banked-vs-plan, alongside the badge
                  'status': 'ahead' if delta > 2 else ('behind' if delta < -2 else 'on')}
                 if delta is not None else None),
        'detailed_after_ride': True,   # power / pedaling-vs-coasting come from Strava post-ride
    }


@live_bp.route('/api/live/positions')
def live_positions():
    """JSON: latest position + live telemetry per opted-in GOING rider for ?ride_id=.

    Auth: a logged-in club member (web session or mobile Bearer token) for any
    ride, OR an unauthenticated guest holding a valid invite code for THIS ride
    (read-only). The heavy route/weather context is cached per ride; only
    per-rider numbers are recomputed each poll.
    """
    ride_id = request.args.get('ride_id', type=int)
    if not ride_id:
        return jsonify({'error': 'ride_id is required'}), 400

    user_id, rider_id = resolve_identity()
    g.rider_id = rider_id
    is_guest = (not rider_id) and (_guest_ride_id() == ride_id)
    if not rider_id and not is_guest:
        if user_id:
            return jsonify({'error': 'Complete your profile to view live tracking'}), 403
        return jsonify({'error': 'Authentication required'}), 401

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=DISPLAY_WINDOW_HOURS)
    rows = get_latest_positions_for_ride(ride_id, since)

    # Build the per-ride context ALWAYS — not only when someone is sharing — so a
    # spectator opening a ride before anyone broadcasts still gets the plan selector,
    # the route-ahead charts, and the shared upcoming-controls list. The context is
    # memoized per ride (~5 min), so the RWGPS/weather work still runs at most once
    # per window regardless of how many riders (or none) are active.
    ctx = _ride_live_context(ride_id)

    has_route = bool(ctx and ctx.get('has_route'))
    track = ctx.get('track') if has_route else None

    # Plan selector (item 1): the allow-set is the sole source of resolvable plans, so
    # a private plan can never leak to a guest or another rider. The requested plan_id
    # is resolved strictly against it; a rejected id falls back to the base plan.
    base_plan_id = ctx.get('base_plan_id') if ctx else None
    plan_options, allowed_custom_ids = _available_plans(base_plan_id, rider_id)
    # is_member gates the 'own' (each-rider's-own) lens to logged-in riders; a guest
    # (rider_id None) requesting it falls back to base, never per-rider private grading.
    applied_plan_id, override_stops = _selected_plan_stops(
        request.args.get('plan_id'), ctx, allowed_custom_ids, is_member=bool(rider_id))

    # The plan whose controls populate the shared upcoming-controls list: the applied
    # override (base or the selected custom), or — for 'own' — the base plan, since no
    # single per-rider schedule exists at ride level.
    list_stops = override_stops if override_stops is not None else (
        ctx.get('plan_stops') if ctx else None)
    start_utc = None
    if ctx and ctx.get('ride_start_iso'):
        try:
            start_utc = datetime.fromisoformat(ctx['ride_start_iso'])
        except ValueError:
            start_utc = None

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
            # A selected named plan (base or a custom) overrides EVERY rider's grading
            # so the whole view compares riders on one schedule; the 'own' lens
            # (override_stops None) keeps each rider on their own custom plan.
            if override_stops is not None:
                rider_plan_stops = override_stops
            else:
                rider_plan_stops = _rider_plan_stops(ctx, row['rider_id']) if ctx else None
            telemetry = _rider_telemetry(row, ctx, now, history, plan_stops=rider_plan_stops)
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
            # Plan-timing dot color (ahead=green / behind=red / grey=unknown), used
            # by the map instead of the signup-status `color`. Falls back to `color`
            # when no plan is matched, so it's always safe to use.
            'plan_color': _plan_dot_color(status, telemetry),
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

    # Leader (furthest-along on-route rider) drives the shared upcoming-controls list.
    leader_dist_mi = None
    for p in positions:
        t = p.get('telemetry') or {}
        d = (t.get('now') or {}).get('distance_mi')
        if d is not None and (leader_dist_mi is None or d > leader_dist_mi):
            leader_dist_mi = d
    upcoming_controls = _upcoming_controls(list_stops, leader_dist_mi, start_utc)

    return jsonify({
        'ride_id': ride_id,
        'positions': positions,
        'stale_after_minutes': STALE_AFTER_MINUTES,
        'server_time': now.isoformat(),
        # Route-ahead weather-style chart series (elevation / headwind / temperature),
        # static per ride. Top-level (not per-rider): each rider's current position is
        # marked from their telemetry.now.distance_mi. Null when the ride has no route.
        'chart_data': ctx.get('chart_data') if ctx else None,
        # Plan selector (item 1): the options the viewer may pick (base + allowed
        # custom plans + 'own'; base only for a single-plan ride) and the plan actually
        # APPLIED — 'base', 'own', or an int id (a rejected id echoes as 'base').
        'plans': plan_options,
        'selected_plan_id': applied_plan_id,
        # Shared upcoming controls of the applied plan with club-local ETAs (item 2).
        'upcoming_controls': upcoming_controls,
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
