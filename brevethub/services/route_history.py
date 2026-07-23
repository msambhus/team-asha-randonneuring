"""services/route_history.py — same-route per-segment historical baseline.

Pure, presentation-agnostic computation of a rider's AVERAGE per-segment
actuals across their PRIOR finished rides ON THE SAME ROUTE (same base plan).

Used to tell a rider, at each control/waypoint, how their time on the ride
being analyzed compares to their *typical* time on that route, e.g.
"you were 2 min slower / 0.4 mph faster at this control than usual".

The single public entry point, `compute_same_route_segment_baseline`, reuses
the same building blocks the rest of the app uses so the mobile/web surfaces
cannot drift:

  * `models.get_rider_rides_with_cached_streams` — the rider's finished rides
    that have cached Strava streams.
  * `services.plan_match.match_plan` — resolve a ride to its base plan by
    route-name when the FK is missing (FK-first, then name match — exactly how
    the app decides a ride is "on the same route").
  * `services.strava_analysis.build_comparison` — per-segment actuals for one
    ride, keyed by plan-stop `location`.

The function NEVER raises: any failure (bad blob, short stream, missing
analysis, unexpected shape) is guarded and either skips the offending ride or
returns {} overall.  It is pure — the CALLER is responsible for caching, since
it can decode up to `max_rides` streams and run `build_comparison` per ride.
"""
import json
import zlib

import models
from services import plan_match, strava_analysis


def _decompress_streams(blob):
    """Decompress a BYTEA blob back to a streams dict.

    Mirrors services.strava_analysis._decompress_streams so we read the cached
    `activity_streams` exactly the way the analysis pipeline wrote it.
    """
    return json.loads(zlib.decompress(bytes(blob)))


def _mean(values):
    """Mean of a list of numbers, or None if empty."""
    return (sum(values) / len(values)) if values else None


def _resolve_base_plan_id(row, base_plan_id, all_plans):
    """Resolve a ride row's base plan id the SAME way the app does.

    FK-first: if the ride already links to `base_plan_id`, use it.  Otherwise
    fall back to route-name matching via services.plan_match.match_plan (the
    shared web/mobile matcher).  Returns True if this ride is on the same route
    as `base_plan_id`.
    """
    ride_plan_id = row.get('ride_plan_id')
    if ride_plan_id is not None and ride_plan_id == base_plan_id:
        return True
    try:
        matched = plan_match.match_plan(row.get('ride_name'), all_plans)
    except Exception:
        return False
    return bool(matched) and matched.get('id') == base_plan_id


def _build_activity_dict(row):
    """Build the `activity` dict build_comparison reads from a ride row.

    build_comparison only ever calls `activity.get(...)`.  It reads:
      distance, moving_time, elapsed_time, total_elevation_gain, average_speed,
      average_heartrate, max_heartrate, average_watts, max_watts,
      weighted_average_watts, kilojoules, suffer_score, has_heartrate,
      device_watts.
    We populate the ones present on the cached-streams row; the row uses
    `strava_distance_m` for the Strava distance in meters.
    """
    return {
        'distance': row.get('strava_distance_m'),
        'moving_time': row.get('moving_time'),
        'elapsed_time': row.get('elapsed_time'),
        'total_elevation_gain': row.get('total_elevation_gain'),
        'average_speed': row.get('average_speed'),
        'average_heartrate': row.get('average_heartrate'),
        'max_heartrate': row.get('max_heartrate'),
        'average_watts': row.get('average_watts'),
        'weighted_average_watts': row.get('weighted_average_watts'),
        'suffer_score': row.get('suffer_score'),
        'has_heartrate': bool(row.get('has_heartrate')),
        'device_watts': bool(row.get('device_watts')),
    }


def _segment_actuals_for_ride(row, stops):
    """Per-segment actuals for ONE prior ride, keyed by plan-stop location.

    Returns a dict:
        { location: {'segment_min': float|None, 'speed_mph': float|None,
                     'watts': float|None, 'cadence': float|None}, ... }
    or None if the ride can't produce usable data (skip it).
    """
    blob = row.get('activity_streams')
    if not blob:
        return None
    try:
        streams = _decompress_streams(blob)
    except Exception:
        return None
    if not streams or not streams.get('distance'):
        return None

    # Detected stops from cached analysis (JSONB → already a list).
    detected = []
    try:
        analysis = models.get_strava_ride_analysis(row.get('match_id'))
        if analysis:
            detected = analysis.get('detected_stops') or []
    except Exception:
        detected = []

    activity = _build_activity_dict(row)

    try:
        comparison = strava_analysis.build_comparison(
            plan_stops=stops,
            detected_stops=detected,
            activity=activity,
            streams=streams,
        )
    except Exception:
        return None

    rows = (comparison or {}).get('rows') or []
    out = {}
    for r in rows:
        if r.get('is_extra'):
            continue
        location = r.get('location')
        if not location:
            continue
        out[location] = {
            'segment_min': r.get('actual_segment_min'),
            'speed_mph': r.get('actual_speed_mph'),
            'watts': r.get('actual_avg_watts'),
            'cadence': r.get('actual_avg_cadence'),
        }
    return out or None


def compute_same_route_segment_baseline(rider_id, base_plan_id,
                                        exclude_ride_id=None, max_rides=8):
    """Average per-segment actuals across a rider's prior same-route rides.

    For every plan waypoint (keyed by its `location` string) of `base_plan_id`,
    compute the mean of the rider's per-segment actuals across their PRIOR
    FINISHED rides on the SAME route (same base plan), so a caller can compare
    the ride being analyzed against the rider's typical performance.

    Args:
        rider_id: rider whose history to aggregate.
        base_plan_id: the ride's base plan; defines "same route".  Falsy → {}.
        exclude_ride_id: ride to leave out (typically the one being analyzed).
        max_rides: cap on prior rides used (most recent first).

    Returns:
        dict keyed by plan-stop `location` string:
          { location: {'avg_segment_min': float, 'avg_speed_mph': float,
                       'avg_watts': int|None, 'avg_cadence': int|None,
                       'n_rides': int}, ... }
        A location is OMITTED if no prior ride produced a segment_min for it.
        Returns {} when base_plan_id is falsy, there are no prior same-route
        rides with usable data, or on ANY failure (this never raises).
    """
    if not base_plan_id:
        return {}

    try:
        candidate_rows = models.get_rider_rides_with_cached_streams(rider_id) or []
    except Exception:
        return {}

    try:
        all_plans = models.get_all_ride_plans() or []
    except Exception:
        all_plans = []

    # 1. Filter to prior rides on the SAME route (FK-first then name match),
    #    excluding exclude_ride_id.
    same_route = []
    for row in candidate_rows:
        try:
            if exclude_ride_id is not None and row.get('ride_id') == exclude_ride_id:
                continue
            if _resolve_base_plan_id(row, base_plan_id, all_plans):
                same_route.append(row)
        except Exception:
            continue

    if not same_route:
        return {}

    # Sort by date desc, cap to max_rides.  (The query already orders by date
    # desc, but re-sort defensively in case the caller-provided rows aren't.)
    def _date_key(row):
        return row.get('date')
    try:
        same_route.sort(key=_date_key, reverse=True)
    except Exception:
        pass
    if max_rides and max_rides > 0:
        same_route = same_route[:max_rides]

    # 2. Shared plan stops — same route → same waypoints for every prior ride.
    try:
        stops = models.get_ride_plan_stops(base_plan_id) or []
    except Exception:
        return {}
    if not stops:
        return {}

    # 3. Per-ride per-segment actuals, accumulated per location.
    accum = {}  # location -> {'segment_min': [], 'speed_mph': [], 'watts': [], 'cadence': []}
    for row in same_route:
        per_ride = _segment_actuals_for_ride(row, stops)
        if not per_ride:
            continue
        for location, metrics in per_ride.items():
            seg = metrics.get('segment_min')
            if seg is None:
                # n_rides counts rides that contributed a segment_min, so a
                # location with no segment_min for this ride does not count.
                continue
            bucket = accum.setdefault(
                location,
                {'segment_min': [], 'speed_mph': [], 'watts': [], 'cadence': []},
            )
            bucket['segment_min'].append(seg)
            if metrics.get('speed_mph') is not None:
                bucket['speed_mph'].append(metrics['speed_mph'])
            if metrics.get('watts') is not None:
                bucket['watts'].append(metrics['watts'])
            if metrics.get('cadence') is not None:
                bucket['cadence'].append(metrics['cadence'])

    # 4. Aggregate: mean per metric, round; n_rides = # rides with a segment_min.
    result = {}
    for location, bucket in accum.items():
        seg_vals = bucket['segment_min']
        if not seg_vals:
            continue
        avg_speed = _mean(bucket['speed_mph'])
        avg_watts = _mean(bucket['watts'])
        avg_cadence = _mean(bucket['cadence'])
        result[location] = {
            'avg_segment_min': round(_mean(seg_vals), 1),
            'avg_speed_mph': round(avg_speed, 1) if avg_speed is not None else None,
            'avg_watts': int(round(avg_watts)) if avg_watts is not None else None,
            'avg_cadence': int(round(avg_cadence)) if avg_cadence is not None else None,
            'n_rides': len(seg_vals),
        }

    return result
