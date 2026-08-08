"""Live rider location tracking routes (PR 1 — Garmin LiveTrack).

Club-login-only, opt-in. Three surfaces:
  GET/POST /live/settings        — rider opts in + registers a Garmin LiveTrack URL
  GET      /ride/<id>/live       — per-ride map (RWGPS route line + live rider dots)
  GET      /api/live/positions   — JSON: latest point per opted-in GOING rider

The poll cron that writes positions lives in routes/cron.py.
"""
import hashlib
import math
import re
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, jsonify, current_app, flash, abort, g,
                   has_app_context)

from auth import profile_required, token_or_session_required, resolve_identity
from cache import cache, CACHE_TIMEOUT
from models import (get_ride_by_id, get_live_tracking, set_live_tracking_enabled,
                    set_ride_garmin, clear_ride_garmin,
                    get_latest_positions_for_ride, insert_live_position,
                    get_going_riders_for_ride,
                    get_rider_upcoming_signups, get_ride_plan_stops,
                    get_followed_live_ride_ids, get_followed_live_rides,
                    set_followed_live_ride,
                    get_positions_for_rider_since, get_default_time_limit,
                    get_live_telemetry_snapshot, upsert_live_telemetry_snapshot,
                    get_or_create_ride_invite, get_valid_ride_invite,
                    get_route_elevation_track, get_route_weather_elevation_track,
                    RideStatus)
from services.garmin_livetrack import parse_session
from services.club_clock import (ride_timezone, schedule_time_labels,
                                 instant_time_labels)
from services.rwgps import extract_rwgps_route_id, fetch_route
from services import live_telemetry as tlm
from services import live_radial as radial
from shared.strategies import compute_pace_strategies
from shared.control_times import control_close_time_minutes
from services.weather import (sample_track_points, load_stored_route_weather,
                              calculate_bearing, headwind_component,
                              crosswind_component, classify_wind,
                              wind_arrow_rotation, wind_arrow_glyph,
                              build_arrival_interpolator, build_weather_segments,
                              build_chart_data, build_live_weather_markers)

live_bp = Blueprint('live', __name__)

M_TO_MI = 1 / 1609.344
KMH_TO_MPH = 0.621371
MS_TO_MPH = 2.236936
_MAX_CONTEXT_TRACK_POINTS = 5000
# The per-ride live context (route geometry + weather + chart_data + plan stops)
# is rider-independent and changes slowly, so it gets a longer TTL than the
# global CACHE_TIMEOUT (5 min) to cut the CPU cost of rebuilding the weather/route
# work on every deploy or cache expiry. Rider POSITIONS are NOT cached — they're
# read fresh each poll — so this only ages the static route/weather overlay.
LIVE_CONTEXT_TTL = 900  # 15 minutes

# The fully composed public roster is substantially more expensive than the
# static route context above: it loads each rider's history and derives all live
# telemetry. Keep it deliberately short so the map remains live while a burst of
# viewers shares one computation per server instance / CDN freshness window.
LIVE_ROSTER_TTL = 15
LIVE_ROSTER_PUBLIC_CACHE_CONTROL = 'public, s-maxage=15, stale-while-revalidate=30'
LIVE_ROSTER_PRIVATE_CACHE_CONTROL = 'private, no-store'

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
        # The scheduled event may override the reusable plan's default clock
        # time. Live elapsed/stopped time must follow the actual ride.
        start_t = ride.get('start_time') or ride.get('plan_start_time') or '06:00'
        hh, mm = (int(x) for x in str(start_t).split(':')[:2])
        d = ride['date']
        if isinstance(d, str):
            d = date.fromisoformat(d)
        return datetime(d.year, d.month, d.day, hh, mm,
                        tzinfo=ride_timezone(ride)).astimezone(timezone.utc)
    except Exception:
        return None

# Display tuning: a rider must have a recent point to appear, while telemetry
# history may span the whole multi-day ride (bounded by database retention).
DISPLAY_WINDOW_HOURS = 24
POSITION_RETENTION_DAYS = 7
STALE_AFTER_MINUTES = 10


def _telemetry_history_since(ctx, now):
    """Start of ride-scoped telemetry needed for elapsed and daily metrics.

    The latest-position gate intentionally remains 24 hours, but using that
    same window for history made Day 1 stops disappear during a multi-day
    brevet. Prefer the event's actual start and bound the query to the same
    seven-day window used by position retention. Fall back to 24 hours when a
    context has no usable start.
    """
    fallback = now - timedelta(hours=DISPLAY_WINDOW_HOURS)
    raw_start = ctx.get('ride_start_iso') if ctx else None
    if not raw_start:
        return fallback
    try:
        start = datetime.fromisoformat(raw_start)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        else:
            start = start.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return fallback
    retention_start = now - timedelta(days=POSITION_RETENTION_DAYS)
    return max(retention_start, min(fallback, start))

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


# The geometry cache is already reduced to at most 5,000 points. Do not apply a
# second aggressive reduction here: on a 1,200 km route a 1,000-point cap made
# multi-mile straight chords cut across winding roads.
_MAX_POLYLINE_POINTS = 5000


def _planned_day_number(ride, timed_stops, now=None):
    """Active plan day from elapsed time at each overnight departure boundary.

    A new route day starts when the prior day's final stop (normally the sleep
    control) is complete, not at civil midnight and not upon arrival at the first
    control on the new route. ``timed_stops`` carries cumulative planned minutes.
    """
    boundaries = {}
    previous_cum = 0
    for stop in timed_stops or []:
        match = re.match(r'\s*Day\s+(\d+)\s*:', stop.get('location') or '', re.I)
        if match:
            day = int(match.group(1))
            boundaries.setdefault(day, previous_cum if day > 1 else 0)
        previous_cum = int(stop.get('cum_time_min') or previous_cum)
    if not boundaries:
        return 1

    start_utc = _ride_start_utc(ride)
    current_utc = now or datetime.now(timezone.utc)
    if current_utc.tzinfo is None:
        current_utc = current_utc.replace(tzinfo=timezone.utc)
    elapsed_min = ((current_utc.astimezone(timezone.utc) - start_utc).total_seconds() / 60
                   if start_utc else 0)
    active = min(boundaries)
    for day, boundary_min in sorted(boundaries.items()):
        if elapsed_min >= boundary_min:
            active = day
    return active


def _day_distance_boundaries(stops):
    """First cumulative route mile belonging to each named plan day."""
    boundaries = {}
    for stop in stops or []:
        match = re.match(r'\s*Day\s+(\d+)\s*:', stop.get('location') or '', re.I)
        if match:
            boundaries.setdefault(int(match.group(1)),
                                  float(stop.get('distance_miles') or 0))
    return boundaries


def _progress_day_number(ride, legs, stops):
    """Active day from progress along the plan's single full-course distance axis.

    Projecting against the individual day routes is ambiguous where legs overlap:
    a rider near mile 235 can appear near mile 0/120 on another leg.  The plan page
    already has the canonical full-course geometry, so live uses that same track.
    """
    ride_id = (ride or {}).get('id')
    boundaries = _day_distance_boundaries(stops)
    if not ride_id or len(boundaries) < 2:
        return None
    cache_key = f'_live_progress_day_{ride_id}'
    if has_app_context() and hasattr(g, cache_key):
        return getattr(g, cache_key)
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=DISPLAY_WINDOW_HOURS)
        rows = get_latest_positions_for_ride(ride_id, since)
        track = _radial_overview_track(ride)
        if not track:
            return None
        leader_mile = None
        for row in rows or []:
            dist_m, _idx, off_by_m = tlm.project_to_route(
                float(row['lat']), float(row['lng']), track)
            if (dist_m is not None and off_by_m is not None
                    and off_by_m <= tlm.ON_ROUTE_MAX_M):
                mile = dist_m * M_TO_MI
                leader_mile = mile if leader_mile is None else max(leader_mile, mile)
        active = None
        if leader_mile is not None:
            active = min(boundaries)
            for day, start_mile in sorted(boundaries.items()):
                if leader_mile >= start_mile:
                    active = day
        if has_app_context():
            setattr(g, cache_key, active)
        return active
    except Exception:
        if has_app_context():
            current_app.logger.warning('live: progress day resolution failed', exc_info=True)
        return None


def _active_plan_leg(ride, now=None, day_number=None):
    """Resolve a route/weather leg by day, defaulting to the current event day."""
    fallback = {
        'day_number': 1,
        'rwgps_url': ((ride or {}).get('rwgps_url_team') or (ride or {}).get('rwgps_url')),
        'forecast_date': (ride or {}).get('date'),
        'distance_offset_mi': 0.0,
    }
    if not ride:
        return fallback
    try:
        from models import get_ride_plan_legs
        plan = _resolve_base_plan(ride)
        legs = [dict(row) for row in (get_ride_plan_legs(plan['id']) or [])] if plan else []
        if not legs:
            return fallback
        ride_date = ride.get('date')
        if isinstance(ride_date, str):
            ride_date = date.fromisoformat(ride_date)
        if day_number is None:
            raw_stops = get_ride_plan_stops(plan['id']) or []
            timed_stops = _compute_base_timing(raw_stops, None, 0)
            day_number = (_progress_day_number(ride, legs, raw_stops)
                          or _planned_day_number(ride, timed_stops, now=now))
        leg = min(legs, key=lambda row: abs(int(row.get('day_number') or 1) - int(day_number)))
        day_number = int(leg.get('day_number') or 1)

        offset_mi = 0.0
        for stop in get_ride_plan_stops(plan['id']) or []:
            if re.match(rf'^\s*Day\s+{day_number}\s*:', stop.get('location') or '', re.I):
                offset_mi = float(stop.get('distance_miles') or 0)
                break
        leg.update({
            'day_number': day_number,
            'forecast_date': ride_date + timedelta(days=day_number - 1),
            'distance_offset_mi': offset_mi,
        })
        return leg
    except Exception:
        if has_app_context():
            current_app.logger.warning('live: active route leg resolution failed', exc_info=True)
        return fallback


def _build_route_polyline(ride):
    """Return a downsampled [[lng, lat], ...] polyline for the ride's RWGPS route.

    Fail-soft: returns None on any missing route / fetch error so the map still
    renders with rider dots only.
    """
    rwgps_url = _active_plan_leg(ride).get('rwgps_url')
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


def _radial_track(ride, leg=None):
    """Fetch + downsample the ride's RWGPS route ONCE into a track
    [{lat, lng, dist_m, e_m}] feeding BOTH the shared map polyline and the altitude
    profile (one fetch, not two). Fail-soft → None on any missing route / fetch
    error so the shared live view still renders with rider markers only."""
    leg = leg or _active_plan_leg(ride)
    rwgps_url = leg.get('rwgps_url')
    route_id = extract_rwgps_route_id(rwgps_url)
    if not route_id:
        return None
    # Prefer the cron-warmed geometry (route_geometry_cache): it renders the route
    # line + altitude profile without a live RWGPS fetch on the request path (TA-237),
    # and it works for routes the authenticated RWGPS API can't serve (which would
    # otherwise leave the live map with no route line or profile). The cached track is
    # already the [{lat, lng, dist_m, e_m}] shape this function returns.
    try:
        cached = get_route_elevation_track(route_id)
    except Exception:  # noqa: BLE001 — cache read is best-effort; fall back to fetch
        cached = None
    if cached:
        offset_m = float(leg.get('distance_offset_mi') or 0) / M_TO_MI
        return [dict(point, dist_m=float(point.get('dist_m') or 0) + offset_m)
                for point in cached]
    # Multi-day private RWGPS legs may have their weather cache warmed even when
    # the route-keyed geometry cache is cold. Use the route-shaped elevation
    # track stored with that forecast. Never draw ``sample_points`` as a route:
    # those are sparse weather lookup locations and straight lines between them
    # visibly cut across roads.
    try:
        route_date = leg.get('forecast_date')
        if isinstance(route_date, str):
            route_date = date.fromisoformat(route_date)
        weather_track = get_route_weather_elevation_track(route_id, route_date)
        if weather_track:
            offset_m = float(leg.get('distance_offset_mi') or 0) / M_TO_MI
            return [dict(point, dist_m=float(point.get('dist_m') or 0) + offset_m)
                    for point in weather_track]
    except Exception:  # noqa: BLE001 — fall through to the existing RWGPS fallback
        current_app.logger.warning('live: stored weather track fallback failed', exc_info=True)
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
    offset_m = float(leg.get('distance_offset_mi') or 0) / M_TO_MI
    return [dict(point, dist_m=float(point.get('dist_m') or 0) + offset_m)
            for point in track]


def _radial_overview_track(ride):
    """Canonical full-course track, identical to the v2 ride-plan route.

    Multi-day plans may also carry one RWGPS URL per day.  Those are weather
    sources, not pieces to append to the plan's already-complete primary route.
    """
    try:
        plan = _resolve_base_plan(ride)
        # Match ride_plan_v2: a linked ride's team route wins; otherwise the
        # plan's official route is the full-course source.
        if plan:
            primary_url = (plan.get('rwgps_url_team')
                           if ride.get('rwgps_url_team') else plan.get('rwgps_url'))
            primary_url = (primary_url or plan.get('rwgps_url')
                           or plan.get('rwgps_url_team'))
        else:
            primary_url = None
        primary_url = (primary_url or ride.get('rwgps_url_team')
                       or ride.get('rwgps_url'))
        track = _radial_track(ride, {
            'rwgps_url': primary_url,
            'distance_offset_mi': 0.0,
        })
        return track or []
    except Exception:  # noqa: BLE001 — overview degrades to active route
        if has_app_context():
            current_app.logger.warning('live: plan route build failed', exc_info=True)
        return _radial_track(ride) or []


def _radial_polyline(track):
    """[[lng, lat], …] for the Mapbox route line from a _radial_track track, capped
    to _MAX_POLYLINE_POINTS. None when there's no track."""
    if not track:
        return None
    coords = [[t['lng'], t['lat']] for t in track]
    if len(coords) > _MAX_POLYLINE_POINTS:
        final = coords[-1]
        step = len(coords) // _MAX_POLYLINE_POINTS + 1
        coords = coords[::step]
        if coords[-1] != final:
            coords.append(final)
    return coords


def _build_weather_points(ride, leg=None):
    """Along-route weather markers for the live map: {lat,lng,temp_f,wind_speed_mph,
    wind_type,arrow_deg,color} from the STORED route weather (route_weather_cache — a DB
    read, never a live Open-Meteo fetch on the request path, per TA-237). The shared
    build_live_weather_markers does all the wind math (same code as the plan page).
    Fail-soft: no route / no forecast / any error → [] so the map just omits weather."""
    try:
        leg = leg or _active_plan_leg(ride)
        route_id = extract_rwgps_route_id(leg.get('rwgps_url'))
        rd = leg.get('forecast_date')
        if isinstance(rd, str):
            rd = date.fromisoformat(rd)
        if not route_id or not rd:
            return []
        weather_data, sample_points = load_stored_route_weather(route_id, rd)
        start_t = ride.get('start_time') or ride.get('plan_start_time') or '06:00'
        return build_live_weather_markers(weather_data, sample_points, rd, str(start_t))
    except Exception:  # noqa: BLE001 — the weather overlay is best-effort
        current_app.logger.warning('live: weather_points build failed', exc_info=True)
        return []


def _build_all_weather_points(ride):
    """Map markers for every multi-day leg, each using its own forecast date."""
    try:
        from models import get_ride_plan_legs

        plan = _resolve_base_plan(ride)
        legs = [dict(row) for row in (get_ride_plan_legs(plan['id']) or [])] if plan else []
        if len(legs) < 2:
            return _build_weather_points(ride)
        ride_date = ride.get('date')
        if isinstance(ride_date, str):
            ride_date = date.fromisoformat(ride_date)
        stops = get_ride_plan_stops(plan['id']) or []
        boundaries = _day_distance_boundaries(stops)
        points = []
        for leg in sorted(legs, key=lambda row: int(
                row.get('day_number') or row.get('leg_order') or 1)):
            day = int(leg.get('day_number') or leg.get('leg_order') or 1)
            leg.update({
                'day_number': day,
                'distance_offset_mi': boundaries.get(day, 0.0),
                'forecast_date': (ride_date + timedelta(days=day - 1)
                                  if ride_date else None),
            })
            points.extend(_build_weather_points(ride, leg))
        return points
    except Exception:  # noqa: BLE001 — map weather remains best-effort
        if has_app_context():
            current_app.logger.warning('live: all-leg weather markers failed', exc_info=True)
        return _build_weather_points(ride)


def _build_all_day_weather(ride, selected_day=None, active_day=None):
    """Stored headwind and temperature chart data for every multi-day leg.

    The map remains scoped to the selected leg while every day's charts are
    visible below it. This avoids combining unrelated route geometries on one
    map while keeping the whole forecast on-page. It is intentionally cache/DB-only:
    ``load_stored_route_weather`` never calls Open-Meteo on the request path.
    """
    try:
        from models import get_ride_plan_legs

        plan = _resolve_base_plan(ride)
        if not plan:
            return None
        legs = [dict(row) for row in (get_ride_plan_legs(plan['id']) or [])]
        if len(legs) < 2:
            return None

        ride_date = ride.get('date')
        if isinstance(ride_date, str):
            ride_date = date.fromisoformat(ride_date)
        active_day = int(active_day or _active_plan_leg(ride).get('day_number') or 1)
        selected_day = int(selected_day or active_day)
        plan_stops = get_ride_plan_stops(plan['id']) or []
        days = []
        for leg in legs:
            day_number = int(leg.get('day_number') or leg.get('leg_order') or 1)
            route_id = extract_rwgps_route_id(leg.get('rwgps_url'))
            forecast_date = ride_date + timedelta(days=day_number - 1)
            weather_data, sample_points = load_stored_route_weather(route_id, forecast_date)
            offset_mi = 0.0
            for stop in plan_stops:
                if re.match(rf'^\s*Day\s+{day_number}\s*:',
                            stop.get('location') or '', re.I):
                    offset_mi = float(stop.get('distance_miles') or 0)
                    break
            offset_m = offset_mi / M_TO_MI
            adjusted_samples = [
                dict(point, distance_m=float(point.get('distance_m') or 0) + offset_m)
                for point in (sample_points or [])
            ]
            cached_track = get_route_elevation_track(route_id) or []
            track_points = [
                {'d': float(point.get('dist_m') or 0) + offset_m,
                 'e': point.get('e_m')}
                for point in cached_track
            ]
            chart_data = _build_live_chart_data(
                adjusted_samples, weather_data, track_points, plan_stops,
                _ride_start_local(ride))
            temperatures = [float(v) for v in ((chart_data or {}).get('temperature_f') or [])
                            if v is not None]
            headwinds = [float(v) for v in ((chart_data or {}).get('headwind_mph') or [])
                         if v is not None]
            max_distance_m = max(
                (float(p.get('distance_m') or 0) for p in (sample_points or [])),
                default=0,
            )
            days.append({
                'day_number': day_number,
                'label': leg.get('label') or f'Day {day_number}',
                'forecast_date': forecast_date.strftime('%a, %b %-d'),
                'is_current': day_number == active_day,
                'is_selected': day_number == selected_day,
                'available': bool(chart_data),
                'distance_mi': round(max_distance_m * M_TO_MI),
                'start_distance_mi': round(offset_mi, 1),
                'chart_data': chart_data,
                'temperature_min_f': round(min(temperatures)) if temperatures else None,
                'temperature_max_f': round(max(temperatures)) if temperatures else None,
                'peak_headwind_mph': (round(max([0.0] + headwinds))
                                      if headwinds else None),
                'peak_tailwind_mph': (round(abs(min([0.0] + headwinds)))
                                      if headwinds else None),
                'plan': _build_plan_snapshot(ride, selected_day=day_number),
            })

        first_url = legs[0].get('rwgps_url') or ''
        start_t = str(ride.get('start_time') or ride.get('plan_start_time') or '06:00')
        start_date_time = f'{ride_date.isoformat()}T{start_t}'
        return {
            'days': days,
            'url': url_for('weather.weather_page', rwgps_url=first_url,
                           plan_slug=plan.get('slug', ''),
                           start_datetime=start_date_time, auto='1'),
        }
    except Exception:  # noqa: BLE001 — live weather summaries are best-effort
        current_app.logger.warning('live: all-day weather build failed', exc_info=True)
        return None


def _build_plan_snapshot(ride, selected_day=None):
    """A compact summary of the ride's resolved plan for the live page — the plan
    name (linked to its plan page), distance, climb, control count, start time — shown
    beside the climb profile. Resolves the plan the SAME way the live grading does
    (FK then route-name match), so it matches the selector's base plan. Fail-soft:
    returns None on no plan / any error so the live page simply omits the panel."""
    try:
        plan = _resolve_base_plan(ride)
        if not plan:
            return None
        try:
            stops = get_ride_plan_stops(plan['id']) or []
        except Exception:  # noqa: BLE001 — count is best-effort
            stops = []
        controls = sum(1 for s in stops
                       if (s.get('stop_type') or '').lower() == 'control')
        # A malformed cutoff must only drop the "h limit" stat, not the whole panel.
        try:
            raw_cutoff = ride.get('time_limit_hours') or plan.get('cutoff_hours')
            cutoff = round(float(raw_cutoff), 1) if raw_cutoff else None
        except (TypeError, ValueError):
            cutoff = None
        total_mi = float(plan.get('total_distance_miles') or 0)
        event_km = plan.get('distance_km') or ride.get('distance_km')
        timed_stops = _compute_base_timing(stops, cutoff, total_mi, event_km)

        # Advance only at each planned overnight departure, not at midnight.
        active_day = _planned_day_number(ride, timed_stops)
        named_days = []
        for stop in timed_stops:
            match = re.match(r'\s*Day\s+(\d+)\s*:', stop.get('location') or '', re.I)
            if match:
                named_days.append(int(match.group(1)))
        if named_days:
            active_day = min(max(active_day, min(named_days)), max(named_days))
        current_day = active_day
        if selected_day is not None and named_days:
            active_day = min(max(int(selected_day), min(named_days)), max(named_days))

        start_raw = ride.get('start_time') or plan.get('start_time') or '06:00'
        try:
            start_h, start_m = (int(x) for x in str(start_raw).split(':')[:2])
        except (TypeError, ValueError):
            start_h, start_m = 6, 0
        day_rows = []
        day_timed_stops = []
        for stop in timed_stops:
            name = stop.get('location') or ''
            match = re.match(r'\s*Day\s+(\d+)\s*:\s*(.*)', name, re.I)
            if not match or int(match.group(1)) != active_day:
                continue
            day_timed_stops.append(stop)
            arrival = int(stop.get('arrival_time_min') or 0)
            clock_total = start_h * 60 + start_m + arrival
            _, clock_min = divmod(clock_total, 24 * 60)
            hh, mm = divmod(clock_min, 60)
            day_rows.append({
                'name': match.group(2),
                'distance_mi': round(float(stop.get('distance_miles') or 0), 1),
                'eta': f'{hh:02d}:{mm:02d}',
                'break_min': int(stop.get('stop_duration_min') or 0),
                'type': (stop.get('stop_type') or 'waypoint').lower(),
                'time_bank_min': stop.get('time_bank_min'),
            })
            labels = schedule_time_labels(ride, start_raw, arrival)
            day_rows[-1].update({
                'eta': labels['event'],
                'eta_event_zone': labels['event_zone'],
                'eta_pacific': labels['pacific'],
                'show_pacific': labels['show_pacific'],
            })

        # Single-day plans commonly have no "Day 1:" prefix. In that case the
        # whole itinerary is Day 1; do not render a misleading zero-mile card.
        if not named_days and active_day == 1:
            day_timed_stops = list(timed_stops)
            day_rows = []
            for stop in day_timed_stops:
                arrival = int(stop.get('arrival_time_min') or 0)
                labels = schedule_time_labels(ride, start_raw, arrival)
                day_rows.append({
                    'name': stop.get('location') or 'Stop',
                    'distance_mi': round(float(stop.get('distance_miles') or 0), 1),
                    'eta': labels['event'],
                    'eta_event_zone': labels['event_zone'],
                    'eta_pacific': labels['pacific'],
                    'show_pacific': labels['show_pacific'],
                    'break_min': int(stop.get('stop_duration_min') or 0),
                    'type': (stop.get('stop_type') or 'waypoint').lower(),
                    'time_bank_min': stop.get('time_bank_min'),
                })

        first_day_mi = (float(day_timed_stops[0].get('distance_miles') or 0)
                        if day_timed_stops else 0.0)
        last_day_mi = (float(day_timed_stops[-1].get('distance_miles') or 0)
                       if day_timed_stops else first_day_mi)
        day_moving_min = sum(int(s.get('segment_time_min') or 0)
                             for s in day_timed_stops)
        day_stopped_min = sum(int(s.get('stop_duration_min') or 0)
                              for s in day_timed_stops)
        day_banks = [s.get('time_bank_min') for s in day_timed_stops
                     if s.get('time_bank_min') is not None]

        start_labels = schedule_time_labels(ride, start_raw, 0)
        return {
            'name': plan.get('name'),
            'slug': plan.get('slug'),
            'distance_mi': round(total_mi),
            'elevation_ft': int(plan.get('total_elevation_ft') or 0),
            'controls': controls,
            'cutoff_hours': cutoff,
            'start_time': (ride.get('start_time') or plan.get('start_time') or None),
            'start_time_event': start_labels['event'],
            'start_time_event_zone': start_labels['event_zone'],
            'start_time_pacific': start_labels['pacific'],
            'show_pacific_time': start_labels['show_pacific'],
            'active_day': active_day,
            'is_current_day': active_day == current_day,
            'day_stops': day_rows,
            'day_distance_mi': round(max(0.0, last_day_mi - first_day_mi), 1),
            'day_elevation_ft': sum(int(s.get('elevation_gain') or 0)
                                    for s in day_timed_stops),
            'day_controls': sum(1 for s in day_timed_stops
                                if (s.get('stop_type') or '').lower() == 'control'),
            'day_moving_min': day_moving_min,
            'day_stopped_min': day_stopped_min,
            'day_elapsed_min': day_moving_min + day_stopped_min,
            'day_time_bank_min': day_banks[-1] if day_banks else None,
        }
    except Exception:  # noqa: BLE001 — the snapshot panel is best-effort
        current_app.logger.warning('live: plan snapshot build failed', exc_info=True)
        return None


@cache.memoize(timeout=LIVE_CONTEXT_TTL)
def _mobile_live_plan_snapshot(ride_id):
    """Cache the base-plan day summary exposed to native live viewers.

    The live positions endpoint polls every 30 seconds.  Building the web-parity
    snapshot performs plan/stop lookups, so keep it on the same slow-changing TTL
    as route and weather context instead of repeating that work for every rider.
    """
    try:
        ride = get_ride_by_id(ride_id)
        return _build_plan_snapshot(ride) if ride else None
    except Exception:  # noqa: BLE001 — additive mobile context must never sink polling
        current_app.logger.warning(
            'live: mobile plan snapshot failed for ride %s', ride_id, exc_info=True)
        return None


@live_bp.route('/live')
@profile_required
def live_hub():
    """Live tracking hub: share from this phone, set up Garmin, or open a ride's map."""
    rider_id = session['rider_id']
    tracking = get_live_tracking(rider_id)
    upcoming = get_rider_upcoming_signups(rider_id)
    followed = get_followed_live_rides(rider_id)
    return render_template(
        'live_hub.html',
        opted_in=bool(tracking and tracking.get('enabled')),
        has_garmin=bool(tracking and tracking.get('garmin_session_token')),
        upcoming=upcoming,
        followed=followed,
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


@cache.memoize(timeout=LIVE_CONTEXT_TTL)
def _live_page_static_payload(ride_id, selected_day, explicit_active_day):
    """Slow-changing route, weather, elevation, and plan data for live-page HTML.

    None of these fields is viewer-specific. Building them for a multi-day 1200K
    performs several plan/route/weather reads and profile transformations, so a
    browser reload should reuse the same payload rather than reconstructing it.
    Rider positions remain in the separately cached short-lived roster endpoint.
    """
    ride = get_ride_by_id(ride_id)
    if not ride:
        return None
    track = _radial_overview_track(ride)
    return {
        'route_polyline': _radial_polyline(track),
        'route_polylines': [_radial_polyline(track)] if track else [],
        'elevation_profile': radial.build_elevation_profile(track or []),
        'weather_points': _build_all_weather_points(ride),
        'all_day_weather': _build_all_day_weather(
            ride, selected_day, active_day=explicit_active_day),
        'plan_snapshot': _build_plan_snapshot(ride, selected_day),
    }


@live_bp.route('/ride/<int:ride_id>/live')
def ride_live_map(ride_id):
    """Per-ride live map: RWGPS route line + live dots for opted-in GOING riders.

    Open to logged-in club members, OR to an unauthenticated guest who entered a
    valid invite code for THIS ride at /live/join (read-only — member controls
    are hidden)."""
    is_member = bool(session.get('rider_id'))
    is_guest = (not is_member) and (_guest_ride_id() == ride_id)
    if not is_member and not is_guest:
        # A half-logged-in member finishes profile setup (no ride fetch needed).
        if session.get('user_id'):
            return redirect(url_for('auth.setup_profile'))
        # A fully anonymous viewer may still open a PUBLIC-live ride read-only, without
        # an invite (the roster.json it polls is already public for such a ride);
        # otherwise send them to the guest join page to enter an invite code.
        ride = get_ride_by_id(ride_id)
        if ride and ride.get('is_public_live'):
            is_guest = True          # read-only public viewer — member controls hidden
        else:
            return redirect(url_for('live.live_join'))
    else:
        ride = get_ride_by_id(ride_id)
    if not ride:
        abort(404)

    mapbox_token = current_app.config.get('MAPBOX_ACCESS_TOKEN', '')
    requested_day = request.args.get('day', type=int)
    auto_day = requested_day is None or request.args.get('auto') == '1'
    selected_leg = _active_plan_leg(ride, day_number=requested_day)
    selected_day = int(selected_leg.get('day_number') or 1)
    static_payload = _live_page_static_payload(
        ride_id, selected_day,
        selected_day if request.args.get('auto') == '1' else None,
    ) or {}
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
        route_polyline=static_payload.get('route_polyline'),
        route_polylines=static_payload.get('route_polylines') or [],
        elevation_profile=static_payload.get('elevation_profile'),
        weather_points=static_payload.get('weather_points') or [],
        all_day_weather=static_payload.get('all_day_weather'),
        roster_url=url_for('live.ride_live_roster', ride_id=ride_id),
        poll_seconds=RADIAL_POLL_SECONDS,
        stale_after_minutes=STALE_AFTER_MINUTES,
        opted_in=opted_in,
        garmin_here=garmin_here,
        garmin_url=garmin_url,
        is_guest=is_guest,
        plan_snapshot=static_payload.get('plan_snapshot'),
        auto_day=auto_day,
        selected_day=selected_day,
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


def _build_wind_by_dist(sample_points, forecasts):
    """Best-effort [{dist_m, headwind_kmh, crosswind_kmh, temperature_f}] using the
    current-hour wind from STORED weather sampled along the route (no live fetch —
    TA-237). `sample_points` / `forecasts` are the aligned points + forecasts the
    fetch-route-weather cron stored. Returns None on any failure (headwinds then
    degrade gracefully)."""
    try:
        if not sample_points or not forecasts or len(forecasts) != len(sample_points):
            return None
        samples = sample_points
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
        start_t = ride.get('start_time') or ride.get('plan_start_time') or '06:00'
        hh, mm = (int(x) for x in str(start_t).split(':')[:2])
        return datetime(d.year, d.month, d.day, hh, mm)
    except Exception:
        return None


def _build_live_chart_data(sample_points, forecasts, track_points, plan_stops, start_dt):
    """Route-ahead chart series for the live page, built from the STORED weather the
    fetch-route-weather cron pre-computed (no live fetch — TA-237) through the SAME
    time-aware pipeline the weather page uses — arrival-hour selection
    (build_weather_segments) → build_chart_data — so the live charts and the weather
    page can never diverge (item 5). `sample_points` / `forecasts` are the aligned stored
    points + forecasts; `track_points` supplies elevation. Each point is timed against the
    ride's BASE plan (plan_stops) when available, else a flat speed, exactly like
    routes/weather.py's build_weather_payload.

    Returns {labels, elevation_ft, headwind_mph, temperature_f} (aligned arrays,
    distance in mi) or None when the route is too short / the forecast is unavailable,
    so the caller hides the charts (today's graceful-degradation behavior)."""
    if not sample_points or not forecasts or len(forecasts) < 2:
        return None
    samples = sample_points
    if len(samples) < 2:
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
        'wind_gust_mph': cd.get('wind_gust_mph'),
        'temperature_f': cd['temperature_f'],
    }


@cache.memoize(LIVE_CONTEXT_TTL)
def _ride_live_context_cached(ride_id, day_key):
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
           'elevation_profile': {'available': False},
           # Base plan id + timing inputs so the per-rider custom plan (if any) can
           # be merged + retimed the SAME way the web plan page does (_rider_plan_stops).
           'base_plan_id': None, 'plan_cutoff_hours': None, 'plan_total_mi': 0.0,
           'plan_total_ascent_ft': None, 'plan_distance_km': None,
           'day_distance_boundaries': {},
           # Only explicitly classified permanents may begin partway around a
           # route. Scheduled brevets always measure from the official start.
           'allow_mid_route_start': False,
           'event_timezone': None}
    if not ride:
        return ctx

    ctx['allow_mid_route_start'] = (
        'permanent' in str(ride.get('ride_type') or '').strip().lower())
    ctx['event_timezone'] = getattr(ride_timezone(ride), 'key', 'America/Los_Angeles')

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
            ctx['plan_total_ascent_ft'] = float(plan.get('total_elevation_ft') or 0)
            ctx['plan_distance_km'] = plan.get('distance_km') or ride.get('distance_km')
            ctx['base_plan_id'] = plan['id']
            base_raw = _compute_base_timing(
                get_ride_plan_stops(plan['id']), ctx['plan_cutoff_hours'],
                ctx['plan_total_mi'], ctx['plan_distance_km'])
            ctx['day_distance_boundaries'] = _day_distance_boundaries(base_raw)
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

    # Route geometry: telemetry must use the canonical FULL course, not only the
    # active day's weather leg. Otherwise prior-day stops cannot be assigned and
    # overlapping daily routes can produce the wrong absolute progress.
    leg = _active_plan_leg(ride)
    rwgps_url = leg.get('rwgps_url')
    route_id = extract_rwgps_route_id(rwgps_url)
    if route_id:
        try:
            overview = _radial_overview_track(ride)
            tps = [
                {'x': point.get('lng'), 'y': point.get('lat'),
                 'd': point.get('dist_m'), 'e': point.get('e_m')}
                for point in overview
                if point.get('lng') is not None and point.get('lat') is not None
            ]
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
                ctx['elevation_profile'] = radial.build_elevation_profile(track)
                # Weather is pre-fetched hourly by the fetch-route-weather cron and READ
                # from storage — no live Open-Meteo on the live/telemetry path (TA-237).
                # Load once (keyed by route + ride date) and feed BOTH the per-rider
                # wind labels and the route-ahead charts.
                rd = leg.get('forecast_date')
                if isinstance(rd, str):
                    try:
                        rd = date.fromisoformat(rd)
                    except ValueError:
                        rd = None
                weather_data, weather_samples = (
                    load_stored_route_weather(route_id, rd) if rd else (None, None))
                if weather_samples:
                    offset_m = float(leg.get('distance_offset_mi') or 0) / M_TO_MI
                    weather_samples = [dict(sample,
                                            distance_m=float(sample.get('distance_m') or 0)
                                                       + offset_m)
                                       for sample in weather_samples]
                # Per-rider "wind done / ahead" labels from the stored current-hour wind.
                ctx['wind_by_dist'] = _build_wind_by_dist(weather_samples, weather_data)
                # Route-ahead charts (elevation / headwind / temperature) from the SAME
                # time-aware weather pipeline as the weather page, timed against the BASE
                # plan's arrival schedule (item 5). Static per ride, so it rides the cached
                # context; the per-poll path only adds each rider's position marker on top.
                try:
                    ctx['chart_data'] = _build_live_chart_data(
                        weather_samples, weather_data, tps, ctx['plan_stops'],
                        _ride_start_local(ride))
                except Exception as cexc:  # noqa: BLE001 — charts are best-effort
                    current_app.logger.warning(
                        'live ctx: chart_data failed for ride %s: %s', ride_id, cexc)
                    ctx['chart_data'] = None
        except Exception as exc:  # noqa: BLE001 — telemetry is best-effort
            current_app.logger.warning('live ctx: route %s failed: %s', route_id, exc)
    return ctx


def _ride_live_context(ride_id):
    """Day-keyed wrapper so the 15-minute static cache rolls at local midnight."""
    ride = get_ride_by_id(ride_id)
    day_key = int(_active_plan_leg(ride).get('day_number') or 1) if ride else 1
    return _ride_live_context_cached(ride_id, day_key)


def _ride_live_context_uncached(ride_id):
    """Test/debug escape hatch retained from the formerly decorated public helper."""
    ride = get_ride_by_id(ride_id)
    day_key = int(_active_plan_leg(ride).get('day_number') or 1) if ride else 1
    return _ride_live_context_cached.uncached(ride_id, day_key)


_ride_live_context.uncached = _ride_live_context_uncached


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


def _upcoming_controls(plan_stops, leader_dist_mi, start_utc, ride=None):
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
        eta_iso = eta_label = eta_pacific_label = None
        if start_utc is not None:
            eta_dt = start_utc + timedelta(minutes=arrival)
            eta_iso = eta_dt.isoformat()
            labels = instant_time_labels(eta_dt, ride or {})
            eta_label = f"{labels['event']} {labels['event_zone']}"
            if labels['show_pacific']:
                eta_pacific_label = f"{labels['pacific']} PT"
        out.append({
            'name': s.get('location') or None,
            'type': s.get('stop_type') or None,
            'distance_mi': round(dm, 1),
            'arrival_time_min': arrival,
            'eta_iso': eta_iso,
            'eta_label': eta_label,
            'eta_pacific_label': eta_pacific_label,
        })
    out.sort(key=lambda c: c['distance_mi'])
    return out


_WIND_SHORT = {'headwind': 'head', 'tailwind': 'tail', 'crosswind': 'cross'}


def _wind_descriptor(head_kmh, cross_kmh):
    """'↓ 8 mph head' — total wind magnitude (hypot of head+cross) in mph, a
    head/cross/tail label, and a direction arrow. Crosswind defaults to 0 so a
    head/tail-only context (legacy cache) still classifies; 'calm' below ~1 mph.
    Promoted to module scope so the shared composer's wind hook can reuse it."""
    if head_kmh is None:
        return None, None
    cross = cross_kmh or 0.0
    speed_mph = round(math.hypot(head_kmh, cross) * KMH_TO_MPH, 1)
    if speed_mph < 1:
        return 'calm', speed_mph
    glyph = wind_arrow_glyph(wind_arrow_rotation(head_kmh, cross))
    short = _WIND_SHORT[classify_wind(head_kmh, cross)]
    return f'{glyph} {speed_mph:g} mph {short}', speed_mph


def _rider_telemetry(row, ctx, now, history, plan_stops=None):
    """Assemble one rider's telemetry via the SHARED composer, then layer Team Asha's
    wind/weather fields on top — the one thing the framework-free shared builder
    can't compute. BOTH apps call shared.live_radial.compose_rider_telemetry so the
    per-rider math (distance / ascent / plan delta / next control / finish / OTL
    margin) can never fork. Team Asha anchors elapsed on the EVENT start, formats ETA
    labels in the club timezone, and injects head/cross-wind through the hook;
    BrevetHub degrades those fields it has no context for."""
    start = None
    if ctx.get('ride_start_iso'):
        try:
            start = datetime.fromisoformat(ctx['ride_start_iso'])
        except ValueError:
            start = None

    def wind_labeler(dist_m):
        # Wind done/ahead split at the rider's ABSOLUTE route position (same as the
        # pre-promotion behavior), from the ride context's stored current-hour wind.
        hw_done, hw_ahead = tlm.headwinds_split(ctx.get('wind_by_dist'), dist_m)
        cw_done, cw_ahead = tlm.crosswinds_split(ctx.get('wind_by_dist'), dist_m)
        wd_label, wd_mph = _wind_descriptor(hw_done, cw_done)
        wa_label, wa_mph = _wind_descriptor(hw_ahead, cw_ahead)
        return {'headwind_done_mph': wd_mph, 'headwind_done_label': wd_label,
                'headwind_ahead_mph': wa_mph, 'headwind_ahead_label': wa_label}

    event_tz = ZoneInfo(ctx.get('event_timezone') or 'America/Los_Angeles')
    return radial.compose_rider_telemetry(
        row, ctx, now, history, plan_stops=plan_stops, start=start, tz=event_tz,
        wind_labeler=wind_labeler, min_history=1, stateless_fallback=True,
        rebase_from_first_fix=bool(ctx.get('allow_mid_route_start')))


def build_live_telemetry_snapshot(ride_id, now=None):
    """Compute the common base-plan payload once for web and native clients.

    This is called by the Garmin poller after it stores fresh points.  Both
    response shapes are produced from the SAME telemetry objects, so route
    projection and multi-day stop analysis run once per rider rather than once
    per viewer and once per client type.
    """
    now = now or datetime.now(timezone.utc)
    ctx = _ride_live_context(ride_id)
    ride = get_ride_by_id(ride_id) or {'timezone': (ctx or {}).get('event_timezone')}
    rows = list(get_latest_positions_for_ride(
        ride_id, now - timedelta(hours=DISPLAY_WINDOW_HOURS)) or [])
    sharing_ids = {row['rider_id'] for row in rows}
    for rider in get_going_riders_for_ride(ride_id) or []:
        if rider['rider_id'] not in sharing_ids:
            rows.append(dict(rider, lat=None, lng=None, recorded_at=None,
                             source=None, speed=None, heart_rate=None,
                             power=None, cadence=None))

    track = ctx.get('track') if ctx and ctx.get('has_route') else None
    base_stops = ctx.get('plan_stops') if ctx else None
    positions, roster = [], []
    source_recorded_at = None
    for row in rows:
        recorded_at = row.get('recorded_at')
        telemetry = None
        history = []
        if recorded_at is not None:
            if recorded_at.tzinfo is None:
                recorded_at = recorded_at.replace(tzinfo=timezone.utc)
            source_recorded_at = max(source_recorded_at, recorded_at) \
                if source_recorded_at else recorded_at
            history = get_positions_for_rider_since(
                row['rider_id'], _telemetry_history_since(ctx, now), ride_id=ride_id)
            try:
                telemetry = _rider_telemetry(
                    row, ctx, now, history, plan_stops=base_stops)
                for block_name in ('next_control', 'finish'):
                    block = (telemetry or {}).get(block_name)
                    if not block or not block.get('eta_iso'):
                        continue
                    labels = instant_time_labels(
                        datetime.fromisoformat(block['eta_iso']), ride)
                    block['eta_label'] = f"{labels['event']} {labels['event_zone']}"
                    block['eta_pacific_label'] = (
                        f"{labels['pacific']} PT" if labels['show_pacific'] else None)
            except Exception:
                current_app.logger.exception(
                    'live snapshot telemetry failed for rider %s', row['rider_id'])

        minutes_ago = (max(0, int((now - recorded_at).total_seconds() // 60))
                       if recorded_at is not None else None)
        status = row.get('status')
        positions.append({
            'rider_id': row['rider_id'],
            'name': (row.get('name') or '').strip(),
            'lat': float(row['lat']) if row.get('lat') is not None else None,
            'lng': float(row['lng']) if row.get('lng') is not None else None,
            'status': status or RideStatus.GOING.value,
            'color': STATUS_COLORS.get(status, DEFAULT_COLOR),
            'plan_color': _plan_dot_color(status, telemetry),
            'recorded_at': recorded_at.isoformat() if recorded_at else None,
            'minutes_ago': minutes_ago,
            'stale': bool(minutes_ago is not None and minutes_ago > STALE_AFTER_MINUTES),
            'source': row.get('source') if recorded_at else None,
            'telemetry': telemetry,
            'trail': tlm.build_trail(history, track) if recorded_at else None,
            **({'not_sharing': True} if recorded_at is None else {}),
        })

        public_row = dict(row)
        public_row['display_name'] = _public_display_name(row.get('name'))
        if telemetry is not None:
            roster.append(radial._privacy_row(
                public_row, telemetry, ride_id, now,
                STALE_AFTER_MINUTES, radial.ROSTER_DISTANCE_UNIT))
        else:
            roster.append(radial._base_roster_row(
                public_row, ride_id, now,
                STALE_AFTER_MINUTES, radial.ROSTER_DISTANCE_UNIT))

    roster.sort(key=lambda item: (
        item.get('route_position_mi')
        if item.get('route_position_mi') is not None else -1.0), reverse=True)
    for roster_row in roster:
        for block_name in ('next_control', 'finish'):
            block = roster_row.get(block_name)
            if not block or not block.get('eta_iso'):
                continue
            labels = instant_time_labels(
                datetime.fromisoformat(block['eta_iso']), ride)
            block['eta_label'] = f"{labels['event']} {labels['event_zone']}"
            block['eta_pacific_label'] = (
                f"{labels['pacific']} PT" if labels['show_pacific'] else None)
    leader_dist_mi = max((
        ((p.get('telemetry') or {}).get('now') or {}).get('distance_mi')
        for p in positions
        if ((p.get('telemetry') or {}).get('now') or {}).get('distance_mi') is not None
    ), default=None)
    start_utc = None
    if ctx and ctx.get('ride_start_iso'):
        try:
            start_utc = datetime.fromisoformat(ctx['ride_start_iso'])
        except ValueError:
            pass
    upcoming = _upcoming_controls(base_stops, leader_dist_mi, start_utc, ride)
    for control in upcoming:
        if control.get('eta_iso'):
            labels = instant_time_labels(datetime.fromisoformat(control['eta_iso']), ride)
            control['eta_label'] = f"{labels['event']} {labels['event_zone']}"
            control['eta_pacific_label'] = (
                f"{labels['pacific']} PT" if labels['show_pacific'] else None)

    common = {
        'ride_id': ride_id,
        'server_time': now.isoformat(),
        'stale_after_minutes': STALE_AFTER_MINUTES,
        'chart_data': ctx.get('chart_data') if ctx else None,
    }
    payload = {
        'version': 1,
        'mobile': dict(common, positions=positions,
                       elevation_profile=ctx.get('elevation_profile') if ctx else None,
                       upcoming_controls=upcoming,
                       plan_snapshot=_mobile_live_plan_snapshot(ride_id)),
        'public': dict(common, roster=roster, poll_seconds=RADIAL_POLL_SECONDS),
    }
    # Flask's JSON provider normalizes any Decimal/date values before psycopg
    # serializes the JSONB document.
    payload = current_app.json.loads(current_app.json.dumps(payload))
    upsert_live_telemetry_snapshot(
        ride_id, payload, source_recorded_at=source_recorded_at)
    return payload


def _shared_live_snapshot(ride_id):
    """Read the cross-instance base snapshot; fail open on pre-migration DBs."""
    try:
        row = get_live_telemetry_snapshot(ride_id)
        return row if row and row.get('payload') else None
    except Exception:
        current_app.logger.warning(
            'live snapshot unavailable for ride %s; using direct compute',
            ride_id, exc_info=True)
        return None


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

    # The base plan is precomputed by the Garmin poller and shared across every
    # Vercel instance and both clients. Viewer-specific/custom plan lenses retain
    # the direct path below because they may contain private schedules.
    requested_plan = request.args.get('plan_id') or PLAN_BASE
    if requested_plan == PLAN_BASE:
        snapshot = _shared_live_snapshot(ride_id)
        if snapshot:
            payload = dict(snapshot['payload']['mobile'])
            ctx = _ride_live_context(ride_id)
            options, _allowed = _available_plans(
                ctx.get('base_plan_id') if ctx else None, rider_id)
            payload['plans'] = options
            payload['selected_plan_id'] = PLAN_BASE
            payload['snapshot_computed_at'] = snapshot['computed_at'].isoformat()
            payload['snapshot_source_recorded_at'] = (
                snapshot['source_recorded_at'].isoformat()
                if snapshot.get('source_recorded_at') else None)
            response = jsonify(payload)
            response.headers['Cache-Control'] = 'private, no-store'
            return response

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

    try:
        schedule_ride = get_ride_by_id(ride_id)
    except Exception:  # noqa: BLE001 — timezone enrichment is optional
        schedule_ride = None
    schedule_ride = schedule_ride or {
        'timezone': (ctx or {}).get('event_timezone'),
    }
    positions = []
    for row in rows:
        recorded_at = row['recorded_at']
        # recorded_at is timestamptz (tz-aware); guard naive values just in case.
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        minutes_ago = max(0, int((now - recorded_at).total_seconds() // 60))
        status = row['status']

        history = get_positions_for_rider_since(
            row['rider_id'], _telemetry_history_since(ctx, now), ride_id=ride_id)
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
            # The web detail panel shows event-local ETA with Pacific underneath
            # for out-of-state rides. Keep the authenticated native response on the
            # same clock model rather than forcing riders to mentally convert it.
            for block_name in ('next_control', 'finish'):
                block = (telemetry or {}).get(block_name)
                if not block or not block.get('eta_iso'):
                    continue
                eta_dt = datetime.fromisoformat(block['eta_iso'])
                labels = instant_time_labels(eta_dt, schedule_ride)
                block['eta_label'] = f"{labels['event']} {labels['event_zone']}"
                block['eta_pacific_label'] = (
                    f"{labels['pacific']} PT" if labels['show_pacific'] else None)
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

    # Match the web roster: a Going rider remains visible even before they share
    # a location.  Native viewers can distinguish "not sharing" from an empty or
    # broken ride instead of assuming that rider was removed from the event.
    sharing_ids = {position['rider_id'] for position in positions}
    try:
        for rider in get_going_riders_for_ride(ride_id) or []:
            if rider['rider_id'] in sharing_ids:
                continue
            positions.append({
                'rider_id': rider['rider_id'],
                'name': (rider.get('name') or '').strip(),
                'lat': None,
                'lng': None,
                'status': rider.get('status') or RideStatus.GOING.value,
                'color': STATUS_COLORS.get(RideStatus.GOING.value, DEFAULT_COLOR),
                'plan_color': PLAN_UNKNOWN_COLOR,
                'recorded_at': None,
                'minutes_ago': None,
                'stale': False,
                'source': None,
                'telemetry': None,
                'trail': None,
                'not_sharing': True,
            })
    except Exception:  # noqa: BLE001 — Going roster is additive and fail-soft
        current_app.logger.warning(
            'live: mobile Going roster failed for ride %s', ride_id, exc_info=True)

    # Leader (furthest-along on-route rider) drives the shared upcoming-controls list.
    leader_dist_mi = None
    for p in positions:
        t = p.get('telemetry') or {}
        d = (t.get('now') or {}).get('distance_mi')
        if d is not None and (leader_dist_mi is None or d > leader_dist_mi):
            leader_dist_mi = d
    upcoming_controls = _upcoming_controls(
        list_stops, leader_dist_mi, start_utc, schedule_ride)
    for control in upcoming_controls:
        if not control.get('eta_iso'):
            continue
        try:
            labels = instant_time_labels(
                datetime.fromisoformat(control['eta_iso']), schedule_ride)
            control['eta_label'] = f"{labels['event']} {labels['event_zone']}"
            control['eta_pacific_label'] = (
                f"{labels['pacific']} PT" if labels['show_pacific'] else None)
        except (TypeError, ValueError):
            pass

    return jsonify({
        'ride_id': ride_id,
        'positions': positions,
        'stale_after_minutes': STALE_AFTER_MINUTES,
        'server_time': now.isoformat(),
        # Route-ahead weather-style chart series (elevation / headwind / temperature),
        # static per ride. Top-level (not per-rider): each rider's current position is
        # marked from their telemetry.now.distance_mi. Null when the ride has no route.
        'chart_data': ctx.get('chart_data') if ctx else None,
        'elevation_profile': ctx.get('elevation_profile') if ctx else None,
        # Plan selector (item 1): the options the viewer may pick (base + allowed
        # custom plans + 'own'; base only for a single-plan ride) and the plan actually
        # APPLIED — 'base', 'own', or an int id (a rejected id echoes as 'base').
        'plans': plan_options,
        'selected_plan_id': applied_plan_id,
        # Shared upcoming controls of the applied plan with club-local ETAs (item 2).
        'upcoming_controls': upcoming_controls,
        # Compact, cache-backed version of the web live page's active-day card.
        # Native users see today's distance/climb/riding/stops/bank and planned
        # controls without opening a second screen; the full plan remains linked.
        'plan_snapshot': _mobile_live_plan_snapshot(ride_id),
    })


# Unified live poll cadence for the shared Radial view (TA + BrevetHub both 30s);
# a rider who stops sharing disappears within one poll (≤ 30s).
RADIAL_POLL_SECONDS = 30


def _public_display_name(full_name):
    """A privacy-reduced public name: first name + last initial ("Alice S."), or the
    single name alone, or "Rider" when unknown. Never exposes a full surname / email
    on the world-viewable roster."""
    parts = [p for p in (full_name or '').split() if p]
    if not parts:
        return 'Rider'
    if len(parts) == 1:
        return parts[0]
    return '{} {}.'.format(parts[0], parts[-1][0].upper())


def _live_roster_cache_key(ride_id, requested_plan, access_scope):
    """Bounded cache key for a composed live roster response.

    ``requested_plan`` is attacker-controlled query text, so hash it instead of
    placing it directly in the SimpleCache key. Access scope is public only for
    a truly anonymous public-live request; member and invite responses never
    share that cache entry.
    """
    plan_digest = hashlib.sha256(
        str(requested_plan or 'base').encode('utf-8')).hexdigest()[:16]
    return f'live-roster:v1:{ride_id}:{access_scope}:{plan_digest}'


def _live_roster_response(payload, *, public_cache):
    response = jsonify(payload)
    response.headers['Cache-Control'] = (
        LIVE_ROSTER_PUBLIC_CACHE_CONTROL
        if public_cache else LIVE_ROSTER_PRIVATE_CACHE_CONTROL)
    return response


@live_bp.route('/ride/<int:ride_id>/live/roster.json')
def ride_live_roster(ride_id):
    """PUBLIC, PII-safe roster poll for the shared Radial live view.

    Reachable by a guest when the ride owner has opted the ride public
    (ride.is_public_live), by any logged-in member, or by a guest holding a valid
    invite code for this ride. Returns ONLY a privacy-reduced display_name (first
    name + last initial) + coarse position + derived stats + an opaque key — NEVER
    rider_id / email / google_id (the shared build_radial_roster strips them).
    Opted-in riders only (get_latest_positions_for_ride enforces enabled + per-ride
    attach), so a rider who stops sharing disappears within one poll. Fail-soft —
    never 500s the poll."""
    ride = get_ride_by_id(ride_id)
    if not ride:
        abort(404)
    is_member = bool(session.get('rider_id'))
    is_public_live = bool(ride.get('is_public_live'))
    is_guest_invite = (not is_member) and (_guest_ride_id() == ride_id)
    if not (is_public_live or is_member or is_guest_invite):
        abort(404)

    # Only a truly anonymous request may use the shared CDN/public entry. A
    # signed-in member can see their own plan option, while an invite establishes
    # private access through a session cookie; both therefore get scoped
    # in-process keys and explicit no-store browser/CDN semantics.
    public_cache = is_public_live and not is_member and not is_guest_invite
    if public_cache:
        access_scope = 'public'
    elif is_member:
        access_scope = f'member-{session.get("rider_id")}'
    else:
        access_scope = f'invite-{ride_id}'
    requested_plan = request.args.get('plan_id') or 'base'
    response_cache_key = _live_roster_cache_key(
        ride_id, requested_plan, access_scope)
    cached_payload = cache.get(response_cache_key)
    if cached_payload is not None:
        return _live_roster_response(cached_payload, public_cache=public_cache)

    if requested_plan == PLAN_BASE:
        snapshot = _shared_live_snapshot(ride_id)
        if snapshot:
            payload = dict(snapshot['payload']['public'])
            ctx = _ride_live_context(ride_id)
            options, _allowed = _available_plans(
                ctx.get('base_plan_id') if ctx else None,
                session.get('rider_id'))
            payload['plans'] = options
            payload['selected_plan_id'] = PLAN_BASE
            payload['snapshot_computed_at'] = snapshot['computed_at'].isoformat()
            payload['snapshot_source_recorded_at'] = (
                snapshot['source_recorded_at'].isoformat()
                if snapshot.get('source_recorded_at') else None)
            cache.set(response_cache_key, payload, timeout=LIVE_ROSTER_TTL)
            return _live_roster_response(payload, public_cache=public_cache)

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=DISPLAY_WINDOW_HOURS)
    try:
        rows = get_latest_positions_for_ride(ride_id, since)
    except Exception:  # noqa: BLE001 — never 500 the public poll
        current_app.logger.exception('live roster: positions load failed for ride %s', ride_id)
        rows = []
    try:
        sharing_ids = {row['rider_id'] for row in rows}
        for rider in get_going_riders_for_ride(ride_id) or []:
            if rider['rider_id'] not in sharing_ids:
                rows.append(dict(rider, lat=None, lng=None, recorded_at=None,
                                 source=None, speed=None, heart_rate=None,
                                 power=None, cadence=None))
    except Exception:  # noqa: BLE001 — Going roster is best-effort on old schemas
        current_app.logger.exception(
            'live roster: Going riders load failed for ride %s', ride_id)
    for row in rows:
        # Feed the builder a privacy-reduced name; it never reads a member's email.
        row['display_name'] = _public_display_name(row.get('name'))

    ctx = _ride_live_context(ride_id)

    # Plan selector (item 1): resolve the requested plan STRICTLY from the allow-set,
    # exactly like /api/live/positions — a guest gets base + public plans only, the
    # 'own' lens is members-only, and a rejected id falls back to base (never a leak).
    base_plan_id = ctx.get('base_plan_id') if ctx else None
    plan_options, allowed_custom_ids = _available_plans(base_plan_id, session.get('rider_id'))
    applied_plan_id, override_stops = _selected_plan_stops(
        request.args.get('plan_id'), ctx, allowed_custom_ids, is_member=is_member)

    history_since = _telemetry_history_since(ctx, now)
    history_by = {}
    for row in rows:
        try:
            history_by[row['rider_id']] = get_positions_for_rider_since(
                row['rider_id'], history_since, ride_id=ride_id)
        except Exception:  # noqa: BLE001 — history is best-effort; base row instead
            current_app.logger.exception(
                'live roster: history load failed for rider %s on ride %s',
                row['rider_id'], ride_id)
            history_by[row['rider_id']] = []

    # A selected named plan (base or a custom) grades EVERY rider on one schedule
    # (override_stops); the 'own' lens keeps each rider on their own custom plan
    # (base fallback per rider) — mirrors /api/live/positions so they can't diverge.
    if override_stops is not None:
        plan_stops_by_rider = {row['rider_id']: override_stops for row in rows}
    elif ctx:
        plan_stops_by_rider = {row['rider_id']: _rider_plan_stops(ctx, row['rider_id'])
                               for row in rows}
    else:
        plan_stops_by_rider = None

    roster = radial.build_radial_roster(
        rows, ctx, now, history_by, plan_stops_by_rider=plan_stops_by_rider,
        ride_id=ride_id, anchor='ride_start', tz=CLUB_TZ,
        min_history=1, stateless_fallback=True,
        stale_after_minutes=STALE_AFTER_MINUTES,
        telemetry_builder=_rider_telemetry)

    # The shared composer carries one primary ETA label. Team Asha adds the
    # Pacific secondary clock for out-of-state events after privacy shaping.
    for roster_row in roster:
        for block_name in ('next_control', 'finish'):
            block = roster_row.get(block_name)
            if not block or not block.get('eta_iso'):
                continue
            try:
                eta_dt = datetime.fromisoformat(block['eta_iso'])
                labels = instant_time_labels(eta_dt, ride)
                block['eta_label'] = f"{labels['event']} {labels['event_zone']}"
                block['eta_pacific_label'] = (
                    f"{labels['pacific']} PT" if labels['show_pacific'] else None)
            except (TypeError, ValueError):
                pass

    payload = {
        'ride_id': ride_id,
        'roster': roster,
        'server_time': now.isoformat(),
        'stale_after_minutes': STALE_AFTER_MINUTES,
        'poll_seconds': RADIAL_POLL_SECONDS,
        'plans': plan_options,
        'selected_plan_id': applied_plan_id,
        # Static, cache-backed route conditions. The client builds these charts
        # once and only moves rider markers on subsequent position polls.
        'chart_data': ctx.get('chart_data') if ctx else None,
    }
    cache.set(response_cache_key, payload, timeout=LIVE_ROSTER_TTL)
    return _live_roster_response(payload, public_cache=public_cache)


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


@live_bp.route('/api/me/followed-live-rides', methods=['GET'])
@token_or_session_required
def api_followed_live_rides():
    """Account-level live follows shared by native and desktop clients."""
    if not g.rider_id:
        return jsonify({'error': 'Complete your profile to follow rides'}), 403
    return jsonify({'ride_ids': get_followed_live_ride_ids(g.rider_id)})


@live_bp.route('/api/me/followed-live-rides/<int:ride_id>', methods=['PUT'])
@token_or_session_required
def api_followed_live_ride(ride_id):
    """Follow/unfollow a ride without changing the rider's signup state."""
    if not g.rider_id:
        return jsonify({'error': 'Complete your profile to follow rides'}), 403
    if not get_ride_by_id(ride_id):
        return jsonify({'error': 'Ride not found'}), 404
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get('followed'), bool):
        return jsonify({'error': 'followed must be true or false'}), 400
    ride_ids = set_followed_live_ride(g.rider_id, ride_id, body['followed'])
    cache.clear()
    return jsonify({'success': True, 'ride_ids': ride_ids})


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
    from models import (get_all_upcoming_events,
                        get_rider_signup_statuses_batch)
    rides = get_all_upcoming_events(include_active=True)
    ride_ids = [r['id'] for r in rides if r.get('id')]
    statuses = get_rider_signup_statuses_batch(g.rider_id, ride_ids)
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
        'signup_status': (statuses.get(r['id']) or {}).get('status'),
        'is_live': bool(r.get('is_live')),
    } for r in rides]
    return jsonify({'rides': out})


@live_bp.route('/api/calendar/<int:ride_id>/status', methods=['POST'])
@token_or_session_required
def api_calendar_status(ride_id):
    """Set the signed-in rider's mobile calendar intent.

    The native app deliberately exposes the two clear choices requested by the
    product: GOING and not going.  Identity always comes from the bearer token
    or web session; a client cannot change another rider's signup.
    """
    if not g.rider_id:
        return jsonify({'error': 'Complete your profile to update rides'}), 403

    from models import get_ride_by_id, signup_rider, remove_signup

    if not get_ride_by_id(ride_id):
        return jsonify({'error': 'Ride not found'}), 404

    body = request.get_json(silent=True) or {}
    status = str(body.get('status') or '').upper()
    if status == RideStatus.GOING.value:
        success = signup_rider(g.rider_id, ride_id)
        result_status = RideStatus.GOING.value
    elif status in {'NONE', 'NOT_GOING'}:
        success = remove_signup(g.rider_id, ride_id)
        result_status = None
    else:
        return jsonify({'error': 'status must be GOING or NONE'}), 400

    if not success:
        return jsonify({'error': 'Could not update ride status'}), 400
    cache.clear()
    return jsonify({'success': True, 'status': result_status})


@live_bp.route('/api/me/profile')
@token_or_session_required
def api_mobile_profile():
    """Small native profile contract backed by the same web profile models."""
    if not g.rider_id:
        return jsonify({'error': 'Complete your profile to view it'}), 403

    from models import (get_rider_by_id, get_rider_career_stats,
                        get_rider_total_srs)
    rider = get_rider_by_id(g.rider_id)
    if not rider:
        return jsonify({'error': 'Rider not found'}), 404
    career = get_rider_career_stats(g.rider_id)
    return jsonify({
        'rider': {
            'id': rider['id'],
            'rusa_id': rider.get('rusa_id'),
            'first_name': rider.get('first_name'),
            'last_name': rider.get('last_name'),
        },
        'career': {
            'rides': career.get('total_rides') or 0,
            'distance_km': round(career.get('total_kms') or 0),
            'super_randonneur': get_rider_total_srs(g.rider_id) or 0,
        },
    })


@live_bp.route('/api/riders')
@token_or_session_required
def api_public_riders():
    """Public randonneuring directory for native clients.

    Deliberately uses brevet/permanent participation only. Provider workouts,
    health metrics, email, and connection state never enter this contract.
    """
    from models import (get_all_riders_with_career_stats, get_all_seasons,
                        get_current_season, get_season_by_name)
    from shared.rider_directory_view import public_rider_row

    seasons = list(get_all_seasons() or [])
    season_name = (request.args.get('season') or '').strip()
    season = get_season_by_name(season_name) if season_name else get_current_season()
    if season_name and not season:
        return jsonify({'error': 'Season not found'}), 404
    rows = get_all_riders_with_career_stats(
        current_season_id=season['id'] if season else None)
    riders = []
    for record in rows:
        row = public_rider_row(dict(record))
        riders.append({
            'id': row['id'], 'rusa_id': row['rusa_id'],
            'first_name': row['first_name'], 'last_name': row['last_name'],
            'display_name': row['display_name'],
            'total_rides': row['total_rides'],
            'total_km': round(float(row['total_km'] or 0)),
            'season_rides': row['season_rides'],
            'season_km': round(float(row['season_kms'] or 0)),
            'eddington_miles': record.get('eddington_number_miles') or None,
            'sr_progress': [distance for distance, key in (
                (200, 'sr_200'), (300, 'sr_300'), (400, 'sr_400'), (600, 'sr_600'))
                if (record.get(key) or 0) > 0],
        })
    return jsonify({
        'riders': riders,
        'season': ({'id': season['id'], 'name': season['name']} if season else None),
        'seasons': [{'id': s['id'], 'name': s['name'],
                     'is_current': bool(s.get('is_current'))} for s in seasons],
    })


@live_bp.route('/api/me/training-log')
@token_or_session_required
def api_training_log():
    """Private month of synced Strava activities for the native training log."""
    if not g.rider_id:
        return jsonify({'error': 'Complete your profile to view training'}), 403
    month = (request.args.get('month') or '').strip()
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        return jsonify({'error': 'month must be YYYY-MM'}), 400
    try:
        start = date.fromisoformat(f'{month}-01')
        end = date(start.year + (1 if start.month == 12 else 0),
                   1 if start.month == 12 else start.month + 1, 1)
    except ValueError:
        return jsonify({'error': 'Invalid month'}), 400

    from models import get_strava_connection, get_strava_activities_between
    connected = bool(get_strava_connection(g.rider_id))
    rows = get_strava_activities_between(g.rider_id, start, end) if connected else []
    activities = []
    for row in rows:
        local = row.get('start_date_local')
        activities.append({
            'id': str(row['strava_activity_id']),
            'name': row.get('name') or row.get('activity_type') or 'Activity',
            'type': row.get('activity_type') or 'Workout',
            'start_local': local.isoformat() if local else None,
            'date': str(local.date()) if local else None,
            'distance_mi': round(float(row.get('distance') or 0) / 1609.344, 1),
            'moving_minutes': round(float(row.get('moving_time') or 0) / 60),
            'elapsed_minutes': round(float(row.get('elapsed_time') or 0) / 60),
            'elevation_ft': round(float(row.get('total_elevation_gain') or 0) * 3.28084),
            'average_hr': (round(float(row['average_heartrate']))
                           if row.get('average_heartrate') is not None else None),
            'average_watts': (round(float(row['average_watts']))
                              if row.get('average_watts') is not None else None),
            'suffer_score': row.get('suffer_score'),
            'calories': row.get('calories'),
            'trainer': bool(row.get('trainer')),
            'commute': bool(row.get('commute')),
            'url': row.get('strava_url'),
        })
    return jsonify({
        'month': month, 'connected': connected, 'activities': activities,
        'attribution': 'Powered by Strava',
    })


@live_bp.route('/api/riders/<int:rusa_id>')
@token_or_session_required
def api_public_rider(rusa_id):
    """One rider's public brevet history; never returns private provider data."""
    from models import (get_rider_by_rusa, get_all_seasons,
                        get_current_season, get_rider_participation,
                        get_rider_season_stats, detect_sr_for_rider_season,
                        get_rider_total_srs, detect_r12_awards)
    import html as _html

    rider = get_rider_by_rusa(rusa_id)
    if not rider:
        return jsonify({'error': 'Rider not found'}), 404
    current = get_current_season()
    season_data = []
    career_rides = 0
    career_km = 0
    for season in get_all_seasons() or []:
        participation = list(get_rider_participation(rider['id'], season['id']) or [])
        if not participation:
            continue
        stats = get_rider_season_stats(rider['id'], season['id'])
        is_current = bool(current and current['id'] == season['id'])
        rides = [{
            'id': row['id'],
            'name': _html.unescape(str(row.get('name') or '')).replace('\xa0', ' ').strip(),
            'date': str(row['date']) if row.get('date') else None,
            'distance_km': row.get('distance_km'),
            'status': row.get('status'),
            'ride_type': row.get('ride_type'),
            'finish_time': str(row['finish_time']) if row.get('finish_time') else None,
        } for row in participation]
        season_data.append({
            'id': season['id'], 'name': season['name'], 'is_current': is_current,
            'rides': stats.get('rides') or 0,
            'distance_km': round(float(stats.get('kms') or 0)),
            'sr_count': detect_sr_for_rider_season(
                rider['id'], season['id'], date_filter=is_current),
            'history': rides,
        })
        career_rides += stats.get('rides') or 0
        career_km += stats.get('kms') or 0
    return jsonify({
        'rider': {
            'id': rider['id'], 'rusa_id': rider.get('rusa_id'),
            'first_name': rider.get('first_name'), 'last_name': rider.get('last_name'),
        },
        'career': {
            'rides': career_rides, 'distance_km': round(float(career_km)),
            'super_randonneur': get_rider_total_srs(rider['id']) or 0,
            'r12': len(detect_r12_awards(rider['id']) or []),
        },
        'seasons': season_data,
    })


@cache.memoize(CACHE_TIMEOUT)
def _ride_route_polyline_cached(ride_id, day_key):
    """Cached [[lng,lat],...] RWGPS route line for a ride (static per ride).
    Returns None when the ride is missing or has no resolvable route."""
    ride = get_ride_by_id(ride_id)
    if not ride:
        return None
    return _radial_polyline(_radial_overview_track(ride))


def _ride_route_polyline(ride_id):
    ride = get_ride_by_id(ride_id)
    if not ride:
        return None
    day_key = int(_active_plan_leg(ride).get('day_number') or 1)
    return _ride_route_polyline_cached(ride_id, day_key)


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


# Stop-marker colours for the mobile plan-page elevation overlay. A local copy (not
# an import of the web route's map) so the two surfaces stay decoupled; kept in step
# with the rpv2 STOP_TYPES colours so a control's dot matches its itinerary badge.
_PLAN_STOP_MARKER_COLORS = {
    'start': '#16a34a', 'control': '#1d4ed8', 'rest': '#ea580c',
    'waypoint': '#64748b', 'finish': '#dc2626',
}


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


def _compute_base_timing(raw_stops, cutoff_hours, total_mi, event_distance_km=None):
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
            bookend = control_close_time_minutes(
                dist_mi, total_mi, cutoff_hours,
                event_distance_km=event_distance_km,
            )
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
        raw = _compute_base_timing(
            get_ride_plan_stops(plan['id']), cutoff_hours, total_mi,
            plan.get('distance_km') or ride.get('distance_km'))

    if not raw:
        return jsonify({'available': False, 'reason': 'no_stops',
                        'message': 'This ride plan has no stops yet.'})

    stops = [_emit_plan_stop(d, base_dt) for d in raw]

    # RWGPS route id + ride date, shared by the wind read and the elevation-profile
    # read below. BOTH are cache-only (stored forecast / cron-warmed track) — never a
    # live RWGPS or Open-Meteo fetch on the mobile plan path (TA-237).
    route_id = extract_rwgps_route_id(
        plan.get('rwgps_url_team') or plan.get('rwgps_url')
        or ride.get('rwgps_url_team') or ride.get('rwgps_url'))
    ride_date = ride.get('date')
    if isinstance(ride_date, str):
        ride_date = _date.fromisoformat(ride_date)

    # Best-effort per-stop wind + temperature (same service as the plan web page),
    # READ from the pre-fetched forecast for this route + ride date (no live Open-Meteo
    # on the mobile plan path — TA-237).
    try:
        if route_id and ride_date:
            wind_stops = [{'distance_miles': st['distance_mi'],
                           'arrival_time_min': st['arrival_time_min']} for st in stops]
            winds = fetch_stop_wind(wind_stops, route_id, ride_date, start_str)
            for st, w in zip(stops, winds or []):
                if w:
                    st['wind_speed_mph'] = w.get('wind_speed_mph')
                    st['wind_label'] = w.get('label') or w.get('wind_type')
                    st['wind_direction_deg'] = w.get('wind_direction_deg')
                    st['temperature_f'] = w.get('temperature_f')
    except Exception:
        current_app.logger.warning('ride plan %s: stop wind unavailable', plan_slug)

    # Per-pace-variant itineraries (comfort / standard / push) so the mobile client can
    # swap the visible schedule + reposition the elevation overlay on pick — client-side,
    # no refetch — using the SAME shared math as the web rpv2 pace cards. No `base_stops`
    # is passed, so the ids are always comfort/standard/push: a stable mobile contract
    # (the web's custom-plan rebaseline to team/yours/extra is deliberately not mirrored).
    # Fail-soft: any error → empty map/meta, logged, and the base `stops` still serve.
    pace_stops_map = {}
    pace_cards_meta = []
    try:
        paces = compute_pace_strategies(raw, plan, start_str, cutoff_hours)
        pace_stops_map = {p['id']: p['stops'] for p in paces}
        pace_cards_meta = [{k: v for k, v in p.items() if k != 'stops'} for p in paces]
    except Exception:
        current_app.logger.warning('ride plan %s: pace strategies unavailable', plan_slug)

    # Gradient elevation profile from the cron-warmed track (route_weather_cache,
    # migration 052) — read from cache ONLY, never a live RWGPS fetch on the request
    # path (TA-237), exactly like the web ride_plan_detail render. Control/break markers
    # are placed from the STANDARD pace stops (which carry `cumul_mi`), not the mobile
    # `stops` array (which uses `distance_mi`). Fail-soft: cold cache / no elevation /
    # any error → {'available': False} so old and new clients both keep working.
    elevation_profile = {'available': False}
    try:
        track = get_route_elevation_track(route_id) if route_id else None
        elevation_profile = radial.build_elevation_profile(track or [])
        if elevation_profile.get('available'):
            elevation_profile['markers'] = radial.overlay_stop_markers(
                elevation_profile, pace_stops_map.get('standard') or [],
                _PLAN_STOP_MARKER_COLORS)
    except Exception:
        current_app.logger.warning('ride plan %s: elevation profile unavailable', plan_slug)
        elevation_profile = {'available': False}

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
        # Additive (PR #535 mobile parity with web PR #534): old clients ignore these.
        # Gradient elevation profile ({available:false} on cache miss) + per-pace stops.
        'elevation_profile': elevation_profile,
        'pace_stops_map': pace_stops_map,
        'pace_cards_meta': pace_cards_meta,
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

    from models import (get_all_seasons, get_current_season, get_rider_season_stats,
                        get_rider_season_elevation_ft, get_rider_career_stats,
                        detect_sr_for_rider_season, get_sr_distances_done,
                        get_sr_counts_by_tier, get_rider_finished_rides_for_season,
                        get_r12_current_streak, get_strava_connection)
    import html as _html

    rider_id = g.rider_id
    seasons = list(get_all_seasons() or [])
    requested_season_id = request.args.get('season_id', type=int)
    season = None
    if requested_season_id is not None:
        season = next((s for s in seasons if s.get('id') == requested_season_id), None)
        if season is None:
            return jsonify({'error': 'Season not found'}), 404
    if season is None:
        season = get_current_season()
    if not season:
        return jsonify({'error': 'No current season set'}), 404

    season_id = season['id']

    # Season totals (current season uses date-filtered SR, mirroring the web profile).
    stats = get_rider_season_stats(rider_id, season_id)
    elevation_ft = get_rider_season_elevation_ft(rider_id, season_id)
    date_filter = bool(season.get('is_current'))
    sr_count = detect_sr_for_rider_season(rider_id, season_id, date_filter=date_filter)
    distances_done = get_sr_distances_done(rider_id, season_id, date_filter=date_filter)
    sr_counts = get_sr_counts_by_tier(rider_id, season_id, date_filter=date_filter)

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
        'season': {
            'id': season.get('id'), 'name': season.get('name'),
            'is_current': bool(season.get('is_current')),
        },
        'seasons': [{
            'id': s.get('id'), 'name': s.get('name'),
            'is_current': bool(s.get('is_current')),
        } for s in seasons],
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
