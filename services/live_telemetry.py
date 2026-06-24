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


def haversine_m(lat1, lng1, lat2, lng2):
    """Great-circle distance between two points, in meters."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def project_to_route(lat, lng, track):
    """Nearest point on the route to (lat,lng).

    `track` is a list of {lat,lng,dist_m} (ascending dist_m). Returns
    (dist_m, index) of the closest track point, or (None, None) if empty.
    """
    if not track:
        return None, None
    best_i, best_d = 0, float('inf')
    for i, tp in enumerate(track):
        d = haversine_m(lat, lng, tp['lat'], tp['lng'])
        if d < best_d:
            best_d, best_i = d, i
    return track[best_i]['dist_m'], best_i


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
        if dt <= 0:
            continue
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
