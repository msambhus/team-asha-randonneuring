"""Pure functions for live per-rider brevet telemetry.

Everything here is I/O-free: callers pass in the rider's position(s) and a
pre-built (cached) per-ride *context* (route geometry, cumulative
distance/ascent, wind-by-distance, plan stop times). That keeps the heavy
route/weather work out of the per-poll path and makes every function trivially
unit-testable.

Units: distances internally in meters; ascent in feet (matching RWGPS/plan
conventions); speeds in m/s; times in minutes.
"""
import math

from .rwgps import _compute_difficulty_score
from .control_times import control_close_time_minutes

METERS_TO_MILES = 1 / 1609.344
METERS_TO_FEET = 3.28084
KMH_TO_MPH = 0.621371

# Below this ground speed (m/s) a rider is considered stopped (~1.8 km/h).
STOPPED_SPEED_MS = 0.5
CURRENT_STOP_RADIUS_M = 50.0

# A rider farther than this (m) from the nearest route point is "off route" —
# we then suppress route-relative metrics rather than snap to a bogus distance.
ON_ROUTE_MAX_M = 800

# Within this gap (s) we trust a point's reported speed for moving/stopped. For
# LONGER gaps (no telemetry — e.g. a cell-signal dropout on a remote brevet) we
# instead classify by the average speed implied by how far the rider actually
# moved between the two fixes, so real riding through a dead zone still counts as
# moving rather than being dropped.
MAX_GAP_SECONDS = 600

# Upper sanity bound (m/s ≈ 72 km/h) for bridging a long gap: above this the
# implied speed is a vehicle / a session resumed elsewhere / a GPS jump, not
# cycling — so we don't count that gap at all (prevents inflating moving time).
MAX_PLAUSIBLE_SPEED_MS = 20.0

# A long gap only counts as moving when the displacement implies a genuine
# riding pace (≈ 9 km/h, the walk/cycle boundary), not the bare not-stopped
# floor — so incidental drift during a long rest stays "stopped", not "moving".
BRIDGE_MOVING_SPEED_MS = 2.5


def haversine_m(lat1, lng1, lat2, lng2):
    """Great-circle distance between two points, in meters."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lng1, lat2, lng2):
    """Forward bearing in degrees [0, 360) from point 1 to point 2."""
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    d_lng = math.radians(lng2 - lng1)
    x = math.sin(d_lng) * math.cos(lat2_r)
    y = (math.cos(lat1_r) * math.sin(lat2_r)
         - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(d_lng))
    return math.degrees(math.atan2(x, y)) % 360


def angle_diff_deg(a, b):
    """Smallest absolute difference between two bearings, in [0, 180]."""
    d = abs((a - b) % 360)
    return d if d <= 180 else 360 - d


def course_over_ground(points, min_move_m=15.0):
    """The rider's recent travel bearing (deg) from their position history.

    `points` is oldest→newest [{lat,lng,...}]. Walks back from the latest fix to
    the most recent earlier fix at least `min_move_m` away and returns the bearing
    between them. Returns None when the rider hasn't moved far enough to have a
    reliable heading (stopped, or GPS jitter) — callers then fall back to plain
    nearest-point projection.
    """
    if not points or len(points) < 2:
        return None
    latest = points[-1]
    for p in reversed(points[:-1]):
        if haversine_m(p['lat'], p['lng'], latest['lat'], latest['lng']) >= min_move_m:
            return bearing_deg(p['lat'], p['lng'], latest['lat'], latest['lng'])
    return None


def _initial_heading(points, min_move_m=15.0):
    """Bearing (deg) LEAVING the first fix: from points[0] to the first later fix
    at least min_move_m away. The mirror of course_over_ground (which reads the
    latest heading) — used to disambiguate the seed leg at a loop start, where the
    rider heads out along the route. None until the rider has moved far enough."""
    if not points or len(points) < 2:
        return None
    try:
        flat, flng = float(points[0]['lat']), float(points[0]['lng'])
    except (TypeError, ValueError, KeyError):
        return None
    for p in points[1:]:
        try:
            lat, lng = float(p['lat']), float(p['lng'])
        except (TypeError, ValueError, KeyError):
            continue
        if haversine_m(flat, flng, lat, lng) >= min_move_m:
            return bearing_deg(flat, flng, lat, lng)
    return None


# How far beyond the single nearest point to still consider a route point a
# candidate leg (m). Overlapping out-and-back legs on the same road sit within a
# few meters of each other, so a modest margin captures both.
_CANDIDATE_RADIUS_MARGIN_M = 150.0
# A candidate leg is only preferred over plain nearest when the rider's heading
# agrees with the route tangent to at least this cosine (~ within 90°).
_MIN_DIRECTION_ALIGN = 0.0


def project_to_route(lat, lng, track, heading_deg=None):
    """Project the rider onto the route, optionally disambiguated by direction.

    `track` is a list of {lat,lng,dist_m} (ascending dist_m). Returns
    (dist_m, index, off_by_m) for the chosen track point, where off_by_m is how
    far the rider is from that point — callers use it to detect off-route.

    With no `heading_deg`, returns the globally nearest point (legacy behavior).
    When the rider's `heading_deg` (course over ground) is given, the route
    points near the rider are treated as candidate *legs*; the one whose forward
    tangent best matches the rider's heading wins. This stops an out-and-back or
    looped route — where the return leg runs alongside the outbound leg — from
    snapping the rider onto the leg going the other way and reporting a bogus
    distance. Falls back to nearest when no direction-consistent leg is close.

    Returns (None, None, None) if the track is empty.
    """
    if not track:
        return None, None, None

    n = len(track)
    dists = [haversine_m(lat, lng, tp['lat'], tp['lng']) for tp in track]
    nearest_i = min(range(n), key=lambda i: dists[i])
    if heading_deg is None or n < 2:
        return track[nearest_i]['dist_m'], nearest_i, dists[nearest_i]

    # Candidate legs = local minima of distance-to-rider within a radius of the
    # nearest point (so both overlapping legs of an out-and-back qualify).
    radius = dists[nearest_i] + _CANDIDATE_RADIUS_MARGIN_M
    candidates = []
    for i in range(n):
        if dists[i] > radius:
            continue
        left = dists[i - 1] if i > 0 else float('inf')
        right = dists[i + 1] if i + 1 < n else float('inf')
        if dists[i] <= left and dists[i] <= right:
            candidates.append(i)
    if not candidates:
        return track[nearest_i]['dist_m'], nearest_i, dists[nearest_i]

    # Pick the candidate whose forward route tangent best agrees with heading.
    best_i, best_align = None, None
    for i in candidates:
        a = i if i + 1 < n else i - 1
        b = i + 1 if i + 1 < n else i
        tangent = bearing_deg(track[a]['lat'], track[a]['lng'],
                              track[b]['lat'], track[b]['lng'])
        align = math.cos(math.radians(angle_diff_deg(heading_deg, tangent)))
        if best_align is None or align > best_align:
            best_align, best_i = align, i

    if best_i is None or best_align is None or best_align < _MIN_DIRECTION_ALIGN:
        return track[nearest_i]['dist_m'], nearest_i, dists[nearest_i]
    return track[best_i]['dist_m'], best_i, dists[best_i]


# Temporal projection windows. Each step searches the route only within
# [cur − back, cur + fwd] of along-route distance, so the match follows the
# rider's continuous path instead of snapping to a far overlapping leg.
_PROJ_BACK_WINDOW_M = 300.0    # allow stepping back this far (GPS noise / brief reversal)
_PROJ_MIN_FWD_M = 3000.0       # min forward window per step (covers a downsample gap)
_PROJ_MAX_SPEED_MS = 20.0      # forward window grows with elapsed time at this cap


def project_history_to_route(history, track, with_start=False):
    """Project the rider's whole trajectory onto the route, in time order.

    `history` is oldest→newest [{lat,lng,recorded_at}]; `track` is
    [{lat,lng,dist_m}] ascending. Carries the matched route position forward —
    each fix is matched only within a window ahead of (and a little behind) the
    previous match — so a route that passes the same place more than once
    (out-and-back / loop) resolves to the leg the rider is actually on, and the
    returned distance is monotonic (never less than a position already reached).

    The walk is SEEDED on the first cleanly on-route fix (skipping off-route
    warm-up fixes), disambiguated by the rider's initial heading — so a loop whose
    start coincides with its finish seeds on the OUTBOUND leg the rider is actually
    on, not the finish vertex a few metres away. This seed is also the rider's
    START position on the route (see route_start_offset_m), so distance-done and
    the start offset come from ONE consistent match.

    Returns (dist_m, index, off_by_m): dist_m is the monotonic distance-done,
    index the track point for it (for ascent/wind splits), off_by_m how far the
    LATEST fix is from the route (for on-route detection). When `with_start` is
    True, returns (dist_m, index, off_by_m, start_dist_m, start_index) with the
    seed's along-route distance and index appended. All-None on empty input.
    """
    empty = (None, None, None, None, None) if with_start else (None, None, None)
    if not track or not history:
        return empty
    n = len(track)
    # Seed on the first on-route fix (skipping off-route warm-up fixes), picking
    # the leg that agrees with the rider's initial heading. Fall back to the first
    # fix's best match when nothing is cleanly on-route yet.
    cur = off_by = seed_pos = None
    for i, p in enumerate(history):
        try:
            lat, lng = float(p['lat']), float(p['lng'])
        except (TypeError, ValueError, KeyError):
            continue
        _, si, soff = project_to_route(lat, lng, track,
                                       heading_deg=_initial_heading(history[i:]))
        if si is None:
            continue
        if cur is None:                      # first usable fix = fallback seed
            cur, off_by, seed_pos = si, soff, i
        if soff is not None and soff <= ON_ROUTE_MAX_M:
            cur, off_by, seed_pos = si, soff, i
            break
    if cur is None:
        return empty
    start_idx, start_dist = cur, track[cur]['dist_m']
    best_dist, best_idx = track[cur]['dist_m'], cur
    prev_t = history[seed_pos]['recorded_at']
    for p in history[seed_pos + 1:]:
        dt = (p['recorded_at'] - prev_t).total_seconds()
        prev_t = p['recorded_at']
        if dt <= 0:
            continue
        lat, lng = float(p['lat']), float(p['lng'])
        cur_dist = track[cur]['dist_m']
        hi = cur_dist + max(_PROJ_MIN_FWD_M, _PROJ_MAX_SPEED_MS * dt)
        lo = cur_dist - _PROJ_BACK_WINDOW_M
        best_i, best_d = cur, float('inf')
        for i in range(cur, n):              # forward within the window
            if track[i]['dist_m'] > hi:
                break
            d = haversine_m(lat, lng, track[i]['lat'], track[i]['lng'])
            if d < best_d:
                best_d, best_i = d, i
        for i in range(cur - 1, -1, -1):     # limited backward within the window
            if track[i]['dist_m'] < lo:
                break
            d = haversine_m(lat, lng, track[i]['lat'], track[i]['lng'])
            if d < best_d:
                best_d, best_i = d, i
        cur, off_by = best_i, best_d
        if track[cur]['dist_m'] > best_dist:
            best_dist, best_idx = track[cur]['dist_m'], cur
    if with_start:
        return best_dist, best_idx, off_by, start_dist, start_idx
    return best_dist, best_idx, off_by


def activity_from_speed(speed_ms):
    """Classify movement from ground speed (m/s): paused/walking/cycling/driving."""
    if speed_ms is None:
        return None
    if speed_ms < STOPPED_SPEED_MS:
        return 'paused'
    if speed_ms < 2.5:        # < ~9 km/h
        return 'walking'
    if speed_ms < 12.0:       # < ~43 km/h
        return 'cycling'
    return 'driving'


def current_stop_duration_min(points, radius_m=CURRENT_STOP_RADIUS_M):
    """Minutes represented by the trailing stationary fix cluster, or None."""
    usable = [p for p in (points or [])
              if p.get('recorded_at') is not None
              and p.get('lat') is not None and p.get('lng') is not None]
    if not usable:
        return None
    usable.sort(key=lambda p: p['recorded_at'])
    latest = usable[-1]
    latest_speed = latest.get('speed')
    try:
        if latest_speed is not None and float(latest_speed) >= STOPPED_SPEED_MS:
            return None
    except (TypeError, ValueError):
        pass
    arrived_at = latest['recorded_at']
    for point in reversed(usable[:-1]):
        try:
            if haversine_m(float(point['lat']), float(point['lng']),
                           float(latest['lat']), float(latest['lng'])) > radius_m:
                break
            speed = point.get('speed')
            if speed is not None and float(speed) >= STOPPED_SPEED_MS:
                break
        except (TypeError, ValueError):
            break
        arrived_at = point['recorded_at']
    duration = (latest['recorded_at'] - arrived_at).total_seconds() / 60.0
    return round(max(0.0, duration), 1)


def stationary_periods(points, min_duration_min=2.0,
                       radius_m=CURRENT_STOP_RADIUS_M):
    """Observed stationary periods, including completed intermediate stops."""
    usable = [p for p in (points or [])
              if p.get('recorded_at') is not None
              and p.get('lat') is not None and p.get('lng') is not None]
    usable.sort(key=lambda p: p['recorded_at'])
    periods, active = [], None
    for first, second in zip(usable, usable[1:]):
        seconds = (second['recorded_at'] - first['recorded_at']).total_seconds()
        if seconds <= 0:
            continue
        distance = haversine_m(float(first['lat']), float(first['lng']),
                               float(second['lat']), float(second['lng']))
        speeds = []
        for point in (first, second):
            try:
                if point.get('speed') is not None:
                    speeds.append(float(point['speed']))
            except (TypeError, ValueError):
                pass
        stationary = (distance <= radius_m
                      and (not speeds or max(speeds) < STOPPED_SPEED_MS))
        if stationary:
            if active is None:
                active = {'start_at': first['recorded_at'],
                          'end_at': second['recorded_at'],
                          'lat': float(second['lat']), 'lng': float(second['lng'])}
            else:
                active['end_at'] = second['recorded_at']
                active['lat'], active['lng'] = float(second['lat']), float(second['lng'])
        elif active is not None:
            periods.append(active)
            active = None
    if active is not None:
        periods.append(active)
    out = []
    for period in periods:
        duration = (period['end_at'] - period['start_at']).total_seconds() / 60.0
        if duration >= min_duration_min:
            out.append(dict(period, duration_min=round(duration, 1)))
    return out


def remaining_distance_m(total_dist_m, current_dist_m):
    """Meters left on the route (never negative)."""
    if total_dist_m is None or current_dist_m is None:
        return None
    return max(0.0, total_dist_m - current_dist_m)


# Minimum along-route distance (m) at a rider's first on-route fix before we
# treat it as a mid-route "start offset". A loop permanent can be begun partway
# round (rider starts at, say, route-mile 5 and finishes back there), so "distance
# done" must be measured from their own start, not the route file's mile 0. Below
# this threshold the rider effectively started at mile 0 and NO offset is applied —
# so every ordinary ride is unaffected.
START_OFFSET_MIN_M = 800.0   # ~0.5 mi


def route_start_offset_m(ride_history, track):
    """Along-route distance (m) at the rider's start on the route — where they
    joined it. For a loop permanent begun partway round this is > 0 and the live
    "distance done" must be measured from here (wrapping the loop), not from the
    route's mile 0.

    Uses the SAME leg-aware trajectory walk as the distance projection
    (project_history_to_route's seed) so the start point and the current position
    are matched consistently — never by an independent stateless match that could
    snap to the wrong overlapping leg. Returns (offset_m, index), or (0.0, 0) when
    there's no on-route fix yet or the offset is below START_OFFSET_MIN_M (an
    ordinary mile-0 start, so no offset is applied)."""
    _, _, _, start_dist, start_idx = project_history_to_route(
        ride_history, track, with_start=True)
    if start_dist is None or start_dist < START_OFFSET_MIN_M:
        return 0.0, 0
    return start_dist, (start_idx or 0)


def distance_progressed_m(current_dist_m, start_offset_m, total_dist_m):
    """Distance (m) travelled since the rider's OWN start on the route, wrapping a
    loop. `current`/`start` are absolute along-route distances; a rider who began
    at start_offset progresses (current − start), and on a loop a position that has
    wrapped past the finish adds the route total. With no offset this is just
    current_dist_m (an ordinary mile-0 start)."""
    if current_dist_m is None:
        return None
    if not start_offset_m:
        return current_dist_m
    prog = current_dist_m - start_offset_m
    if prog < 0 and total_dist_m:
        prog += total_dist_m
    return max(0.0, prog)


def ascent_split(cum_ascent_ft, index, total_ascent_ft):
    """(done_ft, left_ft) given a cumulative-ascent array and current index."""
    if not cum_ascent_ft or index is None:
        return None, None
    done = cum_ascent_ft[min(index, len(cum_ascent_ft) - 1)]
    left = max(0.0, (total_ascent_ft or cum_ascent_ft[-1]) - done)
    return round(done), round(left)


def ascent_progressed_split(cum_ascent_ft, start_index, cur_index, total_ascent_ft):
    """(done_ft, left_ft) for ascent climbed since the rider's start index, wrapping
    a loop. Like ascent_split but measures the arc start_index→cur_index rather than
    0→cur_index, so a mid-route loop start reports climbing done on THEIR ride, not
    from the route file's mile 0. With start_index 0 this equals ascent_split."""
    if not cum_ascent_ft or cur_index is None:
        return None, None
    n = len(cum_ascent_ft)
    ci = min(cur_index, n - 1)
    si = min(start_index or 0, n - 1)
    total = total_ascent_ft or cum_ascent_ft[-1]
    if ci >= si:
        done = cum_ascent_ft[ci] - cum_ascent_ft[si]
    else:   # wrapped past the finish
        done = (total - cum_ascent_ft[si]) + cum_ascent_ft[ci]
    done = max(0.0, done)
    left = max(0.0, total - done)
    return round(done), round(left)


def plan_time_at(dist_miles, plan_stops):
    """Expected riding timeline, from prior departure to next arrival."""
    if not plan_stops or dist_miles is None:
        return None
    pts = sorted((dict(s) for s in plan_stops
                  if s.get('distance_miles') is not None
                  and s.get('cum_time_min') is not None),
                 key=lambda s: float(s['distance_miles']))
    if not pts:
        return None
    if dist_miles <= float(pts[0]['distance_miles']):
        return float(pts[0]['cum_time_min'])
    if dist_miles >= float(pts[-1]['distance_miles']):
        return float(pts[-1]['cum_time_min'])
    for previous, upcoming in zip(pts, pts[1:]):
        d0, d1 = float(previous['distance_miles']), float(upcoming['distance_miles'])
        if d0 <= dist_miles < d1:
            t0 = float(previous['cum_time_min'])
            t1 = float(upcoming.get('arrival_time_min')
                       if upcoming.get('arrival_time_min') is not None
                       else upcoming['cum_time_min'])
            frac = (dist_miles - d0) / (d1 - d0) if d1 > d0 else 0
            return t0 + frac * (t1 - t0)
    return float(pts[-1]['cum_time_min'])


def rebase_plan_stops(plan_stops, start_offset_miles, total_miles):
    """Re-express the plan in the rider's frame for a mid-route (loop) start.

    A loop permanent begun partway round means the rider rides the plan ROTATED:
    their start = 0, distances wrap the loop, and each control's expected time is
    measured as elapsed since THEIR start. This lets the ordinary plan_delta /
    next_control / finish_stop logic work on a rider who did not start at the plan's
    mile 0 — feed them (progressed_distance, rebased_stops).

    Returns stops sorted by rebased distance, with a synthetic finish at the rider's
    OWN start point (a full loop later) so comparison spans their whole ride. With
    no offset it returns the stops unchanged.
    """
    if not plan_stops or not start_offset_miles or not total_miles:
        return plan_stops
    t_start = plan_time_at(start_offset_miles, plan_stops) or 0.0
    # Time wraps over one loop. total_time = the plan's max cumulative time, which on
    # a loop permanent is the finish node's time (start==finish node) = the full-loop
    # duration — the correct wrap period.
    total_time = max((float(s['cum_time_min']) for s in plan_stops
                      if s.get('cum_time_min') is not None), default=0.0)
    out = []
    for s in plan_stops:
        d, ct = s.get('distance_miles'), s.get('cum_time_min')
        if d is None or ct is None:
            continue
        d_r = float(d) - start_offset_miles
        if d_r < 0:
            d_r += total_miles
        c_r = float(ct) - t_start
        if c_r < 0:
            c_r += total_time
        stop = dict(s)
        stop['distance_miles'] = round(d_r, 2)
        stop['cum_time_min'] = round(c_r)
        ar = s.get('arrival_time_min')
        if ar is not None:
            a_r = float(ar) - t_start
            if a_r < 0:
                a_r += total_time
            stop['arrival_time_min'] = round(a_r)
        # The plan's own start/finish (the loop point) is just a waypoint mid-ride
        # for this rider — demote it so it isn't treated as their finish.
        if (stop.get('stop_type') or '').lower() in ('start', 'finish'):
            stop['stop_type'] = 'control'
        out.append(stop)
    out.sort(key=lambda x: x['distance_miles'])
    # On a loop the plan's start-node and finish-node share a point, so both wrap to
    # the same rider-distance — collapse such duplicates (keep the one with a location).
    deduped = []
    for s in out:
        if deduped and abs(s['distance_miles'] - deduped[-1]['distance_miles']) < 0.01:
            if not deduped[-1].get('location') and s.get('location'):
                deduped[-1] = s
            continue
        deduped.append(s)
    out = deduped
    # The rider finishes back at their OWN start after the full loop — a point the
    # plan (start/finish at the loop node) doesn't list. Add it so the finish metric
    # and the last leg of the plan comparison span the rider's whole ride.
    out.append({'distance_miles': round(total_miles, 2),
                'cum_time_min': round(total_time),
                'arrival_time_min': round(total_time),
                'location': 'Start / Finish', 'stop_type': 'finish'})
    return out


def headwinds_split(wind_by_dist, current_dist_m):
    """Average headwind (km/h) over the done vs remaining route portions.

    `wind_by_dist` is a list of {dist_m, headwind_kmh}. Positive = headwind,
    negative = tailwind. Returns (done_kmh, ahead_kmh); either may be None.
    """
    if not wind_by_dist or current_dist_m is None:
        return None, None
    done = [w['headwind_kmh'] for w in wind_by_dist if w['dist_m'] <= current_dist_m]
    ahead = [w['headwind_kmh'] for w in wind_by_dist if w['dist_m'] > current_dist_m]
    done_avg = round(sum(done) / len(done), 1) if done else None
    ahead_avg = round(sum(ahead) / len(ahead), 1) if ahead else None
    return done_avg, ahead_avg


def crosswinds_split(wind_by_dist, current_dist_m):
    """Average crosswind (km/h) over the done vs remaining route portions.

    Mirrors headwinds_split for the {dist_m, crosswind_kmh} component (positive =
    from the rider's right, negative = from the left). Tolerates entries without a
    crosswind_kmh key (legacy cached context). Returns (done_kmh, ahead_kmh).
    """
    if not wind_by_dist or current_dist_m is None:
        return None, None
    done = [w['crosswind_kmh'] for w in wind_by_dist
            if w.get('crosswind_kmh') is not None and w['dist_m'] <= current_dist_m]
    ahead = [w['crosswind_kmh'] for w in wind_by_dist
             if w.get('crosswind_kmh') is not None and w['dist_m'] > current_dist_m]
    done_avg = round(sum(done) / len(done), 1) if done else None
    ahead_avg = round(sum(ahead) / len(ahead), 1) if ahead else None
    return done_avg, ahead_avg


def toughness_remaining(ascent_left_ft, remaining_m):
    """Toughness (Tuf) score 0–10 for the remaining segment, via the plan model."""
    remaining_mi = (remaining_m or 0) * METERS_TO_MILES
    if remaining_mi <= 0:
        return 0.0
    ft_per_mi = (ascent_left_ft or 0) / remaining_mi
    return _compute_difficulty_score(ft_per_mi)


def plan_delta(current_dist_miles, elapsed_min, plan_stops):
    """Minutes ahead (+) or behind (−) the plan at the rider's current distance.

    `plan_stops` is a list of {distance_miles, cum_time_min} (ascending). The
    expected elapsed time at the rider's distance is linearly interpolated, then
    compared to actual elapsed. Returns None if there's no usable plan.
    """
    if not plan_stops or current_dist_miles is None or elapsed_min is None:
        return None
    usable = [s for s in plan_stops
              if s.get('distance_miles') is not None and s.get('cum_time_min') is not None]
    if len(usable) < 2:
        return None
    expected = plan_time_at(current_dist_miles, usable)
    if expected is None:
        return None
    # expected − elapsed: positive means the rider reached this distance faster
    # than the plan (ahead of schedule / banking time).
    return round(expected - elapsed_min)


# Don't re-surface a control the rider is essentially standing at; the next one
# starts this far (miles) ahead of their current distance.
NEXT_CONTROL_EPS_MI = 0.1


def next_control(current_dist_miles, plan_stops):
    """The next plan stop ahead of the rider — for a live "next control + ETA".

    `plan_stops` is a list of stop dicts with at least distance_miles and
    cum_time_min, and (when available) location, stop_type and arrival_time_min.
    Returns the first stop strictly ahead of the rider's current distance,
    skipping the 'start': {location, stop_type, distance_miles, cum_time_min,
    arrival_time_min, dist_to_go_mi}, or None when there is no plan or nothing
    ahead (rider past the last stop).

    `arrival_time_min` (= cum_time_min − stop_duration_min) is the plan's REACHING
    time at the control, i.e. before the break there — the correct basis for the
    live "next control ETA". When a stop carries no arrival_time_min (legacy cached
    context) it falls back to cum_time_min so the ETA still resolves.
    """
    if not plan_stops or current_dist_miles is None:
        return None
    ahead = []
    for s in plan_stops:
        dm, ct = s.get('distance_miles'), s.get('cum_time_min')
        if dm is None or ct is None:
            continue
        if (s.get('stop_type') or '').lower() == 'start':
            continue
        dm = float(dm)
        if dm > current_dist_miles + NEXT_CONTROL_EPS_MI:
            ahead.append((dm, s))
    if not ahead:
        return None
    dm, s = min(ahead, key=lambda x: x[0])
    arrival = s.get('arrival_time_min')
    arrival = round(float(arrival)) if arrival is not None else round(float(s['cum_time_min']))
    return {
        'location': s.get('location') or None,
        'stop_type': s.get('stop_type') or None,
        'distance_miles': round(dm, 1),
        'cum_time_min': round(float(s['cum_time_min'])),
        'arrival_time_min': arrival,
        'dist_to_go_mi': round(max(0.0, dm - current_dist_miles), 1),
    }


def finish_stop(plan_stops):
    """The plan's FINISH — the farthest stop by distance (skipping 'start') — for the
    per-rider "speed to finish" metric (item 3). Returns
    {location, stop_type, distance_miles, arrival_time_min} or None when there's no
    usable plan. arrival_time_min falls back to cum_time_min for a legacy stop that
    carries no arrival time."""
    if not plan_stops:
        return None
    cands = []
    for s in plan_stops:
        dm, ct = s.get('distance_miles'), s.get('cum_time_min')
        if dm is None or ct is None:
            continue
        if (s.get('stop_type') or '').lower() == 'start':
            continue
        cands.append((float(dm), s))
    if not cands:
        return None
    dm, s = max(cands, key=lambda x: x[0])
    arrival = s.get('arrival_time_min')
    arrival = round(float(arrival)) if arrival is not None else round(float(s['cum_time_min']))
    return {
        'location': s.get('location') or None,
        'stop_type': s.get('stop_type') or None,
        'distance_miles': round(dm, 1),
        'arrival_time_min': arrival,
    }


def required_speed_mph(dist_to_go_mi, arrival_time_min, elapsed_min):
    """Average speed (mph) the rider must hold to reach the next control at the
    plan's SCHEDULED ARRIVAL time. Returns (required_mph, behind):

      - required_mph: dist_to_go_mi / ((arrival_time_min − elapsed_min)/60),
        rounded; None when an input is missing or the window is non-positive.
      - behind: True when the plan's arrival time has already passed
        (arrival_time_min − elapsed_min ≤ 0), so a renderer shows an em-dash /
        "behind" indicator rather than a divide-by-zero or a negative speed.
    """
    if dist_to_go_mi is None or arrival_time_min is None or elapsed_min is None:
        return None, False
    window_min = arrival_time_min - elapsed_min
    if window_min <= 0:
        return None, True
    return round(dist_to_go_mi / (window_min / 60.0), 1), False


def time_banked_cutoff_min(current_dist_miles, elapsed_min, total_mi, cutoff_hours,
                           event_distance_km=None):
    """Minutes in hand vs the brevet CUTOFF (OTL margin) at the rider's current
    distance: the interpolated cutoff clock there minus elapsed. Positive = margin
    before going over the time limit.

    For 1000/1200 km events, the cutoff follows the official piecewise long-brevet
    schedule; shorter events retain the existing linear pro-rata calculation.
    Returns None when the ride has no cutoff or the plan distance is unknown/zero.
    """
    if (current_dist_miles is None or elapsed_min is None
            or not cutoff_hours or not total_mi or total_mi <= 0):
        return None
    cutoff_at_dist = control_close_time_minutes(
        current_dist_miles, total_mi, cutoff_hours,
        event_distance_km=event_distance_km,
    )
    return round(cutoff_at_dist - elapsed_min)


def grade_at(track, index, min_window_m=140.0):
    """Signed % grade of the route profile around a track index (climb positive).

    `track` is the downsampled route [{dist_m, e_m, ...}]. Averages the slope over
    the smallest window spanning at least `min_window_m` centered on `index`, so a
    single short/noisy segment doesn't dominate the reading. Returns a float
    percent, or None when the route carries no elevation (e_m) at that point.
    """
    if not track or index is None or len(track) < 2:
        return None

    def _has(p):
        return p.get('e_m') is not None and p.get('dist_m') is not None

    n = len(track)
    i = max(0, min(index, n - 1))
    if not _has(track[i]):
        return None
    lo = hi = i
    # Expand symmetrically until the span covers min_window_m (or we hit an end).
    while (track[hi]['dist_m'] - track[lo]['dist_m']) < min_window_m and (lo > 0 or hi < n - 1):
        if lo > 0:
            lo -= 1
        if hi < n - 1:
            hi += 1
    if not (_has(track[lo]) and _has(track[hi])):
        return None
    run_m = track[hi]['dist_m'] - track[lo]['dist_m']
    if run_m <= 0:
        return None
    rise_m = track[hi]['e_m'] - track[lo]['e_m']
    return round((rise_m / run_m) * 100, 1)


def moving_stopped(points):
    """(moving_min, stopped_min) from an ordered position history.

    Each consecutive interval counts as moving when the rider's ground speed in
    that interval is at/above STOPPED_SPEED_MS, else stopped.

    For a normal-cadence interval (<= MAX_GAP_SECONDS) we trust the point's
    reported `speed`, falling back to displacement/time. For a LONGER gap (a
    telemetry dropout) the reported speed is meaningless, so we classify by the
    average speed implied by the straight-line displacement between the two
    fixes: a riding pace (>= BRIDGE_MOVING_SPEED_MS) → the rider was moving
    through a dead zone (counts as moving); slower (incidental drift during a
    rest) → stopped. An implausibly high implied speed (> MAX_PLAUSIBLE_SPEED_MS:
    a drive, a resumed session, a GPS jump) is not counted at all, so a
    stale/teleported point can't inflate moving time.
    """
    if not points or len(points) < 2:
        return 0.0, 0.0
    moving_s = stopped_s = 0.0
    for a, b in zip(points, points[1:]):
        dt = (b['recorded_at'] - a['recorded_at']).total_seconds()
        if dt <= 0:
            continue
        if dt <= MAX_GAP_SECONDS:
            speed = b.get('speed')
            if speed is None:
                speed = haversine_m(float(a['lat']), float(a['lng']),
                                    float(b['lat']), float(b['lng'])) / dt
            is_moving = speed >= STOPPED_SPEED_MS
        else:
            implied = haversine_m(float(a['lat']), float(a['lng']),
                                  float(b['lat']), float(b['lng'])) / dt
            if implied > MAX_PLAUSIBLE_SPEED_MS:
                continue   # vehicle / resumed elsewhere / GPS jump — don't count
            is_moving = implied >= BRIDGE_MOVING_SPEED_MS
        if is_moving:
            moving_s += dt
        else:
            stopped_s += dt
    return round(moving_s / 60.0, 1), round(stopped_s / 60.0, 1)


def build_trail(history, track, max_points=40):
    """Breadcrumb of where the rider actually rode, as [[lng,lat], ...].

    Downsamples `history` (oldest→newest) to at most `max_points`. When a route
    `track` is given, points that are off-route (> ON_ROUTE_MAX_M from the line)
    are dropped, so a rider's off-route wandering never draws a spurious trail.
    """
    if not history:
        return []
    n = len(history)
    step = max(1, n // max_points)
    idxs = list(range(0, n, step))
    if idxs[-1] != n - 1:
        idxs.append(n - 1)   # always include the most recent point
    out = []
    for i in idxs:
        p = history[i]
        try:
            lat = float(p['lat'])
            lng = float(p['lng'])
        except (TypeError, ValueError, KeyError):
            continue
        if track:
            _, _, off_by_m = project_to_route(lat, lng, track)
            if off_by_m is not None and off_by_m > ON_ROUTE_MAX_M:
                continue
        out.append([lng, lat])
    return out


def latest_speed_ms(points):
    """Most recent usable ground speed (m/s), reported or derived; None if N/A."""
    if not points:
        return None
    last = points[-1]
    if last.get('speed') is not None:
        try:
            return float(last['speed'])
        except (TypeError, ValueError):
            pass
    if len(points) >= 2:
        a, b = points[-2], points[-1]
        dt = (b['recorded_at'] - a['recorded_at']).total_seconds()
        if dt > 0:
            return haversine_m(float(a['lat']), float(a['lng']),
                               float(b['lat']), float(b['lng'])) / dt
    return None
