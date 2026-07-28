"""Private activity-feed normalization shared by Team Asha and BrevetHub.

The feed deliberately contains presentation-safe summary fields only. Database
ownership checks stay in each application's route/model layer.
"""
from datetime import datetime


def _value(row, *keys):
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _iso_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _timestamp(value):
    if not value:
        return 0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0


def build_private_activity_feed(strava_activities=None, garmin_activities=None):
    """Return newest-first logical rides, collapsing explicit Garmin/Strava matches.

    ``garmin_activities`` may contain the enriched fields returned by Team
    Asha's match-review query. A linked ``strava_activity_id`` is authoritative:
    its Strava row is represented by the same card and never emitted twice.
    """
    cards = []
    matched_strava_ids = set()

    for source in garmin_activities or []:
        row = dict(source)
        strava_id = row.get("strava_activity_id")
        if strava_id is not None:
            matched_strava_ids.add(str(strava_id))
        started_at = _iso_datetime(
            _value(row, "started_at", "strava_started_at"))
        distance_m = _value(row, "strava_distance_m", "distance_m")
        duration_s = _value(
            row, "strava_elapsed_s", "duration_s", "moving_duration_s")
        providers = ["Garmin"]
        if strava_id is not None:
            providers.insert(0, "Strava")
        cards.append({
            "key": f"garmin:{row.get('garmin_activity_id')}",
            "name": _value(
                row, "ride_name", "strava_name", "activity_name") or "Cycling activity",
            "activity_type": _value(
                row, "strava_activity_type", "activity_type") or "Ride",
            "started_at": started_at,
            "distance_m": distance_m,
            "duration_s": duration_s,
            "elevation_gain_m": _value(
                row, "strava_elevation_gain_m", "elevation_gain_m"),
            "providers": providers,
            "strava_activity_id": strava_id,
            "garmin_activity_id": row.get("garmin_activity_id"),
            "ride_id": row.get("ride_id"),
            "ride_name": row.get("ride_name"),
            "match_confidence": row.get("confidence"),
        })

    for source in strava_activities or []:
        row = dict(source)
        strava_id = _value(row, "strava_activity_id", "id")
        if strava_id is not None and str(strava_id) in matched_strava_ids:
            continue
        cards.append({
            "key": f"strava:{strava_id}",
            "name": _value(row, "name", "activity_name") or "Cycling activity",
            "activity_type": _value(row, "activity_type", "sport_type") or "Workout",
            "started_at": _iso_datetime(
                _value(row, "start_date_local", "start_date", "started_at")),
            "distance_m": _value(row, "distance", "distance_m"),
            "duration_s": _value(row, "elapsed_time", "duration_s", "moving_time"),
            "elevation_gain_m": _value(
                row, "total_elevation_gain", "elevation_gain_m"),
            "providers": ["Strava"],
            "strava_activity_id": strava_id,
            "garmin_activity_id": None,
            "ride_id": row.get("ride_id"),
            "ride_name": row.get("ride_name"),
            "match_confidence": row.get("confidence"),
        })

    return sorted(
        cards, key=lambda card: _timestamp(card.get("started_at")), reverse=True)
