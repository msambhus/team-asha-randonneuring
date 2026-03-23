# Phase 2: Stop-to-Coordinate Interpolation - Research

**Researched:** 2026-03-23
**Domain:** RWGPS track point structure, linear interpolation, unit conversion (miles-to-meters)
**Confidence:** HIGH

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| WIND-05 | System interpolates lat/lng coordinates for each ride plan stop by matching cumulative distance against RWGPS track points (converting miles to meters at boundary) | RWGPS track points use `d` (distance in meters), `y` (lat), `x` (lng). Stops in `ride_plan_stop` use `distance_miles`. Conversion: `miles * 1609.344 = meters`. Interpolation walks the sorted track list to find the two bounding track points, then linearly interpolates lat/lng. |
</phase_requirements>

## Summary

Phase 2 implements `get_stop_coordinates(stops, track_points)` — a pure function that takes a list of ride plan stops (each carrying `distance_miles`) and an RWGPS track point list, and returns a lat/lng for each stop by interpolating into the track geometry.

The core algorithm is a linear scan of the track point list. RWGPS track points are already sorted ascending by `d` (meters). For each stop, convert its `distance_miles` to meters using the constant `1609.344`, then walk the track until the stop falls between two consecutive points. Interpolate lat/lng proportionally based on position in that segment. Two boundary conditions matter: stops at exactly 0 meters snap to the first point; stops beyond the final track point are clamped to the last point (per the success criteria, not an error).

The unit conversion bug the success criteria explicitly guards against is the classic: multiplying miles by `1609.344` gives meters, but failing to do so leaves the stop distance in miles (a 40-mile stop at ~64 in "distance units" would land somewhere around km 64 on the track, which is 37 miles — dramatically wrong). The success criteria states a stop at 40.0 miles must be within 0.5 km of the correct track position, which validates the conversion is accurate.

**Primary recommendation:** Add `get_stop_coordinates(stops, track_points)` to `services/weather.py` in the `# Pure functions` block. Reuse the `METERS_TO_MILES` constant already in `services/rwgps.py` by defining its inverse `MILES_TO_METERS = 1609.344` at the top of `services/weather.py`. Write tests in `tests/test_weather.py` using the established class-per-function pattern.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib only | stdlib | Linear interpolation, list scan | No external dependency needed; pure arithmetic |
| `services/weather.py` | existing | Target module for new function | Established convention; all weather/wind math lives here |
| `tests/test_weather.py` | existing | Unit tests | Established test file for this module |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pytest` | existing | Test runner | All test execution |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Linear scan + interpolation | `bisect.bisect_left` | `bisect` is faster for large lists, but RWGPS tracks typically have 1,000–5,000 points and we have at most ~30 stops per plan — O(n) scan is fine and more readable |
| `MILES_TO_METERS = 1609.344` | Import from `rwgps.py` | Cross-module import for a single constant; define locally in `weather.py` to keep modules decoupled |

**Installation:** No new dependencies. All work uses existing imports.

## Architecture Patterns

### Recommended Project Structure
No new files needed. All additions go into existing files:

```
services/
└── weather.py      # Add MILES_TO_METERS constant + get_stop_coordinates()

tests/
└── test_weather.py # Add TestGetStopCoordinates class
```

### Pattern 1: Linear Interpolation into Sorted Track Points
**What:** Walk the track list once; for each stop, find the pair of track points that bracket the stop's distance, then interpolate lat/lng.
**When to use:** Mapping any distance-along-route to a geographic coordinate.
**Example:**
```python
# Source: Derived from existing _compute_segment_elevation pattern in services/rwgps.py
MILES_TO_METERS = 1609.344

def get_stop_coordinates(stops, track_points):
    """Return lat/lng for each stop by interpolating RWGPS track points.

    stops: list of dicts with 'distance_miles' key (from ride_plan_stop)
    track_points: list of RWGPS track dicts with y=lat, x=lng, d=distance_meters

    Returns list of {'lat': float, 'lng': float} in same order as stops.
    Stops beyond the end of the track are clamped to the final track point.
    """
    if not track_points:
        return [None] * len(stops)

    # Filter to points with valid coordinates
    valid = [tp for tp in track_points
             if tp.get('y') is not None and tp.get('x') is not None]
    if not valid:
        return [None] * len(stops)

    result = []
    for stop in stops:
        target_m = stop['distance_miles'] * MILES_TO_METERS

        # Clamp to final point if stop is beyond track end
        if target_m >= valid[-1]['d']:
            result.append({'lat': valid[-1]['y'], 'lng': valid[-1]['x']})
            continue

        # Clamp to first point if stop is at or before start
        if target_m <= valid[0]['d']:
            result.append({'lat': valid[0]['y'], 'lng': valid[0]['x']})
            continue

        # Find bounding segment via linear scan
        for i in range(1, len(valid)):
            if valid[i]['d'] >= target_m:
                prev, curr = valid[i - 1], valid[i]
                seg_len = curr['d'] - prev['d']
                if seg_len == 0:
                    result.append({'lat': curr['y'], 'lng': curr['x']})
                else:
                    t = (target_m - prev['d']) / seg_len
                    result.append({
                        'lat': prev['y'] + t * (curr['y'] - prev['y']),
                        'lng': prev['x'] + t * (curr['x'] - prev['x']),
                    })
                break

    return result
```

### Pattern 2: RWGPS Track Point Field Names
**What:** RWGPS API uses single-character shorthand keys for track point fields.
**When to use:** Whenever reading track points fetched by `fetch_route()`.

| RWGPS key | Meaning | Type |
|-----------|---------|------|
| `y` | latitude (WGS84) | float |
| `x` | longitude (WGS84) | float |
| `d` | cumulative distance from start in **meters** | float |
| `e` | elevation in **meters** | float |

This is already confirmed in `services/weather.py` (`sample_track_points` uses `y`, `x`, `d`) and `services/rwgps.py` (`_compute_segment_elevation` uses `d`, `e`).

### Anti-Patterns to Avoid
- **Using `distance_miles` directly as meters:** The stop's `distance_miles` value must be multiplied by `1609.344` before comparison with track `d` values (which are meters). Omitting this conversion places stops wildly off — a 40-mile stop would land at ~64 meters from the start instead of ~64,427 meters.
- **Assuming `bisect` on a non-array:** Track points are dicts. `bisect` requires a key function not available in Python's stdlib `bisect` — stick to the linear scan for clarity.
- **Returning an error for clamped stops:** Stops at exactly `total_distance_miles` (the finish) may exceed the track's final `d` value by a small rounding delta. Clamp silently; do not raise.
- **Mutating the `stops` list:** Return a new parallel list; never modify the input.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Geographic distance between two lat/lng points | Haversine formula from scratch | Not needed here | Phase 2 only needs linear interpolation on a 1D distance axis — `d` values in meters. Haversine is only needed if computing bearings (already done by `calculate_bearing()` in weather.py) |
| Coordinate projection | Spherical geometry | Not needed here | Linear interpolation on lat/lng works at brevet scale (up to 1200 km). Error is <0.01% over a 0.5 km segment. Randonneurs don't need sub-meter precision |

**Key insight:** The problem is purely 1D — find the stop's distance along the track, then linear-interpolate lat/lng. No spatial libraries needed.

## Common Pitfalls

### Pitfall 1: Miles vs. Meters Unit Mismatch
**What goes wrong:** `stop['distance_miles']` (e.g., `40.0`) is compared directly against `track_point['d']` (e.g., `64374.0` meters). The stop matches the wrong segment entirely — a 40-mile stop is placed at ~64 meters from the start.
**Why it happens:** The conversion constant `MILES_TO_METERS = 1609.344` is not applied.
**How to avoid:** Compute `target_m = stop['distance_miles'] * MILES_TO_METERS` as the first line of the per-stop logic. The unit conversion must happen before any comparison with `d` values.
**Warning signs:** Test for "stop at 40.0 miles within 0.5 km of correct position" — if lat/lng is near the start of the route, the unit conversion is missing.

### Pitfall 2: Off-by-One at Track Boundaries
**What goes wrong:** The finish stop's `distance_miles` equals the plan's `total_distance_miles`. Due to floating point rounding in RWGPS track construction, the last track point's `d` value may be slightly less than `stops[-1]['distance_miles'] * 1609.344`.
**Why it happens:** `total_distance_miles` in the plan is rounded to one decimal place (`round(total_dist_m * METERS_TO_MILES, 1)`), so converting back can exceed the actual final `d`.
**How to avoid:** Check `target_m >= valid[-1]['d']` before the scan loop, and clamp to the final point. This is the explicit success criterion: "Stops beyond the end of the track are clamped to the final track point rather than returning an error."
**Warning signs:** `IndexError` or `StopIteration` on the scan loop for the last stop.

### Pitfall 3: Track Points with None Coordinates
**What goes wrong:** Some RWGPS track points have `None` for lat (`y`) or lng (`x`) due to GPS dropouts.
**Why it happens:** GPS signal loss during recording leaves gaps; RWGPS preserves the distance `d` but nulls the coordinates.
**How to avoid:** Filter to `valid` points (non-None `y` and `x`) before the scan. This is the same pattern already used in `sample_track_points()` in `services/weather.py`.
**Warning signs:** `TypeError: unsupported operand type(s) for +: 'float' and 'NoneType'` during interpolation.

### Pitfall 4: Zero-Length Segment Divide-by-Zero
**What goes wrong:** Two consecutive track points have the same `d` value (duplicate distance). `t = (target_m - prev['d']) / seg_len` divides by zero.
**Why it happens:** RWGPS occasionally emits duplicate distance values for stationary GPS fixes.
**How to avoid:** Guard `if seg_len == 0: snap to curr point`. This is a cheap one-line check.

## Code Examples

Verified patterns from codebase:

### Existing Unit Conversion Constants (services/rwgps.py)
```python
# Source: services/rwgps.py line 10-11
METERS_TO_MILES = 1 / 1609.344
# Inverse needed in weather.py:
MILES_TO_METERS = 1609.344
```

### Existing Pattern: Track Point Field Access (services/weather.py)
```python
# Source: services/weather.py — sample_track_points(), line 169
valid = [p for p in track_points if p.get('y') is not None and p.get('x') is not None]
# ...
return {'lat': pt['y'], 'lng': pt['x'], 'distance_m': pt['d']}
```

### Existing Pattern: Segment Scan (services/rwgps.py)
```python
# Source: services/rwgps.py — _compute_segment_elevation(), line 232-236
for tp in track_points:
    d = tp.get('d', 0) or tp.get('distance', 0) or 0
    e = tp.get('e', 0) or tp.get('elevation', 0) or 0
    if start_dist_m <= d <= end_dist_m and e is not None and e > 0:
        segment_pts.append(e)
```

### Stop Structure (from build_ride_plan output in services/rwgps.py)
```python
# Source: services/rwgps.py — build_ride_plan(), line 368-383
stop = {
    'stop_order': i + 1,
    'location': ctrl['name'],
    'stop_type': ctrl['stop_type'],
    'distance_miles': dist_miles,   # <-- this is what we interpolate on
    # ...
}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| No coordinate-per-stop (Phase 1 and earlier) | `get_stop_coordinates()` returns lat/lng per stop | Phase 2 | Enables per-stop wind fetching in Phase 3 |
| Lat/lng only at 50km sample intervals | Lat/lng at each control/stop position | Phase 2 | More precise wind at actual stopping points |

## Open Questions

1. **Are RWGPS `d` values guaranteed to be sorted ascending?**
   - What we know: `build_ride_plan()` accesses track points in sequence and the existing `_compute_segment_elevation()` assumes ascending `d` values. `sample_track_points()` also relies on this.
   - What's unclear: The RWGPS API documentation does not formally guarantee ordering in the response JSON.
   - Recommendation: Treat as guaranteed based on consistent codebase usage. If defensive coding is desired, a one-line sort `valid.sort(key=lambda p: p['d'])` before the scan is cheap enough to add without performance concern.

2. **Where does `get_stop_coordinates()` live — `weather.py` or `rwgps.py`?**
   - What we know: The function takes RWGPS track points as input but its output (lat/lng) feeds the weather pipeline (Phase 3). Both modules could host it.
   - What's unclear: The best home for cross-cutting functions.
   - Recommendation: Place in `services/weather.py` alongside `sample_track_points()`, which already bridges RWGPS track data to the weather pipeline. This keeps the "track → weather coordinate" pipeline co-located.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing) |
| Config file | `pytest.ini` (project root) |
| Quick run command | `python3 -m pytest tests/test_weather.py -x -q` |
| Full suite command | `python3 -m pytest tests/ -x -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| WIND-05 | `get_stop_coordinates()` returns correct lat/lng for mid-route stops | unit | `python3 -m pytest tests/test_weather.py::TestGetStopCoordinates -x` | Wave 0 |
| WIND-05 | Stop at 40.0 miles is within 0.5 km of correct track position (unit conversion correct) | unit | `python3 -m pytest tests/test_weather.py::TestGetStopCoordinates::test_40_mile_stop_unit_conversion -x` | Wave 0 |
| WIND-05 | Stop beyond track end is clamped to final track point | unit | `python3 -m pytest tests/test_weather.py::TestGetStopCoordinates::test_beyond_track_end_clamped -x` | Wave 0 |
| WIND-05 | Empty track point list returns list of None | unit | `python3 -m pytest tests/test_weather.py::TestGetStopCoordinates::test_empty_track_returns_none -x` | Wave 0 |
| WIND-05 | Stop at exactly 0 miles returns first track point | unit | `python3 -m pytest tests/test_weather.py::TestGetStopCoordinates::test_start_stop_returns_first_point -x` | Wave 0 |
| WIND-05 | Track points with None lat/lng are skipped | unit | `python3 -m pytest tests/test_weather.py::TestGetStopCoordinates::test_skips_none_coordinates -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/test_weather.py -x -q`
- **Per wave merge:** `python3 -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_weather.py::TestGetStopCoordinates` — covers WIND-05 (class does not yet exist; add to existing file)

*(No new files needed — add class to existing `tests/test_weather.py`)*

## Sources

### Primary (HIGH confidence)
- `services/rwgps.py` (project codebase) — RWGPS track point field names (`y`, `x`, `d`, `e`), unit constant `METERS_TO_MILES = 1 / 1609.344`, existing scan patterns
- `services/weather.py` (project codebase) — `sample_track_points()` filter pattern (`y`/`x` None guard), established module home for track-to-coordinate functions
- `.planning/REQUIREMENTS.md` — WIND-05 spec: "converting miles to meters at boundary", "clamped to final track point"
- `.planning/phases/01-wind-math-foundation/01-RESEARCH.md` — confirmed project conventions (pure functions in weather.py, class-per-function test pattern)

### Secondary (MEDIUM confidence)
- `services/rwgps.py — build_ride_plan()` — confirms `distance_miles` is the stop field (rounded to 1 decimal via `round(ctrl['distance_m'] * METERS_TO_MILES, 1)`)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new libraries; everything is existing project stdlib
- Architecture: HIGH — function location, interface, and algorithm determined directly from codebase inspection
- Pitfalls: HIGH — unit mismatch, clamping, and None guards all directly observable from existing patterns and success criteria

**Research date:** 2026-03-23
**Valid until:** Stable — no external APIs involved; valid until RWGPS track format changes
