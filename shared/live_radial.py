"""Radial-inspired live-view builders shared by Team Asha and BrevetHub.

The compact "live" view for a brevet in progress — a progress-sorted rider table,
rider markers on ONE map, and rider markers on a colour-graded altitude profile —
is assembled from three pure builders here so BOTH apps render from a single
implementation and can never fork:

- ``compose_rider_telemetry`` — the RICH per-rider telemetry composition (distance
  done / remaining, ascent split, plan delta, next control + ETA + required pace,
  finish ETA, OTL margin). It was previously duplicated as a private
  ``_rider_telemetry`` inside each app's ``routes/live.py``; both now call this one
  function. Wind / weather is the only field this omits — it depends on each app's
  weather layer — so the caller layers it back via the optional ``wind_labeler``
  hook (Team Asha does; BrevetHub carries no weather context and skips it).
- ``build_radial_roster`` — a progress-sorted, PRIVACY-SHAPED roster row per rider:
  a display name + position + derived stats + an opaque per-view ``key`` (a hash of
  rider id + ride id) for client marker diffing. It NEVER emits ``rider_id``,
  ``email`` or ``google_id`` — it is the payload the public, guest-reachable
  ``roster.json`` endpoints return.
- ``build_elevation_profile`` — a server-computed, Tour-de-France-style ALTITUDE
  profile (SVG geometry, per-segment gradient colour buckets, mile + elevation
  ticks, and a linear distance→pixel mapping so the template can place rider dots).
  Server-computed so the shared template stays framework-builtin-only (BrevetHub
  forbids the custom ``commafy`` / ``clean_name`` Jinja filters).

Isolation contract (guarded by test_shared_isolation): stdlib only, plus the sibling
``shared.live_telemetry`` engine — nothing from ``services`` / ``models`` / ``routes``
and never the web framework's app/request globals. BrevetHub ships a byte-identical
vendored copy under ``brevethub/shared/``.
"""
import hashlib
import math
from datetime import datetime, timedelta, timezone

from . import live_telemetry as tlm

M_TO_MI = 1 / 1609.344
MS_TO_MPH = 2.236936

# Distance unit shown in the roster table. Brevet plans are native miles (the
# BrevetHub per-stop plan is miles after PR #516); an owner who prefers km flips
# this one constant (or passes ``dist_unit='km'`` per call).
ROSTER_DISTANCE_UNIT = 'mi'

# Map-marker colour by plan timing: ahead / on plan = green, behind = red, and
# grey when pace can't be graded (off route / no plan). Kept in step with each
# app's map dot colour so the marker and the table badge always agree.
MARKER_AHEAD_COLOR = '#16a34a'
MARKER_BEHIND_COLOR = '#dc2626'
MARKER_UNKNOWN_COLOR = '#9ca3af'


def _as_utc(dt):
    """Treat a naive datetime as UTC so it compares with tz-aware DB timestamps."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _num_or_none(value, cast):
    """Best-effort cast for JSON output; None on failure (NUMERIC → native)."""
    if value is None:
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Rich per-rider telemetry composition (the shared assembler).
# --------------------------------------------------------------------------- #
def compose_rider_telemetry(row, ctx, now, history, *, plan_stops=None, start=None,
                            tz=None, wind_labeler=None, min_history=1,
                            stateless_fallback=True, rebase_from_first_fix=True):
    """Assemble one rider's telemetry block from their position history + the
    per-ride context. Pure — every heavy input (route geometry, plan stops) is
    passed in; no I/O, no framework globals.

    ``start`` is the tz-aware anchor the elapsed / moving-time math is measured from
    (Team Asha: the event start; BrevetHub: the rider's first fix). ``tz`` (a
    ``ZoneInfo``) formats the human ETA labels; None leaves them unset (ISO only).
    ``wind_labeler(dist_m)`` optionally returns the four head/tail-wind fields the
    caller layers on (Team Asha's weather layer); None skips them. ``min_history``
    is the minimum fixes required before route projection is attempted (BrevetHub
    needs 2; Team Asha 1). ``stateless_fallback`` matches a single latest-point
    projection when the trajectory walk yields nothing (Team Asha behaviour).

    Route-relative fields appear only when the rider is ON the route; plan-relative
    fields only when a usable plan (>= 2 stops) resolved. Everything degrades to the
    source-agnostic ``now`` block otherwise — never raises for a missing input.
    """
    elapsed_min = None
    if start is not None and start <= now:
        elapsed_min = round((now - start).total_seconds() / 60)

    # Moving / stopped are measured where tracking exists. For a scheduled brevet
    # whose LiveTrack began late, treat the unobserved pre-tracking interval as
    # moving—not stopped—because the rider's absolute course progress proves they
    # were already riding. Only pauses actually observed in LiveTrack count stopped.
    ride_history = history
    if start is not None:
        ride_history = [h for h in history if _as_utc(h['recorded_at']) >= start]
    first_fix = _as_utc(ride_history[0].get('recorded_at')) if ride_history else None
    history_started_late = bool(
        start is not None and first_fix is not None
        and first_fix > start + timedelta(minutes=10))
    moving_min, stopped_min = tlm.moving_stopped(ride_history)
    if elapsed_min is not None:
        if history_started_late:
            moving_min = round(max(0.0, elapsed_min - stopped_min), 1)
        else:
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
        'current_stop_min': (tlm.current_stop_duration_min(ride_history)
                             if tlm.activity_from_speed(speed_ms) == 'paused' else None),
        'elapsed_min': elapsed_min,
        'moving_min': moving_min,
        'stopped_min': stopped_min,
        'stopped_ride_day_min': None,
        'active_day': None,
        'stop_events': [],
        'heart_rate': _num_or_none(row.get('heart_rate'), int),
        'power': _num_or_none(row.get('power'), int),
        'cadence': _num_or_none(row.get('cadence'), int),
    }
    base = {'on_route': None, 'now': now_block, 'remaining': None,
            'next_control': None, 'finish': None, 'time_banked_cutoff_min': None,
            'time_banked_plan_min': None, 'plan': None, 'detailed_after_ride': True}

    # No route → only the source-agnostic 'now' block. Too few history fixes
    # short-circuits ONLY when there is no stateless fallback (BrevetHub, min 2):
    # with a stateless fallback (Team Asha) a rider that has a current fix but no
    # stored trail still projects off that single latest point below, so telemetry
    # appears on the rider's FIRST position rather than waiting for a second poll
    # (this is the pre-promotion inline behaviour the shared builder must preserve).
    if not ctx.get('has_route'):
        return base
    if len(history) < min_history and not stateless_fallback:
        return base

    # One leg-aware trajectory walk yields BOTH the current distance-done and the
    # rider's START position on the route (the seed), matched consistently.
    dist_m, idx, off_by_m, start_dist_m, start_idx = tlm.project_history_to_route(
        ride_history, ctx['track'], with_start=True)
    if dist_m is None:
        if not stateless_fallback:
            return base
        lat, lng = float(row['lat']), float(row['lng'])
        dist_m, idx, off_by_m = tlm.project_to_route(lat, lng, ctx['track'])
        start_dist_m, start_idx = None, 0
    on_route = (dist_m is not None and off_by_m is not None
                and off_by_m <= tlm.ON_ROUTE_MAX_M)
    if not on_route:
        base['on_route'] = False
        return base

    # Informal/permanent tracking may start partway around a loop, in which case
    # distance is relative to the rider's first fix. A scheduled event is anchored
    # at the official route start: LiveTrack can be enabled hours later, but that
    # must not subtract the miles already ridden before sharing began.
    start_offset_m = (
        start_dist_m
        if (rebase_from_first_fix
            and (start_dist_m or 0) >= tlm.START_OFFSET_MIN_M)
        else 0.0
    )
    if not start_offset_m:
        start_idx = 0
    mid_route_start = start_offset_m > 0
    progressed_m = tlm.distance_progressed_m(dist_m, start_offset_m, ctx['total_dist_m'])
    ascent_done, ascent_left = tlm.ascent_progressed_split(
        ctx['cum_ascent_ft'], start_idx, idx, ctx['total_ascent_ft'])

    dist_mi = progressed_m * M_TO_MI
    route_position_mi = dist_m * M_TO_MI
    # A multi-day ride's telemetry track can represent only its active leg. Keep
    # every value in the Remaining card scoped to the whole brevet, just like the
    # overall time limit, by using the plan totals and absolute course position.
    plan_total_mi = ctx.get('plan_total_mi') or ((ctx['total_dist_m'] or 0) * M_TO_MI)
    remaining_mi = max(0.0, plan_total_mi - route_position_mi)
    plan_total_ascent_ft = ctx.get('plan_total_ascent_ft')
    if plan_total_ascent_ft:
        ascent_left = max(0, round(plan_total_ascent_ft - (ascent_done or 0)))
    remaining_m = remaining_mi / M_TO_MI
    tuf = tlm.toughness_remaining(ascent_left, remaining_m)

    now_block['distance_mi'] = round(dist_mi, 1)                  # ridden (odometer)
    now_block['route_position_mi'] = round(route_position_mi, 1)  # absolute (chart marker)
    now_block['grade_pct'] = tlm.grade_at(ctx.get('track'), idx)
    now_block['avg_elapsed_speed_mph'] = (
        round(dist_mi / (elapsed_min / 60.0), 1) if elapsed_min and elapsed_min > 0 else None)
    moving_distance_m = progressed_m
    moving_distance_mi = moving_distance_m * M_TO_MI
    now_block['avg_moving_speed_mph'] = (
        round(moving_distance_mi / (moving_min / 60.0), 1)
        if moving_min and moving_min > 0 else None)
    now_block['ascent_done_ft'] = ascent_done

    # The plan's Day N boundaries are route distances, not civil midnights. Find
    # where the rider crossed the active day's starting distance, then total only
    # the observed stopping intervals from that point onward.
    boundaries = sorted((int(day), float(miles))
                        for day, miles in (ctx.get('day_distance_boundaries') or {}).items())
    active_day, day_start_mi, next_day_start_mi = 1, 0.0, None
    for pos, (day, miles) in enumerate(boundaries):
        if route_position_mi >= miles:
            active_day, day_start_mi = day, miles
            next_day_start_mi = (boundaries[pos + 1][1]
                                 if pos + 1 < len(boundaries) else None)
    day_history = ride_history
    if day_start_mi > 0:
        crossing = None
        for pos, point in enumerate(ride_history):
            projected, _pidx, off = tlm.project_to_route(
                float(point['lat']), float(point['lng']), ctx['track'])
            if (projected is not None and off is not None and off <= tlm.ON_ROUTE_MAX_M
                    and projected * M_TO_MI >= day_start_mi - 0.1):
                crossing = max(0, pos - 1)
                break
        day_history = ride_history[crossing:] if crossing is not None else []
    _day_moving, stopped_ride_day = tlm.moving_stopped(day_history)
    now_block['stopped_ride_day_min'] = stopped_ride_day
    now_block['active_day'] = active_day

    local_tz = tz or timezone.utc
    stop_events = []
    for period in tlm.stationary_periods(ride_history):
        projected, _pidx, off = tlm.project_to_route(
            period['lat'], period['lng'], ctx['track'])
        if projected is None or off is None or off > tlm.ON_ROUTE_MAX_M:
            continue
        event_mi = projected * M_TO_MI
        event_day = 1
        for day, miles in boundaries:
            if event_mi >= miles:
                event_day = day
        start_local = _as_utc(period['start_at']).astimezone(local_tz)
        end_local = _as_utc(period['end_at']).astimezone(local_tz)
        stop_events.append({
            'distance_mi': round(event_mi, 1), 'duration_min': period['duration_min'],
            'day_number': event_day,
            'start_label': start_local.strftime('%-I:%M %p'),
            'end_label': end_local.strftime('%-I:%M %p'),
        })
    now_block['stop_events'] = stop_events

    # Time left = the brevet's overall time limit minus elapsed (e.g. 40h for a 600),
    # clamped at 0. Only when the context carries a time limit (Team Asha does).
    time_left_min = None
    limit_min = ctx.get('time_limit_min')
    if limit_min is not None and elapsed_min is not None:
        time_left_min = max(0, limit_min - elapsed_min)

    active_stops = plan_stops if plan_stops is not None else ctx.get('plan_stops')
    plan_frame = (tlm.rebase_plan_stops(active_stops, start_offset_m * M_TO_MI, plan_total_mi)
                  if mid_route_start else active_stops)

    delta = tlm.plan_delta(dist_mi, elapsed_min, plan_frame)

    remaining_block = {
        'distance_mi': round(remaining_mi, 1),
        'ascent_left_ft': ascent_left,
        'time_left_min': time_left_min,
        'toughness': tuf,
    }

    next_control_block = _next_control_block(dist_mi, plan_frame, start, elapsed_min, tz)
    finish_block = _finish_block(dist_mi, plan_frame, start, elapsed_min, tz)

    # OTL margin (banked vs the ACP cutoff). For a mid-route loop start, distance
    # done spans the whole route, so pro-rate the cutoff against the route total.
    cutoff_total_mi = ctx.get('plan_total_mi')
    if mid_route_start and ctx.get('total_dist_m'):
        cutoff_total_mi = ctx['total_dist_m'] * M_TO_MI
    banked_cutoff = tlm.time_banked_cutoff_min(
        dist_mi, elapsed_min, cutoff_total_mi, ctx.get('plan_cutoff_hours'),
        event_distance_km=ctx.get('plan_distance_km'))

    # Wind / weather is the one field this shared builder can't compute (it needs the
    # caller's weather layer); Team Asha layers it back through the hook, BrevetHub skips.
    if wind_labeler is not None:
        try:
            winds = wind_labeler(dist_m) or {}
        except Exception:  # noqa: BLE001 — wind is advisory; never sink telemetry
            winds = {}
        if 'headwind_done_mph' in winds:
            now_block['headwind_done_mph'] = winds.get('headwind_done_mph')
            now_block['headwind_done_label'] = winds.get('headwind_done_label')
        if 'headwind_ahead_mph' in winds:
            remaining_block['headwind_ahead_mph'] = winds.get('headwind_ahead_mph')
            remaining_block['headwind_ahead_label'] = winds.get('headwind_ahead_label')

    return {
        'on_route': True,
        'now': now_block,
        'next_control': next_control_block,
        'finish': finish_block,
        'remaining': remaining_block,
        'time_banked_cutoff_min': banked_cutoff,
        'time_banked_plan_min': delta,
        'plan': ({'delta_min': delta, 'banked_min': delta,
                  'status': 'ahead' if delta > 2 else ('behind' if delta < -2 else 'on')}
                 if delta is not None else None),
        'detailed_after_ride': True,
    }


def _eta_labels(start, arrival_min, tz):
    """(eta_iso, eta_label) for a plan arrival time, or (None, None). ``tz`` renders
    the human label in that zone; None leaves it unset."""
    if start is None or arrival_min is None:
        return None, None
    eta_dt = start + timedelta(minutes=arrival_min)
    eta_iso = eta_dt.isoformat()
    eta_label = (eta_dt.astimezone(tz).strftime('%I:%M %p').lstrip('0')
                 if tz is not None else None)
    return eta_iso, eta_label


def _next_control_block(dist_mi, plan_frame, start, elapsed_min, tz):
    """The next control ahead (name / distance / ETA / required pace), or None."""
    nc = tlm.next_control(dist_mi, plan_frame)
    if not nc:
        return None
    arrival_min = nc.get('arrival_time_min')
    eta_iso, eta_label = _eta_labels(start, arrival_min, tz)
    req_mph, behind = tlm.required_speed_mph(
        nc.get('dist_to_go_mi'), arrival_min, elapsed_min)
    return {
        'name': nc.get('location'), 'type': nc.get('stop_type'),
        'distance_mi': nc.get('distance_miles'), 'dist_to_go_mi': nc.get('dist_to_go_mi'),
        'arrival_time_min': arrival_min, 'eta_iso': eta_iso, 'eta_label': eta_label,
        'required_mph': req_mph, 'behind': behind,
    }


def _finish_block(dist_mi, plan_frame, start, elapsed_min, tz):
    """The finish (distance-to-go / ETA / required pace), or None."""
    fin = tlm.finish_stop(plan_frame)
    if not fin:
        return None
    fin_arrival = fin.get('arrival_time_min')
    dist_to_finish = round(max(0.0, fin['distance_miles'] - dist_mi), 1)
    fin_req_mph, fin_behind = tlm.required_speed_mph(dist_to_finish, fin_arrival, elapsed_min)
    eta_iso, eta_label = _eta_labels(start, fin_arrival, tz)
    return {
        'name': fin.get('location'), 'type': fin.get('stop_type'),
        'distance_mi': fin.get('distance_miles'), 'dist_to_go_mi': dist_to_finish,
        'arrival_time_min': fin_arrival, 'eta_iso': eta_iso, 'eta_label': eta_label,
        'required_mph': fin_req_mph, 'behind': fin_behind,
    }


# --------------------------------------------------------------------------- #
# Privacy-shaped roster (the public payload — NO rider_id / email / google_id).
# --------------------------------------------------------------------------- #
def roster_key(rider_id, ride_id):
    """Opaque, stable per-view marker key = a truncated hash of rider + ride id.

    Lets the client diff / animate markers across polls WITHOUT ever seeing the
    rider id. It is one-way (a hash) and scoped to the ride, so it reveals nothing
    about the rider and can't be correlated across rides."""
    raw = '{}:{}'.format(rider_id, ride_id if ride_id is not None else '')
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]


def _initials(display_name):
    """Up to two uppercase initials from a display name (for a compact marker)."""
    parts = [p for p in (display_name or '').split() if p]
    if not parts:
        return '?'
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _marker_color(telemetry):
    """Marker colour from plan timing (green ahead/on, red behind, grey unknown)."""
    if telemetry is not None and telemetry.get('on_route') is False:
        return MARKER_UNKNOWN_COLOR
    plan = (telemetry or {}).get('plan')
    if plan and plan.get('status'):
        return MARKER_BEHIND_COLOR if plan['status'] == 'behind' else MARKER_AHEAD_COLOR
    return MARKER_UNKNOWN_COLOR


def _public_next_control(nc):
    """The PII-free subset of a next-control block for the public roster."""
    if not nc:
        return None
    return {
        'name': nc.get('name'), 'type': nc.get('type'),
        'distance_mi': nc.get('distance_mi'), 'dist_to_go_mi': nc.get('dist_to_go_mi'),
        'eta_iso': nc.get('eta_iso'), 'eta_label': nc.get('eta_label'),
        'required_mph': nc.get('required_mph'), 'behind': nc.get('behind'),
    }


def _resolve_anchor(anchor, ctx, history):
    """The tz-aware elapsed anchor for a rider (event start vs first fix)."""
    if anchor == 'ride_start':
        iso = ctx.get('ride_start_iso')
        if iso:
            try:
                start = datetime.fromisoformat(iso)
            except ValueError:
                return None
            return start
        return None
    # 'first_fix' — anchor on the rider's earliest tracked point.
    if history:
        return _as_utc(history[0].get('recorded_at'))
    return None


def _display_name(row):
    """The rider's public display name — the caller MUST pass a real
    ``display_name`` (or a non-email ``name``); this never reads an email."""
    name = (row.get('display_name') or row.get('name') or '').strip()
    return name or 'Rider'


def _base_roster_row(row, ride_id, now, stale_after_minutes, dist_unit):
    """A minimal privacy-shaped row (name + position only) — the fail-soft fallback
    when a rider's telemetry can't be composed. Still carries NO PII identifiers."""
    recorded_at = _as_utc(row.get('recorded_at'))
    minutes_ago = (max(0, int((now - recorded_at).total_seconds() // 60))
                   if recorded_at is not None else None)
    display = _display_name(row)
    return {
        'key': roster_key(row.get('rider_id'), ride_id),
        'display_name': display,
        'initials': _initials(display),
        'lat': _num_or_none(row.get('lat'), float),
        'lng': _num_or_none(row.get('lng'), float),
        'source': row.get('source') or 'beacon',
        'recorded_at': recorded_at.isoformat() if recorded_at is not None else None,
        'minutes_ago': minutes_ago,
        'stale': (minutes_ago is not None and minutes_ago > stale_after_minutes),
        'on_route': None,
        'speed_mph': None,
        'activity': None,
        'current_stop_min': None,
        'dist_mi': None,
        'dist_display': None,
        'dist_unit': dist_unit,
        'route_position_mi': None,
        'elapsed_min': None,
        'moving_min': None,
        'stopped_min': None,
        'stopped_ride_day_min': None,
        'active_day': None,
        'stop_events': [],
        'avg_elapsed_speed_mph': None,
        'avg_moving_speed_mph': None,
        'heart_rate': None,
        'power': None,
        'cadence': None,
        'grade_pct': None,
        'headwind_done_label': None,
        'ascent_done_ft': None,
        'distance_left_mi': None,
        'ascent_left_ft': None,
        'time_left_min': None,
        'toughness': None,
        'headwind_ahead_label': None,
        'plan_status': None,
        'banked_plan_min': None,
        'banked_cutoff_min': None,
        'next_control': None,
        'finish': None,
        'eta_finish_iso': None,
        'eta_finish_label': None,
        'marker_color': MARKER_UNKNOWN_COLOR,
    }


def _privacy_row(row, telemetry, ride_id, now, stale_after_minutes, dist_unit):
    """The full privacy-shaped roster row for a rider with composed telemetry."""
    entry = _base_roster_row(row, ride_id, now, stale_after_minutes, dist_unit)
    nowb = (telemetry or {}).get('now') or {}
    remaining = (telemetry or {}).get('remaining') or {}
    dist_mi = nowb.get('distance_mi')
    entry.update({
        'on_route': telemetry.get('on_route'),
        'speed_mph': nowb.get('speed_mph'),
        # 'paused' | 'walking' | 'cycling' | 'driving' | None — movement classified
        # from ground speed, so the live view can flag a rider who is stopped (but
        # still sharing) distinctly from one who has gone stale (no updates at all).
        'activity': nowb.get('activity'),
        'current_stop_min': nowb.get('current_stop_min'),
        'dist_mi': dist_mi,
        'route_position_mi': nowb.get('route_position_mi'),
        'elapsed_min': nowb.get('elapsed_min'),
        'moving_min': nowb.get('moving_min'),
        'stopped_min': nowb.get('stopped_min'),
        'stopped_ride_day_min': nowb.get('stopped_ride_day_min'),
        'active_day': nowb.get('active_day'),
        'stop_events': nowb.get('stop_events') or [],
        'avg_elapsed_speed_mph': nowb.get('avg_elapsed_speed_mph'),
        'avg_moving_speed_mph': nowb.get('avg_moving_speed_mph'),
        'heart_rate': nowb.get('heart_rate'),
        'power': nowb.get('power'),
        'cadence': nowb.get('cadence'),
        'grade_pct': nowb.get('grade_pct'),
        'headwind_done_label': nowb.get('headwind_done_label'),
        'ascent_done_ft': nowb.get('ascent_done_ft'),
        'distance_left_mi': remaining.get('distance_mi'),
        'ascent_left_ft': remaining.get('ascent_left_ft'),
        'time_left_min': remaining.get('time_left_min'),
        'toughness': remaining.get('toughness'),
        'headwind_ahead_label': remaining.get('headwind_ahead_label'),
        'plan_status': (telemetry.get('plan') or {}).get('status'),
        'banked_plan_min': telemetry.get('time_banked_plan_min'),
        'banked_cutoff_min': telemetry.get('time_banked_cutoff_min'),
        'next_control': _public_next_control(telemetry.get('next_control')),
        'finish': _public_next_control(telemetry.get('finish')),
        'eta_finish_iso': (telemetry.get('finish') or {}).get('eta_iso'),
        'eta_finish_label': (telemetry.get('finish') or {}).get('eta_label'),
        'marker_color': _marker_color(telemetry),
    })
    if dist_mi is not None:
        entry['dist_display'] = (round(dist_mi * 1.609344, 1) if dist_unit == 'km'
                                 else round(dist_mi, 1))
        entry['dist_unit'] = dist_unit
    return entry


def build_radial_roster(rider_rows, ctx, now, history_by_rider, plan_stops_by_rider=None,
                        *, ride_id=None, anchor='first_fix', tz=None, min_history=1,
                        stateless_fallback=True, stale_after_minutes=10,
                        dist_unit=None, telemetry_builder=None):
    """Progress-sorted, privacy-shaped roster for the public live view.

    ``rider_rows`` are the latest position rows (each carrying a REAL
    ``display_name`` — never an email — plus ``rider_id``/lat/lng/source/recorded_at);
    ``history_by_rider`` maps rider id → oldest→newest position history;
    ``plan_stops_by_rider`` optionally overrides the plan a rider is graded against
    (else ``ctx['plan_stops']``). Every row is PRIVACY-SHAPED: a ``display_name`` +
    coarse position + derived stats + an opaque ``key`` — and NEVER ``rider_id``,
    ``email`` or ``google_id``. Sorted leader-first by absolute route position;
    riders not yet on the route sort last. Fail-soft per rider — a composition error
    degrades that rider to a name-and-position base row, never a 500.
    """
    if dist_unit is None:
        dist_unit = ROSTER_DISTANCE_UNIT
    out = []
    for row in rider_rows:
        rid = row.get('rider_id')
        try:
            history = (history_by_rider or {}).get(rid) or []
            stops = None
            if plan_stops_by_rider is not None:
                stops = plan_stops_by_rider.get(rid)
            if stops is None:
                stops = ctx.get('plan_stops')
            start = _resolve_anchor(anchor, ctx, history)
            if telemetry_builder is not None:
                telemetry = telemetry_builder(row, ctx, now, history, stops)
            else:
                telemetry = compose_rider_telemetry(
                    row, ctx, now, history, plan_stops=stops, start=start, tz=tz,
                    min_history=min_history,
                    stateless_fallback=stateless_fallback,
                    rebase_from_first_fix=(anchor != 'ride_start'))
            out.append(_privacy_row(row, telemetry, ride_id, now,
                                    stale_after_minutes, dist_unit))
        except Exception:  # noqa: BLE001 — one bad rider degrades to a base row
            out.append(_base_roster_row(row, ride_id, now, stale_after_minutes, dist_unit))

    # Leader first: highest absolute route position, riders off/not-yet-on-route last.
    out.sort(key=lambda r: (r['route_position_mi']
                            if r['route_position_mi'] is not None else -1.0),
             reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Server-computed altitude profile (Tour-de-France style, gradient-graded).
# --------------------------------------------------------------------------- #
# Gradient colour buckets by signed grade %, steepest last. A descent is cool
# (blue), flat/rolling green, and climbs warm through to a brutal dark red — the
# familiar colour language of a Tour-de-France profile.
_GRADE_BUCKETS = [
    (-1.0, '#3b82f6', 'descent'),
    (3.0, '#22c55e', '0–3%'),
    (6.0, '#eab308', '3–6%'),
    (9.0, '#f97316', '6–9%'),
    (12.0, '#ef4444', '9–12%'),
    (float('inf'), '#7f1d1d', '12%+'),
]

# Cap the number of profile segments so a long brevet route stays a small payload.
_MAX_PROFILE_SEGMENTS = 240


def _grade_color(grade_pct):
    """Tour-de-France gradient colour for a signed grade %."""
    if grade_pct is None:
        return MARKER_UNKNOWN_COLOR
    for upper, color, _label in _GRADE_BUCKETS:
        if grade_pct < upper:
            return color
    return _GRADE_BUCKETS[-1][1]


def gradient_legend():
    """The gradient colour buckets as [{color, label}] for a profile legend."""
    return [{'color': color, 'label': label} for _u, color, label in _GRADE_BUCKETS]


def _nice_step(span, target_ticks):
    """A rounded 1/2/5×10ⁿ step so ~``target_ticks`` cover ``span``."""
    if span <= 0 or target_ticks <= 0:
        return 1.0
    raw = span / target_ticks
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    for mult in (1, 2, 5, 10):
        if raw <= mult * mag:
            return mult * mag
    return 10 * mag


def build_elevation_profile(track, *, width=1000, height=200, pad_left=44,
                            pad_right=12, pad_top=12, pad_bottom=22):
    """A server-computed altitude profile (SVG geometry) from a route ``track``.

    ``track`` is [{lat, lng, dist_m, e_m}] (ascending dist_m); ALTITUDE is the raw
    ``e_m`` (metres → feet), NOT the plan's cumulative climb. Returns a dict the
    shared template renders with framework builtins only:

      { available, width, height, plot: {x, y, w, h}, total_mi, min_ft, max_ft,
        area_path, segments:[{d, color, grade}], points:[[x,y],...],
        x_ticks:[{x,label}], y_ticks:[{y,label}], legend:[{color,label}] }

    Rider dots are placed by the client from ``plot`` + ``total_mi``:
    ``x = plot.x + (dist_mi / total_mi) * plot.w`` (see ``place_x``); the dot's y is
    interpolated from ``points``. ``available`` is False when the route carries no
    usable elevation, so the caller hides the profile.
    """
    pts = [p for p in (track or [])
           if p.get('dist_m') is not None and p.get('e_m') is not None]
    if len(pts) < 2:
        return {'available': False}

    total_m = pts[-1]['dist_m']
    total_mi = total_m * M_TO_MI
    if total_mi <= 0:
        return {'available': False}

    elevs_ft = [p['e_m'] * tlm.METERS_TO_FEET for p in pts]
    min_ft, max_ft = min(elevs_ft), max(elevs_ft)
    if max_ft <= min_ft:
        max_ft = min_ft + 1.0  # flat route — avoid divide-by-zero

    plot_x = pad_left
    plot_y = pad_top
    plot_w = max(1.0, width - pad_left - pad_right)
    plot_h = max(1.0, height - pad_top - pad_bottom)

    def px(dist_m):
        return round(plot_x + (dist_m / total_m) * plot_w, 2)

    def py(e_ft):
        return round(plot_y + (1 - (e_ft - min_ft) / (max_ft - min_ft)) * plot_h, 2)

    # Downsample to at most _MAX_PROFILE_SEGMENTS, always keeping the endpoints.
    n = len(pts)
    step = max(1, n // _MAX_PROFILE_SEGMENTS)
    idxs = list(range(0, n, step))
    if idxs[-1] != n - 1:
        idxs.append(n - 1)

    points = [[px(pts[i]['dist_m']), py(elevs_ft[i])] for i in idxs]
    baseline = round(plot_y + plot_h, 2)
    segments = []
    for a, b in zip(idxs, idxs[1:]):
        run_m = pts[b]['dist_m'] - pts[a]['dist_m']
        grade = round(((pts[b]['e_m'] - pts[a]['e_m']) / run_m) * 100, 1) if run_m > 0 else None
        x1, y1 = px(pts[a]['dist_m']), py(elevs_ft[a])
        x2, y2 = px(pts[b]['dist_m']), py(elevs_ft[b])
        # ``area_d`` closes the segment down to the baseline so the template can fill
        # the area under each segment in that segment's grade colour (the Radial-style
        # gradient fill), instead of one flat area under the whole line.
        segments.append({'d': 'M{} {}L{} {}'.format(x1, y1, x2, y2),
                         'area_d': 'M{} {}L{} {}L{} {}L{} {}Z'.format(
                             x1, y1, x2, y2, x2, baseline, x1, baseline),
                         'color': _grade_color(grade), 'grade': grade})

    area_path = ('M{} {}'.format(points[0][0], baseline)
                 + ''.join('L{} {}'.format(x, y) for x, y in points)
                 + 'L{} {}Z'.format(points[-1][0], baseline))

    mi_step = _nice_step(total_mi, 6)
    x_ticks, m = [], 0.0
    while m <= total_mi + 1e-6:
        x_ticks.append({'x': round(plot_x + (m / total_mi) * plot_w, 2),
                        'label': '{:g}'.format(round(m, 1))})
        m += mi_step

    ft_step = _nice_step(max_ft - min_ft, 4)
    y_ticks_out = []
    e = math.ceil(min_ft / ft_step) * ft_step
    while e <= max_ft + 1e-6:
        y_ticks_out.append({'y': py(e), 'label': '{:g}'.format(round(e))})
        e += ft_step

    return {
        'available': True,
        'width': width,
        'height': height,
        'plot': {'x': plot_x, 'y': plot_y, 'w': round(plot_w, 2), 'h': round(plot_h, 2)},
        'total_mi': round(total_mi, 2),
        'min_ft': round(min_ft),
        'max_ft': round(max_ft),
        'area_path': area_path,
        'segments': segments,
        'points': points,
        'x_ticks': x_ticks,
        'y_ticks': y_ticks_out,
        'legend': gradient_legend(),
    }


def place_x(dist_mi, profile):
    """Client-parity helper: the x pixel for a distance-along-route (miles) on a
    built profile, or None when the profile is unavailable / distance is missing.
    The template's JS computes the identical mapping to place rider dots."""
    if not profile or not profile.get('available') or dist_mi is None:
        return None
    total_mi = profile.get('total_mi') or 0
    if total_mi <= 0:
        return None
    plot = profile['plot']
    frac = min(1.0, max(0.0, dist_mi / total_mi))
    return round(plot['x'] + frac * plot['w'], 2)


def place_y(dist_mi, profile):
    """The y pixel of the profile line at a distance-along-route (miles), by linear
    interpolation between the built ``points`` — the SERVER twin of the live page's
    ``profileY`` JS, so a server-computed overlay marker and a client re-position of
    the same marker land on the identical pixel. None when unavailable."""
    x = place_x(dist_mi, profile)
    if x is None:
        return None
    pts = (profile or {}).get('points') or []
    if not pts:
        return None
    for i in range(1, len(pts)):
        if x <= pts[i][0]:
            x0, y0 = pts[i - 1][0], pts[i - 1][1]
            x1, y1 = pts[i][0], pts[i][1]
            t = (x - x0) / (x1 - x0) if x1 > x0 else 0
            return round(y0 + t * (y1 - y0), 2)
    return pts[-1][1]


# --------------------------------------------------------------------------- #
# Route geometry + control/break overlay for the plan-page altitude profile.
# --------------------------------------------------------------------------- #
# A stop with no mapped colour falls back to a neutral slate.
_MARKER_DEFAULT_COLOR = '#64748b'


def track_from_route(route, max_points=2000):
    """A downsampled [{lat, lng, dist_m, e_m}] track from an rwgps ``fetch_route``
    result — the geometry ``build_elevation_profile`` consumes. Mirrors the private
    ``_radial_track`` / ``_route_geometry`` extraction each app's ``routes/live.py``
    already runs (track point x=lng, y=lat, d=dist_m, e=elev_m). Fail-soft: any
    missing / malformed route yields ``[]`` so the caller degrades to an empty
    profile rather than raising."""
    tps = [tp for tp in ((route or {}).get('track_points') or [])
           if tp.get('x') is not None and tp.get('y') is not None]
    if not tps:
        return []
    step = max(1, len(tps) // max_points) if max_points and max_points > 0 else 1
    track = []
    for tp in tps[::step]:
        try:
            track.append({'lat': float(tp['y']), 'lng': float(tp['x']),
                          'dist_m': float(tp.get('d') or 0),
                          'e_m': float(tp['e']) if tp.get('e') is not None else None})
        except (TypeError, ValueError):
            continue
    return track


def overlay_stop_markers(profile, stops, colors=None):
    """Server-computed control/break markers for the altitude profile overlay.

    Each stop (a v2/pace stop dict carrying ``cumul_mi``/``type``/``name``/``eta``/
    ``break_min``) is placed on the built ``profile`` at ``x = place_x(cumul_mi)`` and
    ``y = place_y(cumul_mi)`` — the same distance→pixel mapping the client uses — and
    coloured by ``colors.get(stop['type'])``. Returns
    ``[{i, x, y, color, name, cumul_mi, eta, break_min, type}]``, or ``[]`` when the
    profile is unavailable so the template simply omits the overlay."""
    if not profile or not profile.get('available') or not stops:
        return []
    colors = colors or {}
    markers = []
    for i, s in enumerate(stops):
        cumul_mi = s.get('cumul_mi')
        x = place_x(cumul_mi, profile)
        y = place_y(cumul_mi, profile)
        if x is None or y is None:
            continue
        stype = s.get('type')
        markers.append({
            'i': s.get('i', i),
            'x': x,
            'y': y,
            'color': colors.get(stype, _MARKER_DEFAULT_COLOR),
            'name': s.get('name') or '',
            'cumul_mi': cumul_mi,
            'eta': s.get('eta') or '',
            'break_min': int(s.get('break_min') or 0),
            'type': stype,
        })
    return markers
