from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import models
from services.activity_matching import (
    auto_matches,
    refresh_activity_matches,
    refresh_activity_matches_safely,
    score_pair,
)


START = datetime(2026, 7, 27, 7, 0, tzinfo=timezone.utc)


def _garmin(activity_id=1, **overrides):
    row = {
        "rider_id": 42,
        "garmin_activity_id": activity_id,
        "activity_name": "Sunday 200K",
        "activity_type": "cycling",
        "started_at": START,
        "distance_m": 201000,
        "duration_s": 36000,
    }
    row.update(overrides)
    return row


def _strava(activity_id=10, **overrides):
    row = {
        "rider_id": 42,
        "strava_activity_id": activity_id,
        "name": "Sunday 200K",
        "activity_type": "Ride",
        "start_date": START + timedelta(minutes=2),
        "distance": 200500,
        "elapsed_time": 35800,
    }
    row.update(overrides)
    return row


def test_matching_same_recording_explains_high_confidence():
    match = score_pair(_garmin(), _strava())
    assert match["confidence"] == 1.0
    assert match["reasons"]["start_delta_minutes"] == 2.0
    assert match["reasons"]["distance_difference_percent"] == 0.2
    assert match["reasons"]["exact_name"] is True


def test_same_date_but_different_ride_is_rejected():
    assert score_pair(
        _garmin(),
        _strava(distance=80000, start_date=START + timedelta(hours=6)),
    ) is None


def test_ambiguous_candidates_are_not_auto_linked():
    candidates = [
        _strava(10, name="Morning Ride"),
        _strava(11, name="Other Ride", start_date=START + timedelta(minutes=3)),
    ]
    assert auto_matches([_garmin(activity_name="")], candidates) == []


def test_matching_is_one_to_one_across_split_or_duplicate_recordings():
    matches = auto_matches(
        [_garmin(1), _garmin(2, started_at=START + timedelta(minutes=1))],
        [_strava(10)],
    )
    assert len(matches) == 1
    assert matches[0]["strava_activity_id"] == 10


def test_refresh_reads_and_writes_only_the_requested_rider():
    garmin = [_garmin()]
    strava = [_strava()]
    with patch("models.get_garmin_activities_for_matching",
               return_value=garmin) as get_garmin, \
         patch("models.get_strava_activities_for_matching",
               return_value=strava) as get_strava, \
         patch("models.upsert_activity_source_matches") as save:
        count = refresh_activity_matches(42)

    assert count == 1
    get_garmin.assert_called_once_with(42)
    get_strava.assert_called_once_with(42)
    save.assert_called_once()
    assert save.call_args.args[0] == 42


def test_matching_failure_does_not_break_provider_sync(app):
    with app.app_context(), \
         patch("services.activity_matching.refresh_activity_matches",
               side_effect=RuntimeError("derived table unavailable")):
        assert refresh_activity_matches_safely(42) == 0


def test_persistence_replaces_only_automatic_matches():
    conn = Mock()
    cur = conn.cursor.return_value
    match = {
        "rider_id": 42,
        "garmin_activity_id": 1,
        "strava_activity_id": 10,
        "confidence": 0.95,
        "reasons": {"start_delta_minutes": 2},
    }
    with patch("models.get_db", return_value=conn):
        models.upsert_activity_source_matches(42, [match])

    first_sql = cur.execute.call_args_list[0].args[0]
    assert "match_status='auto'" in first_sql
    assert cur.execute.call_args_list[0].args[1] == (42,)
    assert "ON CONFLICT DO NOTHING" in cur.execute.call_args_list[1].args[0]
    conn.commit.assert_called_once_with()


def test_persistence_rejects_foreign_rider_match_and_rolls_back():
    conn = Mock()
    with patch("models.get_db", return_value=conn):
        try:
            models.upsert_activity_source_matches(42, [{
                "rider_id": 999,
                "garmin_activity_id": 1,
                "strava_activity_id": 10,
                "confidence": 1.0,
                "reasons": {},
            }])
        except ValueError:
            pass
        else:
            raise AssertionError("foreign rider match should be rejected")

    conn.rollback.assert_called_once_with()
    conn.commit.assert_not_called()
