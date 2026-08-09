"""Advisory brevet evidence validation for BrevetHub organizers.

The engine never approves or disqualifies a rider.  It emits explainable checks
and one of three machine recommendations; an organizer records the final result.
"""
from __future__ import annotations

import hashlib
import io
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from brevethub.shared.fit_merge import read_fit

EARTH_M = 6_371_000.0
CORRIDOR_M = 500.0
CONTROL_RADIUS_M = 500.0
START_FINISH_RADIUS_M = 1_000.0


@dataclass
class TrackPoint:
    timestamp: datetime
    lat: float
    lng: float
    elevation_m: float | None = None
    distance_m: float | None = None
    speed_mps: float | None = None


@dataclass
class Check:
    code: str
    title: str
    result: str
    summary: str
    metrics: dict[str, Any] = field(default_factory=dict)
    map_segments: list[list[list[float]]] = field(default_factory=list)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _parse_time(value: str) -> datetime:
    value = value.strip().replace('Z', '+00:00')
    return _utc(datetime.fromisoformat(value))


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = math.radians(b[0] - a[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(min(1.0, math.sqrt(h)))


def parse_gpx(data: bytes) -> tuple[list[TrackPoint], dict[str, Any]]:
    root = ET.fromstring(data)
    points = []
    for node in root.iter():
        if not node.tag.endswith(('trkpt', 'rtept')):
            continue
        lat, lng = node.get('lat'), node.get('lon')
        time_node = next((c for c in node if c.tag.endswith('time')), None)
        ele_node = next((c for c in node if c.tag.endswith('ele')), None)
        if lat is None or lng is None or time_node is None or not time_node.text:
            continue
        points.append(TrackPoint(
            _parse_time(time_node.text), float(lat), float(lng),
            float(ele_node.text) if ele_node is not None and ele_node.text else None,
        ))
    if len(points) < 2:
        raise ValueError('GPX has fewer than two timestamped track points')
    creator = root.get('creator')
    return normalize_track(points), {'format': 'gpx', 'creator': creator}


def parse_fit(data: bytes) -> tuple[list[TrackPoint], dict[str, Any]]:
    activity = read_fit(data)
    points = []
    for record in activity.records:
        fields = record.fields
        lat, lng = fields.get('position_lat'), fields.get('position_long')
        if lat is None or lng is None:
            continue
        points.append(TrackPoint(
            _utc(record.timestamp), float(lat), float(lng),
            fields.get('enhanced_altitude', fields.get('altitude')),
            fields.get('distance'), fields.get('enhanced_speed', fields.get('speed')),
        ))
    if len(points) < 2:
        raise ValueError('FIT has fewer than two timestamped GPS records')
    metadata = {
        'format': 'fit', 'sport': str(activity.sport),
        'developer_fields': list(activity.dev_field_names or []),
    }
    try:
        import fitdecode
        with fitdecode.FitReader(io.BytesIO(data)) as reader:
            for frame in reader:
                if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                    continue
                if frame.name == 'file_id':
                    metadata.update({
                        'manufacturer': frame.get_value('manufacturer', fallback=None),
                        'product': frame.get_value('product_name', fallback=None)
                                   or frame.get_value('product', fallback=None),
                        'serial_number': frame.get_value('serial_number', fallback=None),
                    })
                elif frame.name == 'device_info' and not metadata.get('device'):
                    metadata['device'] = frame.get_value('device_type', fallback=None)
                elif frame.name in ('sport', 'session'):
                    metadata['sub_sport'] = frame.get_value('sub_sport', fallback=None)
    except Exception:
        pass  # Device metadata is additive; the already-parsed original remains valid.
    return normalize_track(points), {k: v for k, v in metadata.items() if v is not None}


def normalize_track(points: Iterable[TrackPoint]) -> list[TrackPoint]:
    """Order/dedupe recordings and reconstruct cumulative distance and speed."""
    ordered = sorted(points, key=lambda p: p.timestamp)
    out: list[TrackPoint] = []
    distance = 0.0
    for point in ordered:
        if out and point.timestamp == out[-1].timestamp and point.lat == out[-1].lat and point.lng == out[-1].lng:
            continue
        if out:
            delta = haversine((out[-1].lat, out[-1].lng), (point.lat, point.lng))
            seconds = max(0.001, (point.timestamp - out[-1].timestamp).total_seconds())
            distance += delta
            if point.speed_mps is None:
                point.speed_mps = delta / seconds
        point.distance_m = distance
        out.append(point)
    return out


def combine_recordings(recordings: Iterable[list[TrackPoint]]) -> list[TrackPoint]:
    """Combine device restarts as one chronological recording without time shifting."""
    return normalize_track(point for recording in recordings for point in recording)


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _nearest(point: TrackPoint, route: list[dict]) -> tuple[float, int]:
    best = (float('inf'), -1)
    for idx, route_point in enumerate(route):
        dist = haversine((point.lat, point.lng), (float(route_point['lat']), float(route_point['lng'])))
        if dist < best[0]:
            best = (dist, idx)
    return best


def _route_point_for_mile(route: list[dict], mile: float) -> dict | None:
    target = mile * 1609.344
    with_distance = [p for p in route if p.get('dist_m') is not None]
    if not with_distance:
        return None
    return min(with_distance, key=lambda p: abs(float(p['dist_m']) - target))


def _check(code, title, result, summary, metrics=None, segments=None):
    return Check(code, title, result, summary, metrics or {}, segments or [])


def validate_submission(*, points: list[TrackPoint], route: list[dict], controls: list[dict],
                        event: dict, official_start: datetime | None,
                        evidence_control_orders: set[int] | None = None,
                        source_metadata: dict | None = None,
                        duplicate_conflicts: list[dict] | None = None,
                        has_traditional_evidence: bool = False) -> tuple[str, list[Check]]:
    """Run explainable automated checks and return recommendation + checks."""
    evidence_control_orders = evidence_control_orders or set()
    source_metadata = source_metadata or {}
    checks: list[Check] = []
    required_km = float(event.get('distance_km') or 0)

    if not points:
        result = 'needs_review' if has_traditional_evidence else 'incomplete'
        checks.append(_check('track', 'GPS recording', result,
                             'Traditional proof supplied; organizer review is required.' if has_traditional_evidence
                             else 'No GPS track or traditional proof was supplied.'))
        checks.append(_check('control_evidence', 'Control evidence', result,
                             'Review the submitted card, receipts, photos, or checkpoint answers.' if has_traditional_evidence
                             else 'Required control evidence is missing.'))
        return result, checks

    points = normalize_track(points)

    first, last = points[0], points[-1]
    recorded_km = float(last.distance_m or 0) / 1000.0
    date_delta = abs((first.timestamp.date() - event['date']).days) if event.get('date') else 0
    event_ok = date_delta <= 1 and recorded_km >= required_km * 0.9
    checks.append(_check('event_match', 'Event match', 'clear' if event_ok else 'needs_review',
                         f'Activity starts {date_delta} day(s) from the event date and records {recorded_km:.1f} km.',
                         {'date_delta_days': date_delta, 'recorded_km': round(recorded_km, 2), 'required_km': required_km}))

    if route:
        start_d = haversine((first.lat, first.lng), (float(route[0]['lat']), float(route[0]['lng'])))
        finish_d = haversine((last.lat, last.lng), (float(route[-1]['lat']), float(route[-1]['lng'])))
        early_s = max(0, (official_start - first.timestamp).total_seconds()) if official_start else 0
        sf_ok = start_d <= START_FINISH_RADIUS_M and finish_d <= START_FINISH_RADIUS_M and early_s <= 60
        checks.append(_check('start_finish', 'Start and finish', 'clear' if sf_ok else 'needs_review',
                             f'Start is {start_d:.0f} m and finish is {finish_d:.0f} m from the official locations.' +
                             (f' Recording begins {early_s/60:.0f} minutes early.' if early_s > 60 else ''),
                             {'start_distance_m': round(start_d), 'finish_distance_m': round(finish_d), 'early_seconds': round(early_s)},
                             [[[first.lat, first.lng]], [[last.lat, last.lng]]] if not sf_ok else []))

    limit_h = float(event.get('time_limit_hours') or 0)
    if official_start is None or not limit_h:
        checks.append(_check('official_elapsed', 'Official elapsed time', 'incomplete',
                             'The official event start or applicable time limit is unavailable.',
                             {'limit_hours': limit_h or None}))
    else:
        elapsed_h = (last.timestamp - official_start).total_seconds() / 3600
        timing_ok = elapsed_h >= 0 and elapsed_h <= limit_h
        checks.append(_check('official_elapsed', 'Official elapsed time', 'clear' if timing_ok else 'needs_review',
                             f'{elapsed_h:.2f} hours from the official event start against a {limit_h:g}-hour limit.',
                             {'official_elapsed_hours': round(elapsed_h, 3), 'limit_hours': limit_h}))

    visited_indexes, visit_details, missing_controls, missing_locations, cursor = [], [], [], [], 0
    required_controls = [
        (int(control.get('stop_order', order)), control)
        for order, control in enumerate(controls)
        if 'control' in str(control.get('stop_type') or '').lower()
        or 'checkpoint' in str(control.get('stop_type') or '').lower()
    ]
    for order, control in required_controls:
        target = _route_point_for_mile(route, float(control.get('distance_miles') or 0)) if route else None
        found = None
        if target:
            for idx in range(cursor, len(points)):
                if haversine((points[idx].lat, points[idx].lng), (float(target['lat']), float(target['lng']))) <= CONTROL_RADIUS_M:
                    found = idx
                    break
        if found is None:
            missing_controls.append(order)
            if target:
                missing_locations.append([[float(target['lat']), float(target['lng'])]])
        else:
            visited_indexes.append(found)
            visit_details.append({'order': order, 'timestamp': points[found].timestamp.isoformat(),
                                  'distance_km': round(float(points[found].distance_m or 0) / 1000, 2)})
            cursor = found + 1
    controls_ok = not missing_controls and visited_indexes == sorted(visited_indexes)
    checks.append(_check('control_sequence', 'Control sequence', 'clear' if controls_ok else 'needs_review',
                         'All required controls were visited in order.' if controls_ok else f'Could not confirm controls in order: {missing_controls}.',
                         {'required': len(required_controls), 'confirmed': len(visited_indexes),
                          'visits': visit_details, 'missing_orders': missing_controls},
                         missing_locations))

    required_orders = {order for order, _ in required_controls}
    gps_evidence = required_orders - set(missing_controls)
    missing_evidence = sorted(required_orders - gps_evidence - evidence_control_orders)
    checks.append(_check('control_evidence', 'Control evidence', 'clear' if not missing_evidence else 'incomplete',
                         'Each control has GPS or uploaded proof.' if not missing_evidence else f'Additional proof is needed for control(s): {missing_evidence}.',
                         {'gps_visits': visit_details, 'uploaded_proof_orders': sorted(evidence_control_orders),
                          'missing_orders': missing_evidence}, missing_locations))

    if route:
        activity_sample = points[::max(1, len(points) // 2000)]
        official_sample = route[::max(1, len(route) // 500)]
        off, off_distances, nearest_indexes = [], [], []
        for p in activity_sample:
            distance, nearest_idx = _nearest(p, official_sample)
            nearest_indexes.append(nearest_idx)
            if distance > CORRIDOR_M:
                off.append(p)
                off_distances.append(distance)
        coverage_hits = 0
        for rp in official_sample:
            probe = TrackPoint(first.timestamp, float(rp['lat']), float(rp['lng']))
            if _nearest(probe, [{'lat': p.lat, 'lng': p.lng} for p in activity_sample])[0] <= CORRIDOR_M:
                coverage_hits += 1
        coverage = coverage_hits / max(1, len(official_sample))
        route_ok = coverage >= .95
        # Do not connect unrelated, isolated GPS samples into one giant line.
        # A sparse point just outside the corridor is normal GPS noise; only a
        # contiguous/material run is useful to an organizer reviewing a detour.
        off_indices = [i for i, p in enumerate(activity_sample)
                       if p in off]
        departure_groups = []
        for idx in off_indices:
            if not departure_groups or idx > departure_groups[-1][-1] + 1:
                departure_groups.append([idx])
            else:
                departure_groups[-1].append(idx)
        meaningful_groups = []
        for group in departure_groups:
            distances = [
                _nearest(activity_sample[i], official_sample)[0] for i in group
            ]
            # Two adjacent samples that immediately return to the route are
            # ordinary GPS jitter, not an actionable detour.
            if len(group) >= 3 or max(distances, default=0) > CORRIDOR_M * 2:
                meaningful_groups.append(group)
        segments = [
            [[activity_sample[i].lat, activity_sample[i].lng] for i in group[:250]]
            for group in meaningful_groups
        ]
        checks.append(_check('route_coverage', 'Route coverage', 'clear' if route_ok else 'needs_review',
                             f'{coverage:.1%} of sampled official route points are covered within {CORRIDOR_M:.0f} m.',
                             {'coverage_ratio': round(coverage, 4), 'corridor_m': CORRIDOR_M, 'off_route_samples': len(off)}, segments))
        shortcut = recorded_km < required_km * .98 or coverage < .90
        checks.append(_check('shortcut_detection', 'Shortcut detection', 'needs_review' if shortcut else 'clear',
                             'The recording may omit or materially shorten required route portions.' if shortcut else 'No material shortening is apparent.',
                             {'recorded_km': round(recorded_km, 2), 'required_km': required_km, 'coverage_ratio': round(coverage, 4)}))
        departure_runs = []
        for group in meaningful_groups:
            before = max(0, group[0] - 1)
            after = min(len(nearest_indexes) - 1, group[-1] + 1)
            route_delta = abs(nearest_indexes[after] - nearest_indexes[before])
            departure_runs.append({'samples': len(group),
                                   'returned_near_entry': route_delta <= 3})
        checks.append(_check('route_departures', 'Route departures', 'needs_review' if meaningful_groups else 'clear',
                             f'{len(departure_runs)} route departure(s) need review for an authorized detour and return to the departure point.' if meaningful_groups else 'No material route departure was found.',
                             {'off_route_samples': len(off), 'departures': departure_runs}, segments))
    else:
        for code, title in [('start_finish', 'Start and finish'), ('route_coverage', 'Route coverage'),
                            ('shortcut_detection', 'Shortcut detection'), ('route_departures', 'Route departures')]:
            if not any(c.code == code for c in checks):
                checks.append(_check(code, title, 'incomplete', 'The official route geometry is unavailable.'))

    distance_ok = recorded_km >= required_km * .98
    checks.append(_check('distance_completion', 'Distance completion', 'clear' if distance_ok else 'needs_review',
                         f'{recorded_km:.1f} recorded km compared with {required_km:.1f} required km.',
                         {'recorded_km': round(recorded_km, 2), 'required_km': required_km}))

    gaps, jumps, implausible = [], [], []
    for a, b in zip(points, points[1:]):
        seconds = (b.timestamp - a.timestamp).total_seconds()
        meters = haversine((a.lat, a.lng), (b.lat, b.lng))
        speed = meters / seconds if seconds > 0 else float('inf')
        # A long interval at the same location is a legitimate control/sleep stop,
        # not a recording gap. Flag only when the device resumes elsewhere.
        if seconds > 1800 and meters > 250:
            gaps.append((a, b, seconds))
        if meters > 5000 and seconds < 60:
            jumps.append((a, b))
        if speed > 27.8:  # 100 km/h: advisory; legitimate descents remain human-reviewed.
            implausible.append((a, b, speed))
    continuity_ok = not gaps and not jumps
    gap_segments = [[[a.lat, a.lng], [b.lat, b.lng]] for a, b, *_ in (gaps + jumps)][:100]
    checks.append(_check('track_continuity', 'Track continuity', 'clear' if continuity_ok else 'needs_review',
                         f'{len(gaps)} long time gap(s) and {len(jumps)} jump(s) detected.',
                         {'long_gaps': len(gaps), 'jumps': len(jumps)}, gap_segments))
    checks.append(_check('plausible_movement', 'Plausible movement', 'clear' if not implausible else 'needs_review',
                         'Movement is consistent with cycling.' if not implausible else f'{len(implausible)} high-speed transition(s) need review; downhill and GPS noise may explain them.',
                         {'high_speed_transitions': len(implausible)},
                         [[[a.lat, a.lng], [b.lat, b.lng]] for a, b, _ in implausible[:100]]))

    route_ele = [float(p['e_m']) for p in route if p.get('e_m') is not None]
    actual_ele = [float(p.elevation_m) for p in points if p.elevation_m is not None]
    if len(route_ele) >= 3 and len(actual_ele) >= 3:
        def gain(values):
            return sum(max(0, b - a) for a, b in zip(values, values[1:]))
        def signs(values, bins=20):
            sampled = [values[min(len(values) - 1, round(i * (len(values) - 1) / bins))]
                       for i in range(bins + 1)]
            return [1 if b - a > 2 else (-1 if b - a < -2 else 0)
                    for a, b in zip(sampled, sampled[1:])]
        rg, ag = gain(route_ele), gain(actual_ele)
        ratio = ag / rg if rg else 1.0
        official_signs, actual_signs = signs(route_ele), signs(actual_ele)
        comparable = [(a, b) for a, b in zip(official_signs, actual_signs) if a and b]
        sequence_match = (sum(a == b for a, b in comparable) / len(comparable)) if comparable else 1.0
        terrain_ok = .55 <= ratio <= 1.8 and sequence_match >= .6
        checks.append(_check('terrain_consistency', 'Terrain consistency', 'clear' if terrain_ok else 'needs_review',
                             f'Recorded-to-official sampled climb ratio is {ratio:.2f}; climb/descent sequence agreement is {sequence_match:.0%}.',
                             {'climb_ratio': round(ratio, 3), 'sequence_match': round(sequence_match, 3),
                              'recorded_gain_m': round(ag), 'official_gain_m': round(rg)}))
    else:
        checks.append(_check('terrain_consistency', 'Terrain consistency', 'incomplete', 'Elevation samples are insufficient for terrain comparison.'))

    manual = bool(source_metadata.get('manual'))
    device = source_metadata.get('device') or source_metadata.get('creator') or source_metadata.get('format')
    checks.append(_check('activity_provenance', 'Activity provenance', 'needs_review' if manual or not device else 'clear',
                         'Manual/synthetic activity or missing device metadata needs review.' if manual or not device else f'Original source metadata retained ({device}).',
                         {'manual': manual, 'device': device}))
    activity_kind = str(source_metadata.get('sub_sport') or source_metadata.get('sport')
                        or source_metadata.get('activity_type') or '').lower()
    motorized = activity_kind in {'ebike', 'e-bike', 'e_bike', 'motorcycling'}
    checks.append(_check('human_powered', 'Human-powered activity', 'needs_review' if motorized else 'clear',
                         'Activity type suggests motor assistance; organizer review is required.' if motorized else 'No motorized activity type was identified.',
                         {'activity_type': activity_kind or None}))
    conflicts = duplicate_conflicts or []
    checks.append(_check('duplicate_evidence', 'Duplicate evidence', 'needs_review' if conflicts else 'clear',
                         f'This evidence is already attached to {len(conflicts)} incompatible submission(s).' if conflicts else 'No incompatible reuse of this evidence was found.',
                         {'conflicts': conflicts}))

    results = {c.result for c in checks}
    decision = 'incomplete' if 'incomplete' in results else ('needs_review' if 'needs_review' in results else 'clear')
    return decision, checks
