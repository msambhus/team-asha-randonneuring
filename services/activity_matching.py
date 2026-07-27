"""Deterministic, rider-owned matching of Garmin and Strava ride recordings."""
from datetime import datetime, timezone
from decimal import Decimal

AUTO_MATCH_THRESHOLD = 0.82
AMBIGUITY_MARGIN = 0.12


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
