"""Deterministic, rider-owned matching of Garmin and Strava ride recordings."""
from datetime import date, datetime, timezone
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
    models.replace_activity_brevet_matches(
        rider_id, [*authoritative, *automatic])
    return len(authoritative) + len(automatic)
