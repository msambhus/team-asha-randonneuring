"""Shared Strava ride-analysis core — framework-free and DB-free.

The club-agnostic half of the Team Asha ride-analysis engine: stream
(de)compression, stop detection + coalescing/backfill, the per-stop map payload,
the distance→time interpolator, and stream summary. Every function here is pure —
it consumes decoded Strava streams (plain dicts of index-aligned lists) and returns
plain data. Nothing reads a Flask app global or imports a Team Asha module, so both
the Team Asha app (through the ``services/strava_analysis.py`` shim) and BrevetHub
import one implementation.

The Team-Asha-specific pieces (ride↔activity matching, plan-vs-actual comparison,
cohort/brevet overlays, the Strava HTTP fetch) stay in ``services/strava_analysis.py``
— they need the plan, the ``strava_activity`` DB rows, or Flask config. This module
is only the reusable engine.

``tests/brevethub/test_shared_isolation.py`` fails the build if this module ever
imports a Team Asha module or reaches for Flask's ``current_app``.
"""

import bisect
import json
import zlib


# ── Stream compression helpers ──────────────────────────────────────

def _compress_streams(streams_dict):
    """Compress a streams dict to bytes for BYTEA storage."""
    return zlib.compress(json.dumps(streams_dict).encode(), level=6)


def _decompress_streams(blob):
    """Decompress BYTEA blob back to a streams dict."""
    return json.loads(zlib.decompress(bytes(blob)))


# ── Analysis constants ──────────────────────────────────────────────

# Stop detection constants
VELOCITY_THRESHOLD = 0.5   # m/s (~1 mph) - below this = stopped
MIN_STOP_DURATION = 120    # seconds (2 minutes) - ignore shorter stops
METERS_PER_MILE = 1609.34
METERS_PER_KM = 1000

# A single physical stop can be split into several when velocity briefly rises
# above threshold (GPS jitter, rolling a few feet, re-parking). Consecutive
# stops separated by only this much MOVING time are treated as one.
STOP_MERGE_GAP_S = 120  # seconds

STOP_ABSORPTION_RADIUS_MILES = 0.3  # unmatched stops within this radius of a matched waypoint are absorbed

# The Strava activity-streams the analysis consumes, as the API's `keys` param.
_STREAM_KEYS = 'time,distance,velocity_smooth,heartrate,watts,cadence,altitude,grade_smooth,latlng'


def _latlng_at(latlng, idx):
    """Return (lat, lng) at a stream index, or (None, None) when unavailable.

    The ``latlng`` stream is index-aligned with velocity/time/distance. Returns
    None coordinates when the stream is absent, the index is out of range, or the
    point is malformed — callers treat those stops as having no map position.
    """
    if not latlng or idx < 0 or idx >= len(latlng):
        return None, None
    pt = latlng[idx]
    if isinstance(pt, (list, tuple)) and len(pt) >= 2 and pt[0] is not None and pt[1] is not None:
        try:
            return round(float(pt[0]), 6), round(float(pt[1]), 6)
        except (TypeError, ValueError):
            return None, None
    return None, None


def detect_stops(streams):
    """Detect stoppages from velocity and distance streams.

    Walks velocity_smooth array. When velocity < threshold for > min_duration,
    records a stop with its distance position, duration, and (when the latlng
    stream is present) the lat/lng where the stop began.

    Returns:
        list of dicts: [{distance_miles, start_time_s, duration_s, duration_min,
        lat, lng}]  (lat/lng are None when no latlng stream is available)
    """
    velocity = streams.get('velocity_smooth', [])
    distance = streams.get('distance', [])
    time_arr = streams.get('time', [])
    latlng = streams.get('latlng', [])

    if not velocity or not distance or not time_arr:
        return []

    stops = []
    in_stop = False
    stop_start_idx = 0

    for i in range(len(velocity)):
        if velocity[i] < VELOCITY_THRESHOLD:
            if not in_stop:
                in_stop = True
                stop_start_idx = i
        else:
            if in_stop:
                duration_s = time_arr[i] - time_arr[stop_start_idx]
                if duration_s >= MIN_STOP_DURATION:
                    lat, lng = _latlng_at(latlng, stop_start_idx)
                    stops.append({
                        'distance_miles': round(distance[stop_start_idx] / METERS_PER_MILE, 1),
                        'start_time_s': time_arr[stop_start_idx],
                        'duration_s': duration_s,
                        'duration_min': round(duration_s / 60, 1),
                        'lat': lat,
                        'lng': lng,
                    })
                in_stop = False

    # Handle stop at end of ride (finish)
    if in_stop and len(time_arr) > 0:
        duration_s = time_arr[-1] - time_arr[stop_start_idx]
        if duration_s >= MIN_STOP_DURATION:
            lat, lng = _latlng_at(latlng, stop_start_idx)
            stops.append({
                'distance_miles': round(distance[stop_start_idx] / METERS_PER_MILE, 1),
                'start_time_s': time_arr[stop_start_idx],
                'duration_s': duration_s,
                'duration_min': round(duration_s / 60, 1),
                'lat': lat,
                'lng': lng,
            })

    return _coalesce_stops(stops)


def _coalesce_stops(stops):
    """Merge consecutive stops separated by only a brief moving gap.

    Prevents one long stop from showing as several (e.g. a 25-min control break
    split into 15+3+7 min entries at the same mile). Consecutive stops whose gap
    ``next.start_time_s - prev_real_end`` is <= ``STOP_MERGE_GAP_S`` are merged.

    The merged ``duration_s`` is the SUM of the members' true stopped durations
    — the brief moving blips *between* them are NOT counted as stopped time, so
    downstream segment riding-time / speed math (and enroute-break detection,
    which compares against below-threshold samples only) stays consistent. The
    merged entry keeps the earliest stop's position/coords and adopts a
    matched-waypoint identity only when the earlier stop has none (so a control
    is never downgraded to an extra, and two *different* matched controls are
    never collapsed).

    Idempotent: re-running on already-merged stops is a no-op.
    """
    if not stops:
        return stops
    ordered = sorted(
        stops,
        key=lambda s: (s.get('start_time_s') is None, s.get('start_time_s') or 0),
    )
    merged = []
    last_end = None  # real clock end of the current merged group (for the gap check)
    for s in ordered:
        st = s.get('start_time_s')
        dur = s.get('duration_s') or 0
        if (merged and st is not None and last_end is not None
                and st - last_end <= STOP_MERGE_GAP_S):
            prev = merged[-1]
            pn, sn = prev.get('matched_stop_name'), s.get('matched_stop_name')
            if not (pn and sn and pn != sn):  # never collapse two distinct controls
                prev['duration_s'] = (prev.get('duration_s') or 0) + dur
                prev['duration_min'] = round(prev['duration_s'] / 60, 1)
                if sn and not pn:
                    prev['matched_stop_name'] = sn
                    prev['matched_stop_type'] = s.get('matched_stop_type')
                    prev['is_extra'] = s.get('is_extra', False)
                last_end = max(last_end, st + dur)
                continue
        merged.append(dict(s))
        last_end = (st + dur) if st is not None else last_end
    return merged


def _nearest_time_index(time_arr, t):
    """Index of the stream sample whose time is closest to ``t`` (monotonic arr)."""
    if not time_arr:
        return None
    i = bisect.bisect_left(time_arr, t)
    if i <= 0:
        return 0
    if i >= len(time_arr):
        return len(time_arr) - 1
    return i if abs(time_arr[i] - t) < abs(time_arr[i - 1] - t) else i - 1


def _backfill_stop_coords(stops, streams):
    """Fill lat/lng on stops that lack them, from the streams (by start_time_s).

    Stops analyzed before they carried coordinates (older cached rows) have
    ``lat``/``lng`` None; map each stop's ``start_time_s`` to the nearest stream
    time index and read its latlng so the map can pin them — no re-analysis,
    non-destructive (computed at render, not persisted).
    """
    if not stops:
        return stops
    latlng = streams.get('latlng') or []
    time_arr = streams.get('time') or []
    if not latlng or not time_arr:
        return stops
    for s in stops:
        if s.get('lat') is not None and s.get('lng') is not None:
            continue
        t = s.get('start_time_s')
        if t is None:
            continue
        idx = _nearest_time_index(time_arr, t)
        if idx is not None:
            lat, lng = _latlng_at(latlng, idx)
            s['lat'], s['lng'] = lat, lng
    return stops


def _segment_thumbnails(track, segments, stops=None, width=100, height=60,
                        pad=6, max_track=90, max_seg=60):
    """Precompute tiny inline-SVG thumbnails, one per planned segment.

    Each thumbnail draws that segment's GPS shape (bold) over the faint full
    track (context), in a SINGLE shared projection so every row is comparable.
    Longitude is scaled by cos(mean latitude) so the shape isn't horizontally
    stretched, then the track is fit into the box preserving aspect ratio.

    Returns ``{'viewbox', 'track' (SVG points str), 'segments': {location:
    points str}, 'pins': {location: "x,y"}, 'stop_pins': {dist_key: "x,y"}}`` or
    ``None`` when there's no usable track. ``pins`` marks each planned segment's
    end point (the stop it arrives at); ``stop_pins`` marks each unplanned stop's
    location (keyed by its distance in miles to 1 decimal, matching the note key
    used in the template). All values share the one projection, so a pin lands
    exactly on the track it belongs to. Point strings are plain "x,y x,y …"
    numbers (safe to inline in an SVG ``points`` attribute).
    """
    import math
    if not track or len(track) < 2:
        return None

    mean_lat = sum(p[0] for p in track) / len(track)
    kx = math.cos(math.radians(mean_lat)) or 1e-6

    def raw(p):                       # (x from lng, y from lat)
        return (p[1] * kx, p[0])

    xs = [p[1] * kx for p in track]
    ys = [p[0] for p in track]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    xspan = (xmax - xmin) or 1e-9
    yspan = (ymax - ymin) or 1e-9
    scale = min((width - 2 * pad) / xspan, (height - 2 * pad) / yspan)
    ox = (width - xspan * scale) / 2
    oy = (height - yspan * scale) / 2

    def proj(p):
        x, y = raw(p)
        px = ox + (x - xmin) * scale
        py = oy + (ymax - y) * scale  # flip: SVG y grows downward
        # Clamp into the viewbox: the bbox is derived from the (downsampled)
        # track, so a full-resolution segment point at an extreme could land a
        # hair outside; clamping keeps every drawn point inside the frame.
        px = min(max(px, 0.0), width)
        py = min(max(py, 0.0), height)
        return f"{round(px, 1)},{round(py, 1)}"

    def downsample(pts, cap):
        if len(pts) <= cap:
            return pts
        step = math.ceil(len(pts) / cap)
        out = pts[::step]
        if out[-1] != pts[-1]:
            out.append(pts[-1])
        return out

    def to_str(pts):
        return ' '.join(proj(p) for p in pts)

    seg_thumbs = {}
    pins = {}
    for seg in segments or []:
        loc = seg.get('location')
        pts = seg.get('points')
        if loc and pts and len(pts) >= 2:
            seg_thumbs[loc] = to_str(downsample(pts, max_seg))
            # Pin the segment's arrival point (the stop it ends at).
            pins[loc] = proj(pts[-1])

    # Pin each unplanned stop at its own coordinate, keyed by distance (miles, 1
    # decimal) to match the template's stop-note key.
    stop_pins = {}
    for ds in stops or []:
        lat, lng = ds.get('lat'), ds.get('lng')
        dist = ds.get('distance_miles')
        if lat is None or lng is None or dist is None:
            continue
        stop_pins[f"{float(dist):.1f}"] = proj([lat, lng])

    return {
        'viewbox': f"0 0 {width} {height}",
        'track': to_str(downsample(track, max_track)),
        'segments': seg_thumbs,
        'pins': pins,
        'stop_pins': stop_pins,
    }


def build_map_data(streams, comparison, detected_stops, max_points=500,
                   max_segment_points=120):
    """Build a compact map payload for the ride-analysis page.

    Assembles, from the decoded Strava streams and the plan-vs-actual
    comparison:
      - ``track``:  the full GPS polyline, downsampled to ``max_points``.
      - ``segments``: per planned segment, its sub-polyline plus the segment's
        ``actual_speed_mph`` (from ``comparison['rows']``) for map colouring.
      - ``stops``: each detected stop that has coordinates, with its distance,
        duration, and matched label (informational markers; notes live in the
        segment table + an overall note, not on the map).
      - ``bounds``: [[min_lat, min_lng], [max_lat, max_lng]] for map fit.

    Best-effort: returns ``None`` when there is no usable ``latlng`` stream (old
    cached rows, or activities with no GPS), so the page renders without a map.
    """
    import math

    if not streams:
        return None
    latlng = streams.get('latlng') or []
    if len(latlng) < 2:
        return None

    n = len(latlng)

    def _pt(idx):
        lat, lng = _latlng_at(latlng, idx)
        return [lat, lng] if lat is not None and lng is not None else None

    # Downsample the full track for the base polyline.
    if n > max_points:
        step = math.ceil(n / max_points)
        idxs = list(range(0, n, step))
        if idxs[-1] != n - 1:
            idxs.append(n - 1)
    else:
        idxs = list(range(n))
    track = [p for p in (_pt(i) for i in idxs) if p]
    if len(track) < 2:
        return None

    lats = [p[0] for p in track]
    lngs = [p[1] for p in track]
    bounds = [[min(lats), min(lngs)], [max(lats), max(lngs)]]

    # Distance (miles) per stream index — used to slice segment sub-polylines.
    distance_m = streams.get('distance') or []
    dist_mi = ([d / METERS_PER_MILE for d in distance_m]
               if distance_m and len(distance_m) == n else None)

    segments = []
    rows = comparison.get('rows') if isinstance(comparison, dict) else None
    if dist_mi and rows:
        planned = sorted(
            (r for r in rows if not r.get('is_extra')),
            key=lambda r: r.get('distance_miles') or 0,
        )
        prev_dist = None
        for r in planned:
            cur_dist = r.get('distance_miles')
            if cur_dist is None:
                continue
            if prev_dist is None:
                prev_dist = cur_dist
                continue
            seg_idx = [i for i in range(n) if prev_dist <= dist_mi[i] <= cur_dist]
            if len(seg_idx) >= 2:
                if len(seg_idx) > max_segment_points:
                    st = math.ceil(len(seg_idx) / max_segment_points)
                    seg_idx = seg_idx[::st] + [seg_idx[-1]]
                pts = [p for p in (_pt(i) for i in seg_idx) if p]
                if len(pts) >= 2:
                    segments.append({
                        'location': r.get('location', ''),
                        'start_mi': round(prev_dist, 1),
                        'end_mi': round(cur_dist, 1),
                        'speed_mph': r.get('actual_speed_mph'),
                        'points': pts,
                    })
            prev_dist = cur_dist

    stops = []
    for i, ds in enumerate(detected_stops or []):
        lat = ds.get('lat')
        lng = ds.get('lng')
        if lat is None or lng is None:
            continue
        # Informational marker only — notes are no longer attached to map stops
        # (they live in the segment table + an overall note; see rider_notes).
        stops.append({
            'lat': lat,
            'lng': lng,
            'distance_miles': ds.get('distance_miles'),
            'duration_min': ds.get('duration_min'),
            'location': ds.get('matched_stop_name') or ds.get('location') or None,
        })

    return {
        'track': track,
        'bounds': bounds,
        'segments': segments,
        'stops': stops,
        'thumb': _segment_thumbnails(track, segments, stops),
    }


def _build_stream_interpolator(streams):
    """Build a function that interpolates elapsed time (minutes) at a given distance (miles).

    Uses Strava's distance (meters) and time (seconds) streams.
    Returns None if streams are unavailable.
    """
    if not streams:
        return None
    distance_m = streams.get('distance', [])
    time_s = streams.get('time', [])
    if not distance_m or not time_s or len(distance_m) != len(time_s):
        return None

    # Convert distance to miles once
    dist_miles = [d / METERS_PER_MILE for d in distance_m]

    def interpolate(target_miles):
        """Return elapsed time in minutes at the given distance in miles."""
        if target_miles <= 0:
            return 0.0
        if target_miles >= dist_miles[-1]:
            return time_s[-1] / 60.0
        # Binary search for bracket
        lo, hi = 0, len(dist_miles) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if dist_miles[mid] <= target_miles:
                lo = mid
            else:
                hi = mid
        # Linear interpolation
        d0, d1 = dist_miles[lo], dist_miles[hi]
        t0, t1 = time_s[lo], time_s[hi]
        if d1 == d0:
            return t0 / 60.0
        frac = (target_miles - d0) / (d1 - d0)
        return (t0 + frac * (t1 - t0)) / 60.0

    return interpolate


def _build_stream_summary(streams):
    """Build summary metrics from raw stream data."""
    summary = {}

    time_arr = streams.get('time', [])
    if time_arr:
        summary['total_time_s'] = time_arr[-1] if time_arr else 0

    distance = streams.get('distance', [])
    if distance:
        summary['total_distance_m'] = distance[-1] if distance else 0

    velocity = streams.get('velocity_smooth', [])
    if velocity:
        moving_velocities = [v for v in velocity if v > VELOCITY_THRESHOLD]
        if moving_velocities:
            summary['avg_moving_speed_mph'] = round(
                (sum(moving_velocities) / len(moving_velocities)) * 2.23694, 1
            )

    hr = streams.get('heartrate', [])
    if hr:
        summary['avg_hr'] = round(sum(hr) / len(hr), 1)
        summary['max_hr'] = max(hr)

    watts = streams.get('watts', [])
    if watts:
        non_zero = [w for w in watts if w > 0]
        if non_zero:
            summary['avg_watts'] = round(sum(non_zero) / len(non_zero), 1)
            summary['max_watts'] = max(non_zero)

    return summary


def _fmt_seconds(s):
    """Format seconds as 'Xh YYm' string."""
    if not s:
        return None
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    return f'{h}h {m:02d}m'
