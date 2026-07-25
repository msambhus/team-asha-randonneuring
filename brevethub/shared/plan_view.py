"""Pure plan-view helpers shared by Team Asha and BrevetHub.

The rich 3-tab plan page (Plan / Strategies / Weather) is built from a handful of
pure display functions: per-segment toughness, fuel detection, the ``_to_v2_stops``
row shaper, the weather summary, and the risk-overlay computation. They were
originally written inside Team Asha's ``routes/riders.py``; they are promoted here
so BOTH surfaces render from one implementation and can never drift. Team Asha keeps
byte-identical behavior via re-export shims in ``routes/riders.py``; BrevetHub ships a
byte-identical vendored copy under ``brevethub/shared/`` (Vercel bundles only files
inside its function root).

Isolation contract: stdlib only — nothing from ``services`` / ``models`` / ``routes``
and never the web framework's app/request globals (guarded by test_shared_isolation).
No web-framework import anywhere in this module.
"""
import math


_PLAN_DEFAULTS = {
    'id': None,
    'event_id': None,
    'name': '',
    'slug': None,
    'variant': 'conservative',
    'date_str': None,
    'distance_km': None,
    'total_distance_miles': 0,
    'total_elevation_ft': 0,
    'cutoff_hours': None,
    'start_time': '06:00',
    'rwgps_url': None,
    'rwgps_url_team': None,
    'rwgps_route_id': None,
    'avg_moving_speed': None,
    'total_moving_time_min': 0,
    'total_break_time_min': 0,
    'total_elapsed_time_min': 0,
}

_STOP_DEFAULTS = {
    'id': None,
    'stop_order': None,
    'location': '',
    'name': '',
    'stop_type': 'waypoint',
    'notes': None,
    'distance_miles': None,
    'seg_dist': None,
    'elevation_gain': None,
    'ft_per_mi': None,
    'segment_time_min': None,
    'stop_duration_min': 0,
    'cum_time_min': None,
    'arrival_time_min': None,
    'time_bank_min': None,
    'avg_speed': None,
    'difficulty_score': None,
}


def plan_header(record, **overrides):
    """Losslessly normalize either product's persisted plan header."""
    plan = dict(record or {})
    for key, value in _PLAN_DEFAULTS.items():
        plan.setdefault(key, value)
    plan.update(overrides)
    return plan


def plan_stop(record, **overrides):
    """Losslessly normalize either product's persisted stop/control row."""
    stop = dict(record or {})
    for key, value in _STOP_DEFAULTS.items():
        stop.setdefault(key, value)
    stop.update(overrides)
    if not stop.get('location') and stop.get('name'):
        stop['location'] = stop['name']
    if not stop.get('name') and stop.get('location'):
        stop['name'] = stop['location']
    return stop


# ── Segment toughness ───────────────────────────────────────────────────────
# Headwind<->grade equivalence. A relentless, unrewarded headwind rides like an
# "invisible hill" with no summit -> ~15 ft/mile per mph. Tailwind helps, but less
# than a headwind hurts (the drag term is squared), so it eases at a lower rate.
_HEADWIND_FT_PER_MPH = 15.0
_TAILWIND_FT_PER_MPH = 7.0


def _temp_penalty(temp_f):
    """Heat/cold penalty (0-2.5 points) added to a segment's toughness.

    Endurance cycling performance follows an inverted-U vs ambient temperature,
    optimal ~50-68F. Heat dominates (~6.5% power loss by 90F, double-digit
    >95F); cold is smaller and later-onset. Anchored to that research:
    +1.0 @ 90F, +2.0 @ 100F, +0.5 at/below freezing; flat 0 in the comfort band.
    Returns 0.0 when no temperature is available.
    """
    if temp_f is None:
        return 0.0
    t = temp_f
    if t <= 32:
        return 0.5
    if t < 40:
        return 0.25
    if t < 50:
        return 0.1
    if t <= 68:
        return 0.0
    if t < 80:
        return 0.25
    if t < 85:
        return 0.5
    if t < 90:
        return 0.75
    if t < 95:
        return 1.0
    if t < 100:
        return 1.5
    if t < 105:
        return 2.0
    return 2.5


def _compute_segment_toughness(ft_per_mi, headwind_mph, temp_f=None):
    """Per-segment toughness 0-10 from climbing + headwind + temperature.

    Headwind and climbing are unified into an "effective ft/mile" using the
    physical headwind<->grade equivalence (see _HEADWIND_FT_PER_MPH), so a
    stiff headwind is weighted as hard as the equivalent sustained climb it
    actually is — far higher than the old ad-hoc term. The combined climb+wind
    load maps to a 0-8.5 base (~16 ft/mile per point), then a heat/cold penalty
    can push the worst segments to 10. Degrades gracefully: no wind forecast ->
    climbing only; no temperature -> no heat/cold penalty.
    """
    hw = headwind_mph or 0
    wind_equiv = hw * _HEADWIND_FT_PER_MPH if hw >= 0 else hw * _TAILWIND_FT_PER_MPH
    eff_ft_per_mi = max((ft_per_mi or 0) + wind_equiv, 0.0)
    base = min(eff_ft_per_mi / 16.0, 8.5)
    score = base + _temp_penalty(temp_f)
    return round(min(max(score, 0.0), 10.0), 1)


def _toughness_class(score):
    """Map a 0-10 toughness score to a t1-t4 color tier (mirrors fpm tiers)."""
    if score < 3:
        return 't1'
    if score < 5:
        return 't2'
    if score < 7:
        return 't3'
    return 't4'


# ── Fuel detection ──────────────────────────────────────────────────────────
# Food / refuel keywords used to flag stops as fuel stops. Matched against the
# stop's combined note + location string (case-insensitive). Surfaces stops where
# the rider plans to eat or refill water even if no explicit break duration was
# entered.
_FUEL_KEYWORDS = ('lunch', 'dinner', 'breakfast', 'safeway', 'holland', 'holiday',
                  'subway', 'taco', 'cafe', 'coffee', 'grocery', 'market',
                  'food', 'snack', 'deli', 'pizza', 'burger', 'mcdonald',
                  'starbucks', 'restaurant', 'water', 'refill', 'refuel',
                  'eat', 'meal')


def _stop_is_fuel(stop_or_v2):
    """True if the stop has a food/refuel keyword in note or name."""
    haystack = ((stop_or_v2.get('note') or stop_or_v2.get('notes') or '')
                + ' '
                + (stop_or_v2.get('name') or stop_or_v2.get('location') or '')).lower()
    return any(k in haystack for k in _FUEL_KEYWORDS)


# ── Wind-dict normalization ─────────────────────────────────────────────────
def normalize_wind(sw):
    """Map a raw per-stop wind dict to the ONE canonical shape both surfaces use.

    Team Asha's ``services.weather.fetch_stop_wind`` and the shared
    ``shared.weather.compute_stop_winds`` (used by BrevetHub) both return a rich
    per-stop dict; this collapses either to the neutral keys the plan view needs so
    a caller never re-derives the head/tail/cross word or the km/h->mph conversion:

      * ``wind_speed_mph`` — forecast wind speed (mph), or None
      * ``headwind_mph``   — SIGNED headwind component (mph, +head / -tail); 0.0 when
                             absent
      * ``wind_type``      — the lowercased raw type/label string
      * ``arrow_rotation`` — arrow rotation in degrees (from ``wind_arrow_deg``)
      * ``wind_label``     — 'Head' / 'Tail' / 'Cross', or None when unclassifiable
      * ``temperature_f``  — forecast temperature (F), or None

    Returns None for a missing/empty stop-wind entry so the caller can lay out one
    cell per stop and leave unresolved stops blank.
    """
    if not sw:
        return None
    # Signed headwind component (positive = headwind). Both sources carry it in km/h.
    hw_kmh = sw.get('headwind_kmh')
    headwind_mph = round(float(hw_kmh) * 0.621371, 1) if hw_kmh is not None else 0.0
    wind_type = (sw.get('wind_type') or sw.get('label') or '').lower()
    if 'tail' in wind_type:
        wind_label = 'Tail'
    elif 'head' in wind_type:
        wind_label = 'Head'
    elif 'cross' in wind_type:
        wind_label = 'Cross'
    else:
        wind_label = None
    return {
        'wind_speed_mph': sw.get('wind_speed_mph'),
        'headwind_mph': headwind_mph,
        'wind_type': wind_type,
        'arrow_rotation': sw.get('wind_arrow_deg'),
        'wind_label': wind_label,
        'temperature_f': sw.get('temperature_f'),
    }


# ── v2 stop shaping ─────────────────────────────────────────────────────────
def _to_v2_stops(stops, plan, stop_wind):
    """Map the v1 stop dicts into the design's expected shape."""
    start_time_str = plan.get('start_time') or '06:00'
    try:
        start_hr, start_min = (int(x) for x in start_time_str.split(':')[:2])
    except (ValueError, AttributeError):
        start_hr, start_min = 6, 0
    start_minutes = start_hr * 60 + start_min

    n = len(stops)
    out = []
    for i, s in enumerate(stops):
        is_start = i == 0
        is_finish = i == n - 1
        # The display name lives in the `location` column on ride_plan_stop.
        loc = s.get('location') or s.get('name') or ''
        # Map DB stop_type → design type. Fallback by name conventions.
        db_type = (s.get('stop_type') or '').lower().strip()
        if is_start:
            v2_type = 'start'
        elif is_finish:
            v2_type = 'finish'
        elif db_type in ('control', 'rest', 'waypoint'):
            v2_type = db_type
        elif 'control' in loc.lower():
            v2_type = 'control'
        elif s.get('stop_duration_min', 0) >= 15:
            v2_type = 'rest'
        else:
            v2_type = 'waypoint'

        # Arrival ETA from arrival_time_min
        arrive = start_minutes + (s.get('arrival_time_min') or 0)
        day_offset, arrive_in_day = divmod(arrive, 24 * 60)
        eta_h, eta_m = divmod(arrive_in_day, 60)
        eta = f"{eta_h:02d}:{eta_m:02d}"
        if day_offset >= 1:
            eta = f"{eta}+{day_offset}"

        # Bank like "+1:35" / "-0:25"
        bank_min = s.get('time_bank_min')
        if bank_min is None:
            bank = ''
        else:
            sign = '+' if bank_min >= 0 else '-'
            am = abs(bank_min)
            bank = f"{sign}{am // 60}:{am % 60:02d}"

        # Wind data from stop_wind, collapsed to the canonical shape (headwind in
        # mph, the head/tail/cross word, arrow rotation, temp). normalize_wind is
        # the single home of the head/cross mapping so BrevetHub + Team Asha agree.
        wind_speed_mph = None
        wind_label = None
        wind_arrow_deg = None
        headwind_mph = 0.0
        temp_f = None
        if stop_wind and i < len(stop_wind) and stop_wind[i]:
            nw = normalize_wind(stop_wind[i])
            wind_speed_mph = nw['wind_speed_mph']
            wind_arrow_deg = nw['arrow_rotation']
            temp_f = nw['temperature_f']
            headwind_mph = nw['headwind_mph']
            wind_label = nw['wind_label']

        # Difficulty class for ft/mi
        fpm = s.get('ft_per_mi') or 0
        if fpm < 25:
            fpm_class = 't1'
        elif fpm < 50:
            fpm_class = 't2'
        elif fpm < 75:
            fpm_class = 't3'
        else:
            fpm_class = 't4'

        # ACP cutoff ETA: linear over total distance × cutoff_hours.
        # cutoff_eta = start_time + (cumul_mi / total_mi) * cutoff_h
        cutoff_eta = ''
        total_mi = plan.get('total_distance_miles') or 0
        cutoff_h = plan.get('cutoff_hours')
        cumul_mi = s.get('distance_miles') or 0
        if cutoff_h and total_mi > 0 and cumul_mi >= 0:
            cutoff_total_min = start_minutes + round((cumul_mi / total_mi) * cutoff_h * 60)
            cd, cinday = divmod(cutoff_total_min, 24 * 60)
            ch, cm = divmod(cinday, 60)
            cutoff_eta = f"{ch:02d}:{cm:02d}"
            if cd >= 1:
                cutoff_eta = f"{cutoff_eta}+{cd}"

        # Segment metrics: distance (mi), moving time (min) carried from the
        # route loop, and the implied moving speed. The start row has a 0-length
        # segment, so speed/toughness are left unknown (rendered as "—").
        seg_mi = round(s.get('seg_dist') or 0, 1)
        seg_time_min = int(s.get('segment_time_min') or 0)
        seg_speed = (round(seg_mi / (seg_time_min / 60.0), 1)
                     if seg_mi > 0 and seg_time_min > 0 else None)

        # Per-segment toughness from climbing (ft/mile) + real forecast headwind
        # (weighted as equivalent climbing) + heat/cold penalty.
        if seg_mi > 0:
            tough = _compute_segment_toughness(fpm, headwind_mph, temp_f)
            tough_class = _toughness_class(tough)
        else:
            tough = None
            tough_class = ''

        # Cumulative elapsed time at arrival (moving + prior breaks), formatted
        # "Hh MM". Mirrors the ETA clock time: ETA = start_time + elapsed.
        elapsed_min = int(s.get('arrival_time_min') or 0)
        el_h, el_m = divmod(elapsed_min, 60)
        elapsed = f"{el_h}h{el_m:02d}"

        out.append({
            'i': i,
            'type': v2_type,
            'name': loc,
            'note': s.get('notes') or '',
            'cumul_mi': round(s.get('distance_miles') or 0, 1),
            'cumul_time_min': elapsed_min,
            'elapsed': elapsed,
            'seg_mi': seg_mi,
            'seg_time_min': seg_time_min,
            'seg_speed': seg_speed if seg_speed is not None else 0,
            'seg_speed_known': seg_speed is not None,
            'elev': int(s.get('elevation_gain') or 0),
            'fpm': fpm,
            'fpm_class': fpm_class,
            'eta': eta,
            'bank': bank,
            'bank_min': bank_min if bank_min is not None else 0,
            'cutoff_eta': cutoff_eta,
            'wind_mph': wind_speed_mph if wind_speed_mph is not None else 0,
            'wind_label': wind_label or '',
            'wind_arrow_deg': wind_arrow_deg if wind_arrow_deg is not None else 0,
            'wind_known': wind_label is not None,
            'headwind_mph': headwind_mph,
            'tough': tough if tough is not None else 0,
            'tough_class': tough_class,
            'tough_known': tough is not None,
            'break_min': int(s.get('stop_duration_min') or 0),
            'is_halt': (s.get('stop_duration_min') or 0) >= 120,
            'is_fuel': _stop_is_fuel({'note': s.get('notes'), 'name': loc}),
        })
    return out


# ── Sunrise / sunset ────────────────────────────────────────────────────────
# Bay Area sunrise/sunset by month — rough approximation. The v2 risk overlay uses
# these as a heuristic when no route coordinates are available (the Team Asha page
# passes none, so it is byte-identical to the pre-promotion behavior). Times are
# PST/PDT-naive (matches local wall-clock display).
_BAY_AREA_SUN = {
    1: ('07:25', '17:20'), 2: ('06:55', '17:50'), 3: ('06:20', '18:20'),
    4: ('06:30', '19:45'), 5: ('05:55', '20:15'), 6: ('05:45', '20:30'),
    7: ('05:55', '20:30'), 8: ('06:20', '20:05'), 9: ('06:45', '19:25'),
    10: ('07:15', '18:35'), 11: ('06:45', '17:00'), 12: ('07:15', '16:45'),
}

# Beyond this latitude the sunrise equation loses meaning for parts of the year
# (polar day/night), so the solar helper refuses to guess and the caller falls
# back to its heuristic table.
_SOLAR_MAX_ABS_LAT = 65.0


def _hm_to_min(s):
    try:
        h, m = (int(x) for x in s.split(':')[:2])
        return h * 60 + m
    except (ValueError, AttributeError):
        return 0


def _min_to_hm(minutes):
    """Format a minute-of-day count back to a 'HH:MM' clock string."""
    minutes = int(round(minutes)) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def compute_sun_times(lat, lon, ride_date):
    """Sunrise/sunset ('HH:MM', 'HH:MM') in LOCAL clock time for a lat/lon + date.

    A stdlib implementation of the standard sunrise equation (NOAA-style), so the
    risk overlay works for ANY club's route — not just the Bay Area. The result is
    expressed in the wall-clock timezone implied by longitude (round(lon/15) hours
    from UTC), which lands within a few minutes of civil time across a zone and is
    the honest "solar" clock for a route with no tz metadata.

    Returns None (so the caller uses its heuristic fallback) when:
      * lat/lon/date is missing,
      * the latitude is beyond ±65° (polar day/night makes the equation unstable),
      * or the sun does not cross the horizon on that date (acos domain error).
    """
    if lat is None or lon is None or ride_date is None:
        return None
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    if abs(lat) > _SOLAR_MAX_ABS_LAT:
        return None

    day = ride_date.timetuple().tm_yday
    zenith = 90.833  # official sunrise/sunset (includes atmospheric refraction)
    lng_hour = lon / 15.0
    tz_offset = round(lon / 15.0)  # zone approximation from longitude

    def _event(is_sunrise):
        # Approximate the event time, then solve the sun's hour angle.
        t = day + (((6 if is_sunrise else 18) - lng_hour) / 24.0)
        mean_anom = (0.9856 * t) - 3.289
        true_long = (mean_anom + (1.916 * math.sin(math.radians(mean_anom)))
                     + (0.020 * math.sin(math.radians(2 * mean_anom))) + 282.634) % 360.0
        right_asc = math.degrees(math.atan(0.91764 * math.tan(math.radians(true_long)))) % 360.0
        # Put RA in the same quadrant as the true longitude, then to hours.
        right_asc += (math.floor(true_long / 90.0) * 90.0) - (math.floor(right_asc / 90.0) * 90.0)
        right_asc /= 15.0
        sin_dec = 0.39782 * math.sin(math.radians(true_long))
        cos_dec = math.cos(math.asin(sin_dec))
        cos_h = ((math.cos(math.radians(zenith)) - (sin_dec * math.sin(math.radians(lat))))
                 / (cos_dec * math.cos(math.radians(lat))))
        if cos_h > 1 or cos_h < -1:
            return None  # sun never rises / never sets on this date at this lat
        hour_angle = (360.0 - math.degrees(math.acos(cos_h))) if is_sunrise \
            else math.degrees(math.acos(cos_h))
        hour_angle /= 15.0
        local_mean = hour_angle + right_asc - (0.06571 * t) - 6.622
        utc = (local_mean - lng_hour) % 24.0
        return (utc + tz_offset) % 24.0

    try:
        sunrise_h = _event(True)
        sunset_h = _event(False)
    except (ValueError, ZeroDivisionError):
        return None
    if sunrise_h is None or sunset_h is None:
        return None
    return _min_to_hm(sunrise_h * 60), _min_to_hm(sunset_h * 60)


def compute_risk_zones(stops, v2_stops, plan, start_time_str, ride_date,
                       lat=None, lon=None):
    """Build risk-overlay data for the v2 Risks tab.

    Returns a dict the template iterates over to draw the 4-lane SVG.

    Sunrise/sunset come from the route's own coordinates (``lat``/``lon``) via the
    stdlib solar equation when they are supplied, so the overlay is correct for any
    club's geography. When no coordinates are given (Team Asha passes none), it falls
    back to the Bay Area monthly table — byte-identical to the pre-promotion behavior.
    """
    try:
        start_hr, start_min = (int(x) for x in start_time_str.split(':')[:2])
    except (ValueError, AttributeError):
        start_hr, start_min = 6, 0
    start_minutes = start_hr * 60 + start_min

    month = ride_date.month if ride_date else 5
    sun = compute_sun_times(lat, lon, ride_date)
    if sun:
        sunrise_str, sunset_str = sun
    else:
        sunrise_str, sunset_str = _BAY_AREA_SUN.get(month, _BAY_AREA_SUN[5])
    sunrise_min = _hm_to_min(sunrise_str)
    sunset_min = _hm_to_min(sunset_str)

    total_mi = plan.get('total_distance_miles') or 0
    if not total_mi or len(stops) < 2:
        return {
            'has_data': False, 'sunrise_str': sunrise_str, 'sunset_str': sunset_str,
            'segments': [], 'callouts': [],
            'night_mi_from': None, 'night_mi_to': None,
            'max_elev_ft': 0,
        }

    def find_transition_mi(target_minutes_in_day, day_offset):
        """Mile at which arrival_time crosses target (linear interp between stops)."""
        target_total = day_offset * 24 * 60 + target_minutes_in_day
        prev_total = None
        prev_mi = 0
        for s in stops:
            arr = start_minutes + (s.get('arrival_time_min') or 0)
            cur_mi = float(s.get('distance_miles') or 0)
            if prev_total is not None and prev_total <= target_total <= arr:
                if arr == prev_total:
                    return cur_mi
                t = (target_total - prev_total) / (arr - prev_total)
                return prev_mi + t * (cur_mi - prev_mi)
            prev_total = arr
            prev_mi = cur_mi
        return None

    # Build segments — one per gap between adjacent stops
    segments = []
    cum_elev = 0
    max_elev = 0
    for i in range(1, len(stops)):
        prev = stops[i - 1]
        cur = stops[i]
        mi_from = float(prev.get('distance_miles') or 0)
        mi_to = float(cur.get('distance_miles') or 0)
        cum_elev += int(cur.get('elevation_gain') or 0)
        max_elev = max(max_elev, cum_elev)
        # Wind for this segment: use v2_stops[i].wind_mph + label
        vs = v2_stops[i] if i < len(v2_stops) else {}
        wmph = vs.get('wind_mph') or 0
        wlabel = vs.get('wind_label') or ''
        if not vs.get('wind_known'):
            wind_color, wind_intense = '#cbd5e1', False
        elif wlabel == 'Head' and wmph >= 15:
            wind_color, wind_intense = '#dc2626', True
        elif wlabel == 'Head' and wmph >= 10:
            wind_color, wind_intense = '#ea580c', True
        elif wlabel == 'Cross' and wmph >= 15:
            wind_color, wind_intense = '#ca8a04', False
        elif wmph >= 10:
            wind_color, wind_intense = '#84cc16', False
        else:
            wind_color, wind_intense = '#16a34a', False
        # Bank for this segment
        bank_min = cur.get('time_bank_min')
        if bank_min is None:
            bank_color, bank_intense = '#cbd5e1', False
        elif bank_min < 30:
            bank_color, bank_intense = '#dc2626', True
        elif bank_min < 60:
            bank_color, bank_intense = '#ea580c', True
        elif bank_min < 90:
            bank_color, bank_intense = '#ca8a04', False
        else:
            bank_color, bank_intense = '#16a34a', False
        segments.append({
            'mi_from': mi_from, 'mi_to': mi_to,
            'cum_elev': cum_elev,
            'wind_color': wind_color, 'wind_intense': wind_intense,
            'wind_mph': wmph, 'wind_label': wlabel,
            'bank_color': bank_color, 'bank_intense': bank_intense,
            'bank_min': bank_min if bank_min is not None else 0,
        })

    # Elevation polyline points (cumulative)
    elev_pts = [{'mi': 0, 'cum': 0}]
    running = 0
    for s in stops[1:]:
        running += int(s.get('elevation_gain') or 0)
        elev_pts.append({'mi': float(s.get('distance_miles') or 0), 'cum': running})
    max_elev_ft = max(p['cum'] for p in elev_pts) or 1

    # Light transitions — find sunset (day 0) and sunrise (day 1) crossing miles
    night_mi_from = find_transition_mi(sunset_min, 0)
    night_mi_to = find_transition_mi(sunrise_min, 1)

    # Callouts — pick the most dangerous range in each category
    callouts = []

    # Wind callout: longest run of high-wind (head ≥10 or any ≥15 mph) segments
    hot_runs = []
    cur_run = None
    for seg in segments:
        is_hot = seg['wind_intense'] or (seg['wind_label'] == 'Head' and seg['wind_mph'] >= 10) or seg['wind_mph'] >= 15
        if is_hot:
            if cur_run is None:
                cur_run = {'from': seg['mi_from'], 'to': seg['mi_to'], 'max_mph': seg['wind_mph']}
            else:
                cur_run['to'] = seg['mi_to']
                cur_run['max_mph'] = max(cur_run['max_mph'], seg['wind_mph'])
        elif cur_run is not None:
            hot_runs.append(cur_run)
            cur_run = None
    if cur_run is not None:
        hot_runs.append(cur_run)
    if hot_runs:
        longest = max(hot_runs, key=lambda r: r['to'] - r['from'])
        callouts.append({
            'tag': 'WIND', 'color': '#dc2626',
            'lead': f"Mile {longest['from']:.0f}–{longest['to']:.0f}:",
            'body': f"sustained wind to {longest['max_mph']} mph. Pack a vest and hydrate.",
        })

    # Dark callout: number of dark hours
    if night_mi_from is not None and night_mi_to is not None and night_mi_to > night_mi_from:
        dark_hours = ((night_mi_to - night_mi_from) / total_mi) * 100 if total_mi else 0
        # More useful — derive actual hours from the time difference
        dark_min = (24 * 60 - sunset_min) + sunrise_min  # sunset → sunrise spanning midnight
        dark_h = dark_min / 60
        callouts.append({
            'tag': 'DARK', 'color': '#312e81',
            'lead': f"{sunset_str} → {sunrise_str}:",
            'body': f"~{dark_h:.1f} hours of night riding. Charge lights, layer up before sundown.",
        })

    # Bank callout: tightest stop
    tightest = min((s for s in stops if s.get('time_bank_min') is not None),
                   key=lambda s: s['time_bank_min'], default=None)
    if tightest is not None and tightest.get('time_bank_min', 0) < 90:
        tb = tightest['time_bank_min']
        sign = '+' if tb >= 0 else '-'
        tb_str = f"{sign}{abs(tb)//60}:{abs(tb)%60:02d}"
        loc = tightest.get('location') or tightest.get('name') or 'a control'
        loc_short = loc.split('—')[0].strip()[:42]
        callouts.append({
            'tag': 'BANK', 'color': '#ea580c' if tb < 60 else '#ca8a04',
            'lead': f"Tightest at {loc_short}:",
            'body': f"only {tb_str} cushion against the ACP cutoff. Keep stops short here.",
        })

    return {
        'has_data': True,
        'sunrise_str': sunrise_str, 'sunset_str': sunset_str,
        'sunrise_min': sunrise_min, 'sunset_min': sunset_min,
        'segments': segments,
        'elev_pts': elev_pts,
        'max_elev_ft': max_elev_ft,
        'night_mi_from': night_mi_from,
        'night_mi_to': night_mi_to,
        'callouts': callouts,
    }


def _weather_summary_from_stop_wind(stop_wind, stops):
    """Build a small weather summary dict the v2 template consumes.

    fetch_stop_wind / compute_stop_winds return dicts with `temperature_f`,
    `wind_speed_mph`, `wind_type`, and `label` keys.
    """
    if not stop_wind:
        return {
            'temp_low': None, 'temp_high': None,
            'wind_max': None, 'sunrise': None, 'sunset': None,
            'headwind_segs': 0, 'crosswind_segs': 0,
        }
    temps_f = [sw.get('temperature_f') for sw in stop_wind
               if sw and sw.get('temperature_f') is not None]
    speeds_mph = [sw.get('wind_speed_mph') or 0 for sw in stop_wind if sw]

    def is_kind(sw, kind):
        wt = (sw.get('wind_type') or sw.get('label') or '').lower()
        return kind in wt

    head_count = sum(1 for sw in stop_wind if sw and is_kind(sw, 'head'))
    cross_count = sum(1 for sw in stop_wind if sw and is_kind(sw, 'cross'))

    return {
        'temp_low': int(min(temps_f)) if temps_f else None,
        'temp_high': int(max(temps_f)) if temps_f else None,
        'wind_max': int(round(max(speeds_mph))) if speeds_mph else None,
        'sunrise': None,  # not currently surfaced by fetch_stop_wind
        'sunset': None,
        'headwind_segs': head_count,
        'crosswind_segs': cross_count,
    }
