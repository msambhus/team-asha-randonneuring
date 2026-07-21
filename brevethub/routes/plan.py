"""BrevetHub ride planning — the pacing schedule for an upcoming brevet.

A rider opens a brevet from the calendar, picks a target average speed (or a target
finish time), and sees a per-stop pacing schedule: cumulative distance, arrival
time, time bank vs the ACP control cutoff, and average speed. The math is the
REUSED, club-agnostic pacing engine (shared/pacing.py, extracted verbatim from Team
Asha's proven ``recalculate_cumulative_values``) — BrevetHub runs it in kilometres
(passed straight through the engine's unit-agnostic ``distance_miles`` field, reading
``avg_speed`` back as km/h). DISPLAY is miles / mph: per-stop distance and speed are
converted at render time, while the ride's TOTAL distance stays km (a brevet is a
200/300/400/600 km event). The ``?speed=`` input is mph, converted to km-h internally.

Guest surface (NO account required):
  GET  /plan/<event_id>[?speed=<mph> | ?finish=<hours>]
                                     — compute + render the schedule for a target
                                       pace. Guests can compute freely; the view
                                       exposes no rider PII.

Rider surface (authenticated BrevetHub rider):
  POST /plan/<event_id>/save         — persist the rider's target + the
                                       server-recomputed schedule to rp_brevet_plan
                                       (JSON API; 401 for guests with a login_url).

Scope A (keyless): stops are derived evenly from the brevet distance (every 100 km
plus a final stop at the exact total) — no RWGPS credentials or real controls yet.
Scope B (RWGPS-backed real controls + elevation) can later swap in a richer stop
generator without touching the engine or rp_brevet_plan.

Isolation: imports only flask / stdlib / brevethub.* / shared.*, and every model
call is on an rp_* table, so test_brevethub_isolation.py and test_rp_only.py stay
green; the schedule is always recomputed server-side (never trusted from a client).
"""
from datetime import date, datetime

from flask import (Blueprint, abort, current_app, jsonify, render_template,
                   request, url_for)

from brevethub import models
from brevethub.decorators import current_rider
from shared.pacing import recalculate_cumulative_values, _get_cutoff_hours
from shared.plan_view import (_to_v2_stops, _weather_summary_from_stop_wind,
                              compute_risk_zones)
from shared.strategies import compute_pace_strategies
from shared.weather import compute_stop_winds

plan_bp = Blueprint('plan', __name__)

# Default target when the rider has not (yet) picked a pace — a conservative,
# finishable brevet speed for every ACP distance.
DEFAULT_SPEED_KMH = 20.0

# Stops are derived evenly from the brevet distance (Scope A, keyless): a control
# every STOP_STEP_KM plus a final stop at the exact total.
STOP_STEP_KM = 100

# ── Display units: miles / mph ─────────────────────────────────────────────
# The /plan page shows per-stop DISTANCE in miles and SPEED in mph (US convention),
# while the ride's TOTAL distance stays km (a brevet is a 200/300/400/600 km event).
#
# Two data sources, one display unit:
#   • Real RWGPS plans: the reused shared/rwgps.py engine emits NATIVE miles / mph /
#     feet and stores them verbatim in rp_brevet_route_plan[_stop]. So the real-plan
#     path DISPLAYS those native values directly (round only) — no conversion.
#   • Synthetic Scope-A plans + the ACP cutoff math run internally in km / km-h (the
#     km-native ACP model), and the saved rp_brevet_plan payload stays km. So the
#     synthetic path CONVERTS km → miles and km-h → mph at display time only.
# Elevation stays feet either way. The target-pace INPUT (?speed=) is mph and is
# converted to km-h for the internal engine in _resolve_target.
KM_PER_MILE = 1.609344
MI_PER_KM = 1.0 / KM_PER_MILE


def _round1(value):
    """Round a native NUMERIC value to 1 dp (or None). Coerces to float first: the
    NUMERIC columns come back from psycopg2 as ``Decimal`` and ``Decimal`` mixed with
    float raises ``TypeError`` — so cast before rounding (also accepts int / str)."""
    return round(float(value), 1) if value is not None else None


def _km_to_mi(km):
    """km → miles (or None), for the synthetic path's display distances. Coerces to
    float first (NUMERIC → Decimal, and ``Decimal * float`` raises)."""
    return round(float(km) * MI_PER_KM, 1) if km is not None else None


def _kmh_to_mph(kmh):
    """km-h → mph (or None), for the synthetic path's display speeds. Coerces to
    float first (NUMERIC → Decimal, and ``Decimal * float`` raises)."""
    return round(float(kmh) * MI_PER_KM, 1) if kmh is not None else None


def _control_distances(total_km):
    """Evenly spaced cumulative stop distances for a brevet, ending at the total.

    e.g. total 200 -> [100, 200]; total 600 -> [100, 200, 300, 400, 500, 600].
    Always includes a final stop at the exact total, even when it is not a clean
    multiple of the step.
    """
    stops = []
    d = STOP_STEP_KM
    while d < total_km:
        stops.append(float(d))
        d += STOP_STEP_KM
    stops.append(float(total_km))
    return stops


def _cutoff_hours(event):
    """The brevet's control cutoff in hours: the cached ``time_limit_hours`` when
    present, else the ACP distance->time-limit mapping from the shared engine."""
    limit = event.get('time_limit_hours')
    if limit is not None:
        return float(limit)
    return _get_cutoff_hours(int(event['distance_km']))


def _resolve_target(source, total_km):
    """Resolve the target average speed (km/h) from the request inputs.

    ``source`` is a mapping (request.args or a JSON/form body). Precedence:
    an explicit ``speed`` (MPH — the page's display unit) wins and is converted to
    km-h for the km-native engine; else a ``finish`` time (hours) is converted to a
    speed; else the default. Returns ``(speed_kmh, mode)`` where mode is
    'speed' | 'finish' | 'default'. Invalid/non-positive inputs fall through.
    """
    speed_raw = str(source.get('speed') or '').strip()
    if speed_raw:
        try:
            speed_mph = float(speed_raw)
            if speed_mph > 0:
                return speed_mph * KM_PER_MILE, 'speed'   # mph input → km-h internal
        except (TypeError, ValueError):
            pass

    finish_raw = str(source.get('finish') or '').strip()
    if finish_raw:
        try:
            hours = float(finish_raw)
            if hours > 0:
                return total_km / hours, 'finish'
        except (TypeError, ValueError):
            pass

    return DEFAULT_SPEED_KMH, 'default'


def _compute_schedule(total_km, cutoff_hours, speed_kmh):
    """Per-stop pacing schedule from the REUSED shared engine.

    Builds a stop per control distance with a segment time implied by the target
    speed, then hands the list to ``recalculate_cumulative_values`` — which fills in
    seg_dist, avg_speed (km/h, since km go straight through the unit-agnostic
    ``distance_miles`` field), cumulative/arrival time, and the time bank vs the
    cutoff. Returns the engine's mutated stop dicts.
    """
    stops = []
    prev = 0.0
    for cum in _control_distances(total_km):
        seg = cum - prev
        seg_time = int(round(seg / speed_kmh * 60)) if speed_kmh > 0 else 0
        stops.append({
            'distance_miles': cum,        # km passed straight through (unit-agnostic engine)
            'elevation_gain': 0,          # no elevation in Scope A (flat/gray difficulty)
            'segment_time_min': seg_time,
            'stop_duration_min': 0,       # no rest stops modelled in Scope A
        })
        prev = cum
    return recalculate_cumulative_values(
        stops, {'name': ''}, cutoff_hours=cutoff_hours, total_mi=total_km)


def _fmt_hm(minutes):
    """Format a minute count as ``'Hh MMm'`` (e.g. 90 -> '1h 30m'), or None.

    Handles negatives (a blown time bank) with a leading minus so the template can
    print the value directly without any custom Jinja filter.
    """
    if minutes is None:
        return None
    total = int(round(minutes))
    sign = '-' if total < 0 else ''
    h, m = divmod(abs(total), 60)
    return f"{sign}{h}h {m:02d}m"


def _display_rows(raw):
    """Turn the engine's stop dicts into display rows for the template.

    Pre-formats the clock-ish fields in Python so ``plan.html`` needs only builtin
    Jinja (no commafy/clean_name — this app registers neither).
    """
    rows = []
    for s in raw:
        tb = s.get('time_bank_min')
        rows.append({
            # The engine's unit-agnostic distance_miles field carries km here (Scope A
            # passes km straight through); convert to miles for display. Speed comes
            # back km-h; convert to mph.
            'distance_mi': _km_to_mi(s['distance_miles']),
            'arrival': _fmt_hm(s.get('arrival_time_min')),
            'avg_speed_mph': _kmh_to_mph(s.get('avg_speed')),
            'time_bank': _fmt_hm(tb),
            'time_bank_positive': (tb is not None and tb >= 0),
            'time_bank_known': tb is not None,
        })
    return rows


def _serialize_plan(raw, speed_kmh, cutoff_hours, total_km):
    """JSON-safe, server-computed plan payload persisted to rp_brevet_plan.plan_data.

    Stores raw minute values (not formatted strings) so a later view can re-render
    or recompute without re-deriving them.
    """
    return {
        'target_speed_kmh': round(speed_kmh, 2),
        'cutoff_hours': cutoff_hours,
        'total_km': total_km,
        'stops': [{
            'distance_km': int(round(s['distance_miles'])),
            'arrival_time_min': s.get('arrival_time_min'),
            'time_bank_min': s.get('time_bank_min'),
            'avg_speed': s.get('avg_speed'),
        } for s in raw],
    }


# ── Real RWGPS plan rendering (native miles / mph; total distance in km) ────
# Difficulty coloring bands (unit-agnostic 0-10 score → a green→red ramp). Mirrors
# the intent of Team Asha's per-segment difficulty coloring; the score itself is
# what the reused engine's _compute_difficulty_score produced.
_DIFFICULTY_BANDS = [
    (2.0, '#22c55e'),   # easy — green
    (4.0, '#84cc16'),   # rolling — lime
    (6.0, '#eab308'),   # moderate — amber
    (8.0, '#f97316'),   # hard — orange
    (10.1, '#ef4444'),  # very hard — red
]


def _difficulty_color(score):
    """Map a 0-10 difficulty score to a green→red hex color for a stop marker/row."""
    s = score or 0
    for threshold, color in _DIFFICULTY_BANDS:
        if s < threshold:
            return color
    return _DIFFICULTY_BANDS[-1][1]


def _build_elevation_svg(stops_mi, *, width=940, height=200):
    """Build SVG geometry for the cumulative-climb elevation profile (feet vs miles).

    Consumes the display stops (x in miles, matching the tables) and the stored
    per-stop elevation gain (feet). Returns a dict of pre-computed pixel paths /
    gridlines / markers so plan.html stays builtin-Jinja only (no namespace math in
    the template). Modelled on TA's journey_svg macro.
    """
    padl, padr, padt, padb = 40, 16, 18, 26
    inner_w = width - padl - padr
    inner_h = height - padt - padb

    total_mi = stops_mi[-1]['distance_mi'] if stops_mi else 0
    span = total_mi or 1

    # Cumulative climb series (feet) at each stop's cumulative mile position.
    pts = []
    cum_ft = 0
    for s in stops_mi:
        cum_ft += (s['elevation_gain'] or 0)
        pts.append({'mi': s['distance_mi'] or 0, 'ft': cum_ft})
    max_ft = max((p['ft'] for p in pts), default=0) or 1

    def _px(mi):
        return round(padl + (mi / span) * inner_w, 2)

    def _py(ft):
        return round(padt + inner_h - (ft / max_ft) * inner_h, 2)

    # Line + filled area under it.
    line_cmds = []
    for i, p in enumerate(pts):
        line_cmds.append(f"{'M' if i == 0 else 'L'} {_px(p['mi'])} {_py(p['ft'])}")
    line_path = ' '.join(line_cmds)
    baseline_y = round(padt + inner_h, 2)
    area_path = (
        f"M {_px(0)} {baseline_y} " + ' '.join('L' + c[1:] for c in line_cmds)
        + f" L {_px(span)} {baseline_y} Z"
    ) if pts else ''

    # Mile gridlines every 25 mi (minor unless a 50-mi line).
    gridlines = []
    step = 25
    mi = 0
    while mi <= total_mi + 0.001:
        gridlines.append({'x': _px(mi), 'label': int(mi),
                          'minor': (mi % 50 != 0)})
        mi += step

    # Elevation axis labels (0, half, max).
    elev_labels = [
        {'y': _py(0), 'label': 0},
        {'y': _py(max_ft / 2), 'label': int(round(max_ft / 2))},
        {'y': _py(max_ft), 'label': int(round(max_ft))},
    ]

    # Per-stop markers, colored by difficulty.
    markers = []
    for i, s in enumerate(stops_mi):
        st = s['stop_type']
        markers.append({
            'x': _px(s['distance_mi'] or 0),
            'y': _py(pts[i]['ft']),
            'r': 7 if st in ('start', 'finish') else (5.5 if st == 'control' else 4),
            'color': s['difficulty_color'],
            'location': s['location'],
            'distance_mi': s['distance_mi'],
            'elevation_gain': s['elevation_gain'],
            'difficulty_score': s['difficulty_score'],
        })

    return {
        'width': width, 'height': height,
        'line_path': line_path, 'area_path': area_path,
        'gridlines': gridlines, 'elev_labels': elev_labels, 'markers': markers,
        'total_mi': round(total_mi, 1), 'max_ft': int(round(max_ft)),
    }


def _build_real_plan(plan, stops, stop_winds=None):
    """Turn a persisted real plan (native miles/mph/feet) into a miles / mph display
    context for plan.html: per-stop rows, the SVG elevation profile, and plan-level
    summary values. The engine already stores miles / mph / feet, so this path shows
    the NATIVE values directly (rounded) — no unit conversion.

    ``stop_winds`` (when present) is the per-stop forecast wind list from
    ``compute_stop_winds`` — SAME length + order as ``stops``, with ``None`` for a
    stop that has no forecast. Each stop's dict gets a ``wind`` field (its wind dict
    or None; wind stays km-h, from the shared km-h wind macro); ``has_wind`` flags
    whether the Wind column should render at all.
    """
    display_stops = []
    for i, s in enumerate(stops):
        wind = stop_winds[i] if stop_winds and i < len(stop_winds) else None
        is_meal = s['stop_type'] == 'meal'
        # A meal-break row is a rest, not a control: no segment/speed/difficulty — just
        # its clock-typed label (notes) and its dwell (stored in segment_time_min). Its
        # cum_time_min is the break-inclusive ETA, so later ETAs already fold in the stop.
        display_stops.append({
            'stop_order': s['stop_order'],
            'location': s['location'],
            'stop_type': s['stop_type'],
            'is_meal': is_meal,
            'meal_label': s.get('notes') or '' if is_meal else '',
            'dwell_min': s['segment_time_min'] if is_meal else None,
            'distance_mi': _round1(s['distance_miles']),          # native miles
            'seg_dist_mi': None if is_meal else _round1(s['seg_dist']),   # native miles
            'elevation_gain': None if is_meal else s['elevation_gain'],   # feet
            'ft_per_mi': None if is_meal else s['ft_per_mi'],             # labeled ft/mi
            'avg_speed_mph': None if is_meal else _round1(s['avg_speed']),  # native mph
            'difficulty_score': None if is_meal else s['difficulty_score'],
            'difficulty_color': None if is_meal else _difficulty_color(s['difficulty_score']),
            'arrival': _fmt_hm(s['cum_time_min']),
            'time_bank': _fmt_hm(s['time_bank_min']),
            'time_bank_positive': (s['time_bank_min'] is not None
                                   and s['time_bank_min'] >= 0),
            'time_bank_known': s['time_bank_min'] is not None,
            'wind': None if is_meal else wind,
        })

    # The elevation profile + terrain strip are per-CONTROL; meal rows carry no segment
    # geometry, so exclude them from the SVG (they'd double-mark a control's distance).
    control_stops = [ds for ds in display_stops if not ds['is_meal']]
    final_mi = control_stops[-1]['distance_mi'] if control_stops else None
    total_break_min = plan.get('total_break_time_min') or 0
    return {
        'name': plan['name'],
        'variant': plan.get('variant', 'conservative'),
        'rwgps_url': plan['rwgps_url'],
        # The ride's TOTAL distance stays km (the brevet's nominal ACP distance is on
        # event.distance_km); total_distance_mi is the route's measured length in miles,
        # used only as the denominator for the per-segment difficulty strip.
        'total_distance_mi': _round1(plan['total_distance_miles']),
        'total_elevation_ft': plan['total_elevation_ft'],
        'overall_ft_per_mile': plan['overall_ft_per_mile'],
        'avg_moving_speed_mph': _round1(plan['avg_moving_speed']),   # native mph
        'total_break_time_min': total_break_min,
        'total_break_hm': _fmt_hm(total_break_min) if total_break_min else None,
        'final_distance_mi': final_mi,
        'stops': display_stops,
        'has_wind': any(ds['wind'] for ds in display_stops),
        'svg': _build_elevation_svg(control_stops),
    }


# ── rpv2 3-tab plan view (rich visual parity with Team Asha) ────────────────
# The real-plan page renders the same rich Plan / Strategies / Weather layout Team
# Asha's /ride-plan/<slug>/v2 uses, driven by the SHARED pure functions (no fork):
# BrevetHub's rp_brevet_route_plan_stop rows are normalized to the shape
# shared._to_v2_stops expects, fed the cron-warmed forecast wind (never a live call),
# and passed through the promoted toughness / strategy / risk math. Everything below
# is server-computed and PII-free — the page stays guest-readable.


def _event_date(raw):
    """Coerce an event date (a ``datetime.date`` from the DB or an ISO string from a
    test/JSON) to a ``date``, or None if it can't be parsed. Used both to date the
    forecast lookup and to feed the solar sunrise/sunset math."""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return datetime.strptime(raw[:10], '%Y-%m-%d').date()
        except ValueError:
            return None
    return None


def _event_date_str(raw):
    """A display 'YYYY-MM-DD' string for an event date (or '' when absent), so the
    template never calls .isoformat() on a value that might be a plain string."""
    d = _event_date(raw)
    return d.isoformat() if d else ''


def _to_v2_stop_rows(stops):
    """Normalize BrevetHub real-plan stop rows into the shape shared._to_v2_stops wants.

    BrevetHub's schema lacks the two per-stop fields Team Asha carries — an explicit
    arrival time and a stop duration — so they are synthesized here in Python (no
    migration):

      * ``arrival_time_min`` — derived from the stored break-inclusive ``cum_time_min``
        (a meal row's own dwell is subtracted so its ETA is the arrival BEFORE eating).
      * ``stop_duration_min`` — a control's is 0; a meal-break row (``stop_type='meal'``,
        whose ``segment_time_min`` holds the dwell) becomes a rest stop carrying that
        dwell, so the itinerary shows the break without a schema change.

    Returns a list aligned 1:1 with the rows the v2 itinerary + wind list render.
    """
    rows = []
    for s in stops:
        is_meal = (s.get('stop_type') or '').lower() == 'meal'
        dwell = int(s.get('segment_time_min') or 0) if is_meal else 0
        cum = int(s.get('cum_time_min') or 0)
        tb = s.get('time_bank_min')
        rows.append({
            'location': s.get('location') or (s.get('notes') if is_meal else '') or '',
            'stop_type': 'rest' if is_meal else s.get('stop_type'),
            'distance_miles': float(s['distance_miles']) if s.get('distance_miles') is not None else 0.0,
            'seg_dist': 0.0 if is_meal else (float(s['seg_dist']) if s.get('seg_dist') is not None else 0.0),
            'elevation_gain': 0 if is_meal else int(s.get('elevation_gain') or 0),
            'ft_per_mi': 0 if is_meal else int(s.get('ft_per_mi') or 0),
            'segment_time_min': 0 if is_meal else int(s.get('segment_time_min') or 0),
            'stop_duration_min': dwell,
            # cum_time_min is break-inclusive; a meal's own dwell is folded out so its
            # arrival ETA is before the break (later stops already include it).
            'arrival_time_min': cum - dwell,
            'time_bank_min': None if is_meal else (int(tb) if tb is not None else None),
            'notes': s.get('notes'),
        })
    return rows


def _route_latlon(weather_row):
    """A representative (lat, lon) for the route from the cached weather sample points,
    used to derive sunrise/sunset for the risk overlay. Picks the middle sample so the
    coordinate is central to the route. Returns (None, None) when unavailable, which
    makes compute_risk_zones fall back to its heuristic table (no crash)."""
    if not weather_row:
        return None, None
    pts = weather_row.get('sample_points') or []
    if not pts:
        return None, None
    mid = pts[len(pts) // 2]
    try:
        return float(mid['lat']), float(mid['lng'])
    except (KeyError, TypeError, ValueError):
        return None, None


def _v2_stop_winds(v2_rows, weather_row, forecast_date, start_time_str):
    """Per-stop forecast wind for the v2 rows, from the warm route-weather cache only.

    Hands the cached forecast + sample points to the SHARED ``compute_stop_winds`` — the
    SAME pure per-stop math Team Asha uses — so the guest page never calls Open-Meteo /
    RWGPS live. Returns a list aligned with ``v2_rows`` (None per unresolved stop), or
    None on any miss/error so the itinerary renders without the Wind column, never a 500.
    """
    try:
        if not weather_row or not forecast_date:
            return None
        weather_data = weather_row.get('weather_data')
        sample_points = weather_row.get('sample_points')
        if not weather_data or not sample_points:
            return None
        return compute_stop_winds(v2_rows, weather_data, sample_points,
                                  forecast_date, start_time_str)
    except Exception as e:  # pragma: no cover - defensive; keep the page up
        current_app.logger.warning('v2 wind injection failed: %s', e)
        return None


def _v2_weather_stops(v2_stops, stop_wind):
    """The lean per-stop forecast list for the Weather tab (label / ETA / temp / wind).

    Phase 1 is a text list from the cached forecast — no Mapbox, no live call. Reuses the
    wind fields already resolved on each v2 stop and adds the per-stop temperature."""
    out = []
    for i, s in enumerate(v2_stops):
        sw = stop_wind[i] if stop_wind and i < len(stop_wind) else None
        out.append({
            'name': s['name'],
            'eta': s['eta'],
            'cumul_mi': s['cumul_mi'],
            'temp_f': sw.get('temperature_f') if sw else None,
            'wind_mph': s['wind_mph'],
            'wind_label': s['wind_label'],
            'wind_arrow_deg': s['wind_arrow_deg'],
            'wind_known': s['wind_known'],
        })
    return out


def _v2_weighted_difficulty(v2_stops):
    """Distance-weighted mean of the per-segment toughness (0-10) for the hero gauge,
    or None when no segment carries a score."""
    tot, wsum = 0.0, 0.0
    for s in v2_stops:
        if s['seg_mi'] > 0 and s['tough_known']:
            tot += s['seg_mi']
            wsum += s['tough'] * s['seg_mi']
    return round(wsum / tot, 1) if tot > 0 else None


def _build_v2_context(event, plan, stops, variant):
    """Assemble the rpv2 render context for a real plan: the enriched itinerary, the
    read-only pace strategies, the risk overlay, the weather summary + per-stop list,
    and the PII-free roster. All server-computed; the page stays guest-readable."""
    cutoff_hours = _cutoff_hours(event)
    start_time = plan.get('start_time') or event.get('start_time') or '06:00'
    if not isinstance(start_time, str):
        start_time = start_time.strftime('%H:%M')
    total_mi = float(plan.get('total_distance_miles') or 0)

    plan_ctx = {
        'name': plan['name'],
        'distance_km': int(event['distance_km']),
        'date_str': _event_date_str(event.get('date')),
        'start_time': start_time,
        'total_distance_miles': total_mi,
        'total_elevation_ft': int(plan.get('total_elevation_ft') or 0),
        'cutoff_hours': cutoff_hours,
        'event_id': event['id'],
    }

    forecast_date = _event_date(event.get('date'))
    weather_row = None
    if forecast_date:
        try:
            weather_row = models.get_brevet_route_weather(event['id'], forecast_date)
        except Exception as e:  # pragma: no cover - defensive; keep the page up
            current_app.logger.warning('Route weather lookup failed for event %s: %s',
                                        event.get('id'), e)

    v2_rows = _to_v2_stop_rows(stops)
    stop_wind = _v2_stop_winds(v2_rows, weather_row, forecast_date, start_time)
    v2_stops = _to_v2_stops(v2_rows, plan_ctx, stop_wind)

    fuel_stops_v2 = [s for s in v2_stops
                     if s.get('break_min', 0) >= 5 or s.get('is_fuel')]
    weather_summary = _weather_summary_from_stop_wind(stop_wind, v2_rows)

    lat, lon = _route_latlon(weather_row)
    risks = compute_risk_zones(v2_rows, v2_stops, plan_ctx, start_time,
                               forecast_date, lat=lat, lon=lon)
    weather_summary['sunrise'] = risks.get('sunrise_str')
    weather_summary['sunset'] = risks.get('sunset_str')

    # Per-segment wind/toughness for the strategy cards, from the enriched v2 stops.
    # Keyed by rounded cumulative mile (route-constant) so it lines up across the
    # variant stop sets — mirrors the parent web app so the Strategies tab flags the
    # same tough/windy sections the Plan tab does (not a blank column).
    seg_meta = {round(vs.get('cumul_mi') or 0, 1): {
        'headwind_mph': vs.get('headwind_mph', 0),
        'wind_label': vs.get('wind_label', ''),
        'wind_arrow_deg': vs.get('wind_arrow_deg', 0),
        'wind_known': vs.get('wind_known', False),
        'tough_class': vs.get('tough_class', ''),
        'tough_known': vs.get('tough_known', False),
    } for vs in v2_stops}
    paces = compute_pace_strategies(v2_rows, plan_ctx, start_time, cutoff_hours,
                                    seg_meta=seg_meta)

    # Hero aggregates — prefer the stored plan-level values, fall back to the derived
    # rows so a plan with NULL summary columns still renders.
    total_moving_time = int(plan.get('total_moving_time_min')
                            or sum(s['seg_time_min'] for s in v2_stops))
    total_break_time = int(plan.get('total_break_time_min')
                           or sum(s['break_min'] for s in v2_stops))
    total_time = int(plan.get('total_elapsed_time_min')
                     or (v2_stops[-1]['cumul_time_min'] if v2_stops else 0)) \
        or (total_moving_time + total_break_time)
    overall_ft_per_mile = int(plan.get('overall_ft_per_mile')
                              or (round(plan_ctx['total_elevation_ft'] / total_mi)
                                  if total_mi > 0 else 0))
    avg_elapsed = plan.get('avg_elapsed_speed')
    if avg_elapsed is not None:
        avg_elapsed_speed = round(float(avg_elapsed), 1)
    else:
        avg_elapsed_speed = round(total_mi / (total_time / 60.0), 1) if total_time > 0 else 0

    try:
        riders = models.get_event_going_riders(event['id']) or []
    except Exception as e:  # pragma: no cover - defensive; keep the page up
        current_app.logger.warning('Roster lookup failed for event %s: %s',
                                    event.get('id'), e)
        riders = []
    going_count = sum(1 for r in riders if r['status'] == models.RideStatus.GOING.value)

    return {
        'plan': plan_ctx,
        'variant': variant,
        'stops_v2': v2_stops,
        'fuel_stops_v2': fuel_stops_v2,
        'paces': paces,
        'risks': risks,
        'weather_summary': weather_summary,
        'weather_stops': _v2_weather_stops(v2_stops, stop_wind),
        'has_forecast': bool(stop_wind and any(stop_wind)),
        'total_time': total_time,
        'total_moving_time': total_moving_time,
        'total_break_time': total_break_time,
        'overall_ft_per_mile': overall_ft_per_mile,
        'avg_elapsed_speed': avg_elapsed_speed,
        'weighted_difficulty': _v2_weighted_difficulty(v2_stops),
        'riders': riders,
        'going_count': going_count,
    }


@plan_bp.route('/plan/<int:event_id>')
def plan_view(event_id):
    """Compute + render the pacing schedule for a brevet at a target pace.

    If a real, RWGPS-backed plan has been persisted for this brevet, render THAT
    (real control names, SVG elevation profile, per-segment difficulty coloring and
    gradient speed, distances in miles / speeds in mph; total distance in km).
    Otherwise fall back to the synthetic evenly-spaced Scope-A schedule at a target pace.

    Guest-readable either way: anyone can view; no rider PII is rendered. A signed-in
    rider additionally sees their previously-saved target (synthetic mode) and the
    Save control. Unknown event -> 404.
    """
    event = models.get_brevet_event_full(event_id)
    if not event:
        abort(404)

    rider = current_rider()
    # ?variant= picks the stored pacing plan: conservative (default, the realistic pace)
    # or aggressive (+1.5 mph). Anything else falls back to conservative.
    variant = request.args.get('variant', 'conservative')
    if variant not in ('conservative', 'aggressive'):
        variant = 'conservative'

    total_km = float(event['distance_km'])
    cutoff_hours = _cutoff_hours(event)

    # Real plan mode: render the rich rpv2 3-tab view (Plan / Strategies / Weather).
    # Fail-soft — any DB error (or no stored plan) drops to the synthetic schedule and
    # never 500s the read path.
    bundle = None
    try:
        bundle = models.get_brevet_route_plan_with_stops(event_id, variant)
    except Exception as e:  # pragma: no cover - defensive; keep the page up
        current_app.logger.warning('Real plan lookup failed for event %s: %s',
                                    event_id, e)
    if bundle and bundle.get('stops'):
        v2 = _build_v2_context(event, bundle['plan'], bundle['stops'], variant)
        active_tab = request.args.get('tab', 'plan')
        if active_tab not in ('plan', 'strategies', 'weather'):
            active_tab = 'plan'
        return render_template(
            'plan.html',
            event=event,
            real_plan=True,
            v2=v2,
            variant=variant,
            active_tab=active_tab,
            cutoff_hours=cutoff_hours,
            rider=rider,
            saved=None,
        )

    speed_kmh, mode = _resolve_target(request.args, total_km)
    raw = _compute_schedule(total_km, cutoff_hours, speed_kmh)
    schedule = _display_rows(raw)

    saved = models.get_rider_brevet_plan(rider['id'], event_id) if rider else None

    # Target pace displays in mph (the engine ran in km-h); finish time is unit-neutral.
    finish_hours = round(total_km / speed_kmh, 2) if speed_kmh > 0 else None
    return render_template(
        'plan.html',
        event=event,
        real_plan=None,
        schedule=schedule,
        cutoff_hours=cutoff_hours,
        speed_mph=_kmh_to_mph(speed_kmh),
        finish_hours=finish_hours,
        finish_row=schedule[-1] if schedule else None,
        mode=mode,
        rider=rider,
        saved=saved,
    )


@plan_bp.route('/plan/<int:event_id>/save', methods=['POST'])
def save_plan(event_id):
    """Persist the signed-in rider's target + the SERVER-recomputed schedule.

    Auth ladder mirrors the calendar sign-up: no session rider -> 401 (with a
    login_url the client can send them to); unknown event -> 404. The schedule is
    always recomputed here from the reused engine — a client-posted schedule is
    never trusted.
    """
    rider = current_rider()
    if not rider:
        return jsonify({
            'error': 'Sign in to save your plan.',
            'login_url': url_for('auth.login',
                                 next=url_for('plan.plan_view', event_id=event_id)),
        }), 401

    event = models.get_brevet_event_full(event_id)
    if not event:
        return jsonify({'error': 'Event not found'}), 404

    total_km = float(event['distance_km'])
    cutoff_hours = _cutoff_hours(event)
    payload = request.get_json(silent=True) or request.form
    speed_kmh, mode = _resolve_target(payload, total_km)

    raw = _compute_schedule(total_km, cutoff_hours, speed_kmh)
    plan_data = _serialize_plan(raw, speed_kmh, cutoff_hours, total_km)
    target_finish_min = int(round(total_km / speed_kmh * 60)) if speed_kmh > 0 else None

    models.upsert_rider_brevet_plan(
        rider['id'], event_id,
        target_speed_kmh=round(speed_kmh, 2),
        target_finish_min=target_finish_min,
        plan_data=plan_data,
    )
    return jsonify({
        'ok': True,
        'event_id': event_id,
        'target_speed_kmh': round(speed_kmh, 2),
    }), 200
