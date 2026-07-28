"""Conservative owner-scoped SRAM AXS ride matching."""
from datetime import datetime


def _epoch(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _candidate(source, id_key, distance_key, time_key, activity):
    started = float(activity.get("started_at_epoch") or 0)
    target_distance = float(activity.get("distance_m") or 0)
    candidate_time = _epoch(source.get(time_key))
    candidate_distance = float(source.get(distance_key) or 0)
    time_delta = abs(candidate_time - started) if candidate_time else 999999
    distance_delta = (
        abs(candidate_distance - target_distance) / target_distance
        if target_distance and candidate_distance else 1.0
    )
    if time_delta > 900 or distance_delta > 0.08:
        return None
    confidence = 1.0 - min(0.20, time_delta / 4500) - min(
        0.20, distance_delta * 1.5)
    return {
        "id": source[id_key],
        "ride_id": source.get("ride_id"),
        "confidence": round(max(0, min(1, confidence)), 3),
        "time_delta_seconds": round(time_delta),
        "distance_delta_percent": round(distance_delta * 100, 1),
    }


def build_sram_match(activity, candidates):
    """Auto-link only unique, high-confidence source and brevet candidates."""
    result = {
        "strava_activity_id": None,
        "garmin_activity_id": None,
        "ride_id": None,
        "confidence": 0.0,
        "reasons": {},
        "match_status": "auto",
    }
    scores = []
    for key, id_key, distance_key, time_key in (
        ("strava", "strava_activity_id", "distance", "start_date_local"),
        ("garmin", "garmin_activity_id", "distance_m", "started_at"),
    ):
        matches = [
            match for row in candidates.get(key, [])
            if (match := _candidate(
                row, id_key, distance_key, time_key, activity))
        ]
        matches.sort(key=lambda row: row["confidence"], reverse=True)
        if len(matches) == 1 and matches[0]["confidence"] >= 0.90:
            result[id_key] = matches[0]["id"]
            if matches[0].get("ride_id"):
                result["ride_id"] = matches[0]["ride_id"]
            result["reasons"][key] = matches[0]
            scores.append(matches[0]["confidence"])

    target_distance = float(activity.get("distance_m") or 0)
    ride_matches = []
    for ride in candidates.get("rides", []):
        ride_distance = float(ride.get("distance_km") or 0) * 1000
        delta = (
            abs(ride_distance - target_distance) / target_distance
            if target_distance and ride_distance else 1.0)
        if delta <= 0.10:
            ride_matches.append((delta, ride))
    if len(ride_matches) == 1:
        delta, ride = ride_matches[0]
        confidence = round(0.94 - delta, 3)
        result["ride_id"] = ride["ride_id"]
        result["reasons"]["brevet"] = {
            "distance_delta_percent": round(delta * 100, 1),
            "same_start_window": True,
        }
        scores.append(confidence)

    if not scores:
        return None
    result["confidence"] = min(scores)
    if result["confidence"] < 0.88:
        return None
    return result
