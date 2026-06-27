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

from services.rwgps import _compute_difficulty_score

METERS_TO_MILES = 1 / 1609.344
METERS_TO_FEET = 3.28084

# Below this ground speed (m/s) a rider is considered stopped (~1.8 km/h).
STOPPED_SPEED_MS = 0.5

# A rider farther than this (m) from the nearest route point is "off route" —
# we then suppress route-relative metrics rather than snap to a bogus distance.
ON_ROUTE_MAX_M = 800

# Gaps longer than this (s) between consecutive points are NOT counted toward
# moving/stopped time — they mean we simply had no data, not that the rider was
# riding for that whole span (prevents a stale point from inflating moving time).
MAX_GAP_SECONDS = 600


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


def remaining_distance_m(total_dist_m, current_dist_m):
    """Meters left on the route (never negative)."""
    if total_dist_m is None or current_dist_m is None:
        return None
    return max(0.0, total_dist_m - current_dist_m)


def ascent_split(cum_ascent_ft, index, total_ascent_ft):
    """(done_ft, left_ft) given a cumulative-ascent array and current index."""
    if not cum_ascent_ft or index is None:
        return None, None
    done = cum_ascent_ft[min(index, len(cum_ascent_ft) - 1)]
    left = max(0.0, (total_ascent_ft or cum_ascent_ft[-1]) - done)
    return round(done), round(left)


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
    pts = sorted(
        ((float(s['distance_miles']), float(s['cum_time_min']))
         for s in plan_stops
         if s.get('distance_miles') is not None and s.get('cum_time_min') is not None),
        key=lambda x: x[0],
    )
    if len(pts) < 2:
        return None
    d = current_dist_miles
    if d <= pts[0][0]:
        expected = pts[0][1]
    elif d >= pts[-1][0]:
        expected = pts[-1][1]
    else:
        expected = pts[-1][1]
        for (d0, t0), (d1, t1) in zip(pts, pts[1:]):
            if d0 <= d <= d1:
                frac = (d - d0) / (d1 - d0) if d1 > d0 else 0
                expected = t0 + frac * (t1 - t0)
                break
    # expected − elapsed: positive means the rider reached this distance faster
    # than the plan (ahead of schedule / banking time).
    return round(expected - elapsed_min)


def moving_stopped(points):
    """(moving_min, stopped_min) from an ordered position history.

    Each consecutive interval counts as moving when the rider's ground speed in
    that interval is at/above STOPPED_SPEED_MS, else stopped. Uses a point's
    reported `speed` when present, otherwise derives it from displacement/time.
    """
    if not points or len(points) < 2:
        return 0.0, 0.0
    moving_s = stopped_s = 0.0
    for a, b in zip(points, points[1:]):
        dt = (b['recorded_at'] - a['recorded_at']).total_seconds()
        if dt <= 0 or dt > MAX_GAP_SECONDS:
            continue   # ignore non-positive and large data gaps
        speed = b.get('speed')
        if speed is None:
            dist = haversine_m(float(a['lat']), float(a['lng']),
                               float(b['lat']), float(b['lng']))
            speed = dist / dt
        if speed >= STOPPED_SPEED_MS:
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
