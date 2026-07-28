from datetime import datetime, timezone
from unittest.mock import patch

from services.activity_matching import (
    garmin_brevet_auto_matches,
    refresh_brevet_matches,
    score_garmin_brevet,
)


def _garmin(activity_id=1, distance_m=201000, started_at=None):
    return {
        "rider_id": 42,
        "garmin_activity_id": activity_id,
        "activity_type": "cycling",
        "started_at": started_at or datetime(
            2026, 7, 27, 7, 0, tzinfo=timezone.utc),
        "distance_m": distance_m,
    }


def _brevet(ride_id=10, distance_km=200, date="2026-07-27"):
    return {
        "ride_id": ride_id,
        "date": date,
        "distance_km": distance_km,
    }


def test_same_day_finished_brevet_scores_high_with_reasons():
    match = score_garmin_brevet(_garmin(), _brevet())
    assert match["confidence"] == 0.94
    assert match["reasons"]["same_calendar_date"] is True
    assert match["reasons"]["distance_difference_percent"] == 0.5


def test_same_day_wrong_distance_is_not_a_brevet_match():
    assert score_garmin_brevet(
        _garmin(distance_m=80000), _brevet()) is None


def test_ambiguous_same_distance_brevets_are_not_auto_linked():
    assert garmin_brevet_auto_matches(
        [_garmin()],
        [_brevet(10), _brevet(11)],
    ) == []


def test_split_garmin_recordings_require_rider_review():
    assert garmin_brevet_auto_matches(
        [_garmin(1), _garmin(2)],
        [_brevet()],
    ) == []


def test_authoritative_strava_brevet_link_carries_garmin_source():
    authoritative = [{
        "rider_id": 42,
        "ride_id": 10,
        "source_match_id": 8,
        "garmin_activity_id": 1,
        "strava_activity_id": 20,
        "confidence": 1.0,
        "match_status": "authoritative",
        "reasons": {"existing_strava_brevet_match": True},
    }]
    with patch("models.get_authoritative_brevet_source_links",
               return_value=authoritative), \
         patch("models.get_garmin_activities_for_matching",
               return_value=[_garmin(1), _garmin(2)]), \
         patch("models.get_strava_activities_for_matching",
               return_value=[]), \
         patch("models.get_finished_brevets_for_matching",
               return_value=[_brevet(10), _brevet(11, 300)]), \
         patch("models.replace_activity_brevet_matches") as save:
        count = refresh_brevet_matches(42)

    assert count == 1
    saved = save.call_args.args[1]
    assert saved == authoritative
    assert save.call_args.args[0] == 42
