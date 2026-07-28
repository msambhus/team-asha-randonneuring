"""Deterministic, rider-owned matching of Garmin and Strava ride recordings."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

AUTO_MATCH_THRESHOLD = 0.82
AMBIGUITY_MARGIN = 0.12
METERS_PER_MILE = 1609.344
FEET_PER_METER = 3.28084


def _number(value):
    if value is None:
        return None
    return float(Decimal(str(value)))


def _datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_cycling(activity_type):
    value = (activity_type or "").casefold()
    return any(token in value for token in ("cycl", "ride", "bike", "biking"))


def _relative_difference(left, right):
    if left is None or right is None or max(left, right) <= 0:
        return None
    return abs(left - right) / max(left, right)


def build_provider_metric_comparison(strava_summary, garmin_metrics):
    """Preserve both providers and flag only material recording differences."""
    if not strava_summary or not garmin_metrics:
        return []

    metrics = [
        {
            "key": "distance", "label": "Distance", "unit": "mi",
            "strava": _number(strava_summary.get("actual_distance_miles")),
            "garmin": (
                _number(garmin_metrics.get("distance_m")) / METERS_PER_MILE
                if garmin_metrics.get("distance_m") is not None else None),
            "absolute_floor": 0.5, "relative_floor": 0.02, "precision": 1,
        },
        {
            "key": "elapsed_time", "label": "Elapsed time", "unit": "min",
            "strava": _number(
                strava_summary.get("actual_elapsed_time_min")),
            "garmin": (
                _number(garmin_metrics.get("duration_s")) / 60
                if garmin_metrics.get("duration_s") is not None else None),
            "absolute_floor": 5, "relative_floor": 0.05, "precision": 0,
        },
        {
            "key": "moving_time", "label": "Moving time", "unit": "min",
            "strava": _number(
                strava_summary.get("actual_moving_time_min")),
            "garmin": (
                _number(garmin_metrics.get("moving_duration_s")) / 60
                if garmin_metrics.get("moving_duration_s") is not None
                else None),
            "absolute_floor": 5, "relative_floor": 0.05, "precision": 0,
        },
        {
            "key": "elevation", "label": "Elevation gain", "unit": "ft",
            "strava": _number(strava_summary.get("actual_elevation_ft")),
            "garmin": (
                _number(garmin_metrics.get("elevation_gain_m"))
                * FEET_PER_METER
                if garmin_metrics.get("elevation_gain_m") is not None
                else None),
            "absolute_floor": 200, "relative_floor": 0.10, "precision": 0,
        },
        {
            "key": "average_hr", "label": "Average heart rate", "unit": "bpm",
            "strava": _number(
                strava_summary.get("average_heartrate")
                or strava_summary.get("avg_hr")),
            "garmin": _number(garmin_metrics.get("average_hr")),
            "absolute_floor": 5, "relative_floor": 0.04, "precision": 0,
        },
        {
            "key": "max_hr", "label": "Maximum heart rate", "unit": "bpm",
            "strava": _number(
                strava_summary.get("max_heartrate")
                or strava_summary.get("max_hr")),
            "garmin": _number(garmin_metrics.get("max_hr")),
            "absolute_floor": 5, "relative_floor": 0.03, "precision": 0,
        },
        {
            "key": "average_power", "label": "Average power", "unit": "W",
            "strava": _number(
                strava_summary.get("average_watts")
                or strava_summary.get("avg_watts")),
            "garmin": _number(garmin_metrics.get("average_power")),
            "absolute_floor": 10, "relative_floor": 0.08, "precision": 0,
        },
        {
            "key": "normalized_power", "label": "Normalized power", "unit": "W",
            "strava": _number(
                strava_summary.get("weighted_average_watts")
                or strava_summary.get("weighted_avg_watts")),
            "garmin": _number(garmin_metrics.get("normalized_power")),
            "absolute_floor": 10, "relative_floor": 0.07, "precision": 0,
        },
        {
            "key": "max_power", "label": "Maximum power", "unit": "W",
            "strava": _number(
                strava_summary.get("max_watts")),
            "garmin": _number(garmin_metrics.get("max_power")),
            "absolute_floor": 50, "relative_floor": 0.10, "precision": 0,
        },
        {
            "key": "cadence", "label": "Average cadence", "unit": "rpm",
            "strava": _number(strava_summary.get("average_cadence")),
            "garmin": _number(garmin_metrics.get("average_cadence")),
            "absolute_floor": 4, "relative_floor": 0.06, "precision": 0,
        },
    ]

    comparison = []
    for metric in metrics:
        left = metric["strava"]
        right = metric["garmin"]
        if left is None or right is None:
            continue
        delta = right - left
        relative = _relative_difference(left, right)
        material = (
            abs(delta) >= metric["absolute_floor"]
            and relative is not None
            and relative >= metric["relative_floor"]
        )
        comparison.append({
            **metric,
            "strava": round(left, metric["precision"]),
            "garmin": round(right, metric["precision"]),
            "delta": round(delta, metric["precision"]),
            "difference_percent": (
                round(relative * 100, 1) if relative is not None else None),
            "material": material,
        })
    return comparison


def build_garmin_brevet_summary(ride, garmin_metrics):
    """Build an honest plan-level summary when Garmin is the only recording."""
    if not ride or not garmin_metrics:
        return None

    planned_km = _number(ride.get("distance_km"))
    distance_m = _number(garmin_metrics.get("distance_m"))
    elapsed_s = _number(garmin_metrics.get("duration_s"))
    moving_s = _number(garmin_metrics.get("moving_duration_s"))
    limit_hours = _number(ride.get("time_limit_hours"))

    planned_miles = (
        planned_km * 0.621371 if planned_km is not None else None)
    actual_miles = (
        distance_m / METERS_PER_MILE if distance_m is not None else None)
    elapsed_min = elapsed_s / 60 if elapsed_s is not None else None
    moving_min = moving_s / 60 if moving_s is not None else None
    stopped_min = (
        max(0, elapsed_min - moving_min)
        if elapsed_min is not None and moving_min is not None else None)
    limit_min = limit_hours * 60 if limit_hours is not None else None

    return {
        "planned_distance_miles": (
            round(planned_miles, 1) if planned_miles is not None else None),
        "actual_distance_miles": (
            round(actual_miles, 1) if actual_miles is not None else None),
        "distance_delta_miles": (
            round(actual_miles - planned_miles, 1)
            if actual_miles is not None and planned_miles is not None
            else None),
        "elapsed_time_min": (
            round(elapsed_min) if elapsed_min is not None else None),
        "moving_time_min": (
            round(moving_min) if moving_min is not None else None),
        "stopped_time_min": (
            round(stopped_min) if stopped_min is not None else None),
        "official_limit_min": (
            round(limit_min) if limit_min is not None else None),
        "limit_margin_min": (
            round(limit_min - elapsed_min)
            if limit_min is not None and elapsed_min is not None else None),
        "average_moving_speed_mph": (
            round(actual_miles / (moving_s / 3600), 1)
            if actual_miles is not None and moving_s and moving_s > 0
            else None),
    }


def aggregate_garmin_recordings(recordings):
    """Combine split Garmin recordings without discarding per-part provenance."""
    rows = [dict(row) for row in recordings or []]
    if not rows:
        return None

    def total(key):
        values = [_number(row.get(key)) for row in rows]
        present = [value for value in values if value is not None]
        return sum(present) if present else None

    moving_weights = [
        max(
            _number(row.get("moving_duration_s"))
            or _number(row.get("duration_s"))
            or 1,
            0,
        )
        for row in rows
    ]

    def weighted(key):
        pairs = [
            (_number(row.get(key)), weight)
            for row, weight in zip(rows, moving_weights)
            if row.get(key) is not None and weight > 0
        ]
        return (
            sum(value * weight for value, weight in pairs)
            / sum(weight for _, weight in pairs)
            if pairs else None
        )

    starts = [
        _datetime(row.get("started_at")) for row in rows
        if row.get("started_at") is not None
    ]
    ends = []
    for row in rows:
        started = _datetime(row.get("started_at"))
        duration = _number(row.get("duration_s"))
        if started and duration is not None:
            ends.append(started + timedelta(seconds=duration))
    elapsed = (
        (max(ends) - min(starts)).total_seconds()
        if starts and ends else total("duration_s")
    )
    normalized = [
        (_number(row.get("normalized_power")), weight)
        for row, weight in zip(rows, moving_weights)
        if row.get("normalized_power") is not None and weight > 0
    ]
    normalized_power = (
        (sum((value ** 4) * weight for value, weight in normalized)
         / sum(weight for _, weight in normalized)) ** 0.25
        if normalized else None
    )
    devices = sorted({
        str(row["device_name"]) for row in rows if row.get("device_name")
    })
    parts = [{
        "garmin_activity_id": row.get("garmin_activity_id"),
        "activity_name": row.get("activity_name"),
        "started_at": row.get("started_at"),
        "distance_m": row.get("distance_m"),
        "duration_s": row.get("duration_s"),
        "device_name": row.get("device_name"),
    } for row in sorted(
        rows, key=lambda row: str(row.get("started_at") or ""))]

    result = {
        "recording_count": len(rows),
        "recording_parts": parts,
        "distance_m": total("distance_m"),
        "duration_s": elapsed,
        "moving_duration_s": total("moving_duration_s"),
        "elevation_gain_m": total("elevation_gain_m"),
        "average_hr": weighted("average_hr"),
        "max_hr": max(
            (_number(row.get("max_hr")) for row in rows
             if row.get("max_hr") is not None), default=None),
        "average_power": weighted("average_power"),
        "max_power": max(
            (_number(row.get("max_power")) for row in rows
             if row.get("max_power") is not None), default=None),
        "normalized_power": normalized_power,
        "aerobic_training_effect": max(
            (_number(row.get("aerobic_training_effect")) for row in rows
             if row.get("aerobic_training_effect") is not None), default=None),
        "anaerobic_training_effect": max(
            (_number(row.get("anaerobic_training_effect")) for row in rows
             if row.get("anaerobic_training_effect") is not None), default=None),
        "calories": total("calories"),
        "average_cadence": weighted("average_cadence"),
        "device_name": ", ".join(devices) if devices else None,
    }
    return result


def aggregate_strava_recordings(recordings):
    """Combine split Strava headline metrics while retaining every source row."""
    rows = [dict(row) for row in recordings or []]
    if not rows:
        return None

    def total(key):
        values = [_number(row.get(key)) for row in rows]
        present = [value for value in values if value is not None]
        return sum(present) if present else None

    weights = [max(_number(row.get("moving_time")) or 0, 0) for row in rows]

    def weighted(key):
        pairs = [
            (_number(row.get(key)), weight)
            for row, weight in zip(rows, weights)
            if row.get(key) is not None and weight > 0
        ]
        return (
            sum(value * weight for value, weight in pairs)
            / sum(weight for _, weight in pairs)
            if pairs else None
        )

    starts = [
        _datetime(row.get("start_date")) for row in rows
        if row.get("start_date") is not None
    ]
    ends = []
    for row in rows:
        started = _datetime(row.get("start_date"))
        duration = _number(row.get("elapsed_time"))
        if started and duration is not None:
            ends.append(started + timedelta(seconds=duration))
    elapsed = (
        (max(ends) - min(starts)).total_seconds()
        if starts and ends else total("elapsed_time")
    )
    return {
        "recording_count": len(rows),
        "recording_parts": [{
            "strava_activity_id": row.get("strava_activity_id"),
            "activity_name": row.get("name"),
            "started_at": row.get("start_date"),
            "distance_m": row.get("distance"),
            "duration_s": row.get("elapsed_time"),
            "strava_url": row.get("strava_url"),
        } for row in rows],
        "actual_distance_miles": (
            total("distance") / METERS_PER_MILE
            if total("distance") is not None else None),
        "actual_elapsed_time_min": elapsed / 60 if elapsed is not None else None,
        "actual_moving_time_min": (
            total("moving_time") / 60
            if total("moving_time") is not None else None),
        "actual_elevation_ft": (
            total("total_elevation_gain") * FEET_PER_METER
            if total("total_elevation_gain") is not None else None),
        "average_heartrate": weighted("average_heartrate"),
        "max_heartrate": max(
            (_number(row.get("max_heartrate")) for row in rows
             if row.get("max_heartrate") is not None), default=None),
        "average_watts": weighted("average_watts"),
        "max_watts": max(
            (_number(row.get("max_watts")) for row in rows
             if row.get("max_watts") is not None), default=None),
        "weighted_average_watts": weighted("weighted_average_watts"),
        "average_cadence": weighted("average_cadence"),
        "kilojoules": total("kilojoules"),
        "suffer_score": total("suffer_score"),
    }


def score_pair(garmin, strava):
    """Score one Garmin/Strava candidate and explain every contributing signal."""
    if not (_is_cycling(garmin.get("activity_type"))
            and _is_cycling(strava.get("activity_type"))):
        return None

    garmin_start = _datetime(garmin.get("started_at"))
    strava_start = _datetime(
        strava.get("start_date") or strava.get("start_date_local"))
    if not garmin_start or not strava_start:
        return None
    start_minutes = abs((garmin_start - strava_start).total_seconds()) / 60
    if start_minutes > 120:
        return None

    distance_difference = _relative_difference(
        _number(garmin.get("distance_m")),
        _number(strava.get("distance")),
    )
    if distance_difference is None or distance_difference > 0.20:
        return None

    duration_difference = _relative_difference(
        _number(garmin.get("duration_s")),
        _number(strava.get("elapsed_time")),
    )
    if duration_difference is not None and duration_difference > 0.30:
        return None

    if start_minutes <= 5:
        start_score = 0.35
    elif start_minutes <= 15:
        start_score = 0.30
    elif start_minutes <= 60:
        start_score = 0.20
    else:
        start_score = 0.10

    if distance_difference <= 0.03:
        distance_score = 0.35
    elif distance_difference <= 0.08:
        distance_score = 0.28
    else:
        distance_score = 0.18

    duration_score = 0.0
    if duration_difference is not None:
        if duration_difference <= 0.05:
            duration_score = 0.20
        elif duration_difference <= 0.12:
            duration_score = 0.15
        else:
            duration_score = 0.08

    garmin_name = (garmin.get("activity_name") or "").casefold().strip()
    strava_name = (strava.get("name") or "").casefold().strip()
    name_score = 0.10 if garmin_name and garmin_name == strava_name else 0.0
    confidence = round(
        min(start_score + distance_score + duration_score + name_score, 1.0),
        3,
    )
    return {
        "confidence": confidence,
        "reasons": {
            "start_delta_minutes": round(start_minutes, 1),
            "distance_difference_percent": round(distance_difference * 100, 1),
            "duration_difference_percent": (
                round(duration_difference * 100, 1)
                if duration_difference is not None else None
            ),
            "exact_name": bool(name_score),
        },
    }


def auto_matches(garmin_activities, strava_activities):
    """Return only unambiguous, one-to-one, high-confidence source matches."""
    candidates_by_garmin = {}
    for garmin in garmin_activities:
        garmin_id = garmin["garmin_activity_id"]
        candidates = []
        for strava in strava_activities:
            scored = score_pair(garmin, strava)
            if scored:
                candidates.append({
                    **scored,
                    "rider_id": garmin["rider_id"],
                    "garmin_activity_id": garmin_id,
                    "strava_activity_id": strava["strava_activity_id"],
                })
        candidates_by_garmin[garmin_id] = sorted(
            candidates, key=lambda row: row["confidence"], reverse=True)

    eligible = []
    for candidates in candidates_by_garmin.values():
        if not candidates or candidates[0]["confidence"] < AUTO_MATCH_THRESHOLD:
            continue
        if (len(candidates) > 1 and
                candidates[0]["confidence"] - candidates[1]["confidence"]
                < AMBIGUITY_MARGIN):
            continue
        eligible.append(candidates[0])

    # A single Strava recording cannot auto-link to two Garmin activities.
    eligible.sort(key=lambda row: row["confidence"], reverse=True)
    used_strava = set()
    matches = []
    for candidate in eligible:
        if candidate["strava_activity_id"] in used_strava:
            continue
        used_strava.add(candidate["strava_activity_id"])
        matches.append(candidate)
    return matches


def refresh_activity_matches(rider_id):
    """Recompute safe automatic matches from normalized, rider-owned rows."""
    import models

    garmin = models.get_garmin_activities_for_matching(rider_id)
    strava = models.get_strava_activities_for_matching(rider_id)
    matches = auto_matches(garmin, strava)
    models.upsert_activity_source_matches(rider_id, matches)
    refresh_brevet_matches(rider_id)
    return len(matches)


def refresh_activity_matches_safely(rider_id):
    """Run derived matching without turning a provider sync into a failure."""
    try:
        return refresh_activity_matches(rider_id)
    except Exception:
        from flask import current_app
        current_app.logger.exception(
            "Activity source matching failed for rider %s", rider_id)
        return 0


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def score_garmin_brevet(garmin, brevet):
    """Conservatively score a Garmin-only recording against a finished brevet."""
    if not _is_cycling(garmin.get("activity_type")):
        return None
    started = _datetime(garmin.get("started_at"))
    if not started or abs((_date(brevet["date"]) - started.date()).days) > 1:
        return None

    distance_difference = _relative_difference(
        _number(garmin.get("distance_m")),
        _number(brevet.get("distance_km")) * 1000,
    )
    if distance_difference is None or distance_difference > 0.08:
        return None
    same_date = _date(brevet["date"]) == started.date()
    confidence = (
        0.94 if same_date and distance_difference <= 0.03
        else 0.86 if same_date
        else 0.82
    )
    return {
        "confidence": confidence,
        "reasons": {
            "same_calendar_date": same_date,
            "date_delta_days": abs((_date(brevet["date"]) - started.date()).days),
            "distance_difference_percent": round(distance_difference * 100, 1),
            "finished_brevet": True,
            "garmin_only": True,
        },
    }


def garmin_brevet_auto_matches(garmin_activities, brevets):
    """Return unique Garmin-only brevet links; do not guess among ambiguity."""
    candidates = {}
    for garmin in garmin_activities:
        scored = []
        for brevet in brevets:
            result = score_garmin_brevet(garmin, brevet)
            if result:
                scored.append({
                    **result,
                    "rider_id": garmin["rider_id"],
                    "ride_id": brevet["ride_id"],
                    "garmin_activity_id": garmin["garmin_activity_id"],
                    "strava_activity_id": None,
                    "source_match_id": None,
                    "match_status": "auto",
                })
        if len(scored) == 1:
            candidates[garmin["garmin_activity_id"]] = scored[0]

    # Two Garmin recordings can represent split parts of one brevet, but those
    # require rider review. Auto-link only a brevet with one clear recording.
    ride_counts = {}
    for candidate in candidates.values():
        ride_counts[candidate["ride_id"]] = (
            ride_counts.get(candidate["ride_id"], 0) + 1)
    return [
        candidate for candidate in candidates.values()
        if ride_counts[candidate["ride_id"]] == 1
    ]


def split_brevet_auto_matches(activities, brevets, provider):
    """Auto-link only a unique contiguous set whose combined distance fits."""
    if provider not in ("garmin", "strava"):
        raise ValueError("unsupported split recording provider")
    id_key = (
        "garmin_activity_id" if provider == "garmin"
        else "strava_activity_id")
    start_key = "started_at" if provider == "garmin" else "start_date"
    distance_key = "distance_m" if provider == "garmin" else "distance"
    duration_key = "duration_s" if provider == "garmin" else "elapsed_time"
    candidates = []

    for brevet in brevets:
        target = _number(brevet.get("distance_km")) * 1000
        max_days = max(1, min(5, int(target / 250000) + 1))
        ride_date = _date(brevet["date"])
        pool = []
        for activity in activities:
            if not _is_cycling(activity.get("activity_type")):
                continue
            started = _datetime(activity.get(start_key))
            distance = _number(activity.get(distance_key))
            if not started or not distance or distance >= target * 0.90:
                continue
            if ride_date <= started.date() <= ride_date + timedelta(days=max_days):
                pool.append(activity)
        pool.sort(key=lambda row: _datetime(row.get(start_key)))
        # Bound work and risk: split recordings should be chronological and
        # adjacent, not arbitrary combinations of a rider's week.
        pool = pool[:12]
        qualifying = []
        for start in range(len(pool)):
            total_distance = 0.0
            previous_end = None
            group = []
            for activity in pool[start:start + 6]:
                started = _datetime(activity.get(start_key))
                duration = _number(activity.get(duration_key)) or 0
                if previous_end and started - previous_end > timedelta(hours=18):
                    break
                group.append(activity)
                total_distance += _number(activity.get(distance_key)) or 0
                previous_end = started + timedelta(seconds=duration)
                if len(group) < 2:
                    continue
                difference = _relative_difference(total_distance, target)
                if difference is not None and difference <= 0.08:
                    qualifying.append((difference, list(group), total_distance))
                if total_distance > target * 1.08:
                    break
        if len(qualifying) != 1:
            continue
        difference, group, combined = qualifying[0]
        candidates.append({
            "brevet": brevet,
            "group": group,
            "difference": difference,
            "combined": combined,
        })

    # Never use a source recording in two automatic brevet groups.
    counts = {}
    for candidate in candidates:
        for activity in candidate["group"]:
            source_id = activity[id_key]
            counts[source_id] = counts.get(source_id, 0) + 1

    matches = []
    for candidate in candidates:
        group = candidate["group"]
        if any(counts[activity[id_key]] != 1 for activity in group):
            continue
        for index, activity in enumerate(group, start=1):
            match = {
                "rider_id": activity["rider_id"],
                "ride_id": candidate["brevet"]["ride_id"],
                "garmin_activity_id": (
                    activity[id_key] if provider == "garmin" else None),
                "strava_activity_id": (
                    activity[id_key] if provider == "strava" else None),
                "source_match_id": None,
                "confidence": round(
                    max(0.82, 0.94 - candidate["difference"]), 3),
                "match_status": "auto",
                "reasons": {
                    "split_recording": True,
                    "provider": provider,
                    "part_number": index,
                    "part_count": len(group),
                    "combined_distance_m": round(candidate["combined"]),
                    "distance_difference_percent": round(
                        candidate["difference"] * 100, 1),
                },
            }
            matches.append(match)
    return matches


def refresh_brevet_matches(rider_id):
    """Reuse reviewed Strava links, then add conservative Garmin-only links."""
    import models

    authoritative = models.get_authoritative_brevet_source_links(rider_id)
    linked_garmin_ids = {
        row["garmin_activity_id"] for row in authoritative
        if row.get("garmin_activity_id") is not None
    }
    linked_ride_ids = {row["ride_id"] for row in authoritative}
    garmin = [
        row for row in models.get_garmin_activities_for_matching(rider_id)
        if row["garmin_activity_id"] not in linked_garmin_ids
    ]
    brevets = [
        row for row in models.get_finished_brevets_for_matching(rider_id)
        if row["ride_id"] not in linked_ride_ids
    ]
    automatic = garmin_brevet_auto_matches(garmin, brevets)
    single_garmin_ids = {
        row["garmin_activity_id"] for row in automatic
        if row.get("garmin_activity_id") is not None
    }
    split_garmin = split_brevet_auto_matches(
        [row for row in garmin
         if row["garmin_activity_id"] not in single_garmin_ids],
        brevets, "garmin")
    strava = [
        row for row in models.get_strava_activities_for_matching(rider_id)
        if row["strava_activity_id"] not in {
            link["strava_activity_id"] for link in authoritative
            if link.get("strava_activity_id") is not None
        }
    ]
    split_strava = split_brevet_auto_matches(strava, brevets, "strava")
    models.replace_activity_brevet_matches(
        rider_id, [*authoritative, *automatic, *split_garmin, *split_strava])
    return (
        len(authoritative) + len(automatic)
        + len(split_garmin) + len(split_strava)
    )
