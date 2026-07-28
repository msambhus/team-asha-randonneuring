"""Split-provider brevet matching and duration-weighted Stats."""
from datetime import datetime, timezone

from services.activity_matching import (
    aggregate_garmin_recordings,
    aggregate_strava_recordings,
    build_provider_metric_comparison,
    split_brevet_auto_matches,
)
from unittest.mock import patch


def _garmin(activity_id, hour, distance, duration=18000):
    return {
        "rider_id": 42,
        "garmin_activity_id": activity_id,
        "activity_type": "cycling",
        "activity_name": f"Part {activity_id}",
        "started_at": datetime(2026, 7, 27, hour, tzinfo=timezone.utc),
        "distance_m": distance,
        "duration_s": duration,
        "moving_duration_s": duration - 1200,
        "elevation_gain_m": 1000,
        "average_hr": 120 + activity_id,
        "max_hr": 160 + activity_id,
        "average_power": 140 + activity_id,
        "max_power": 500 + activity_id,
        "normalized_power": 150 + activity_id,
        "aerobic_training_effect": 3.5 + activity_id / 10,
        "anaerobic_training_effect": 0.5,
        "calories": 1500,
        "average_cadence": 70,
        "device_name": "Edge",
    }


def test_unique_contiguous_garmin_parts_auto_link_to_one_brevet():
    brevets = [{
        "ride_id": 10, "date": "2026-07-27", "distance_km": 200,
    }]
    matches = split_brevet_auto_matches(
        [_garmin(1, 6, 98000), _garmin(2, 12, 101000)],
        brevets,
        "garmin",
    )

    assert len(matches) == 2
    assert {row["garmin_activity_id"] for row in matches} == {1, 2}
    assert {row["ride_id"] for row in matches} == {10}
    assert all(row["reasons"]["split_recording"] for row in matches)
    assert [row["reasons"]["part_number"] for row in matches] == [1, 2]


def test_unique_contiguous_strava_parts_use_strava_provenance():
    activities = [
        {
            "rider_id": 42, "strava_activity_id": 11,
            "activity_type": "Ride", "start_date": "2026-07-27T06:00:00Z",
            "distance": 99000, "elapsed_time": 18000,
        },
        {
            "rider_id": 42, "strava_activity_id": 12,
            "activity_type": "Ride", "start_date": "2026-07-27T12:00:00Z",
            "distance": 101000, "elapsed_time": 18000,
        },
    ]
    matches = split_brevet_auto_matches(
        activities,
        [{"ride_id": 10, "date": "2026-07-27", "distance_km": 200}],
        "strava",
    )
    assert len(matches) == 2
    assert {row["strava_activity_id"] for row in matches} == {11, 12}
    assert all(row["garmin_activity_id"] is None for row in matches)
    assert all(row["reasons"]["provider"] == "strava" for row in matches)


def test_ambiguous_split_group_is_not_auto_linked():
    brevets = [{
        "ride_id": 10, "date": "2026-07-27", "distance_km": 200,
    }]
    # Parts 1+2 and parts 2+3 both satisfy the brevet distance.
    matches = split_brevet_auto_matches(
        [_garmin(1, 2, 100000), _garmin(2, 7, 100000),
         _garmin(3, 12, 100000)],
        brevets,
        "garmin",
    )
    assert matches == []


def test_split_group_rejects_large_recording_gap():
    brevets = [{
        "ride_id": 10, "date": "2026-07-27", "distance_km": 200,
    }]
    first = _garmin(1, 1, 100000, duration=3600)
    second = _garmin(2, 23, 100000, duration=3600)
    assert split_brevet_auto_matches(
        [first, second], brevets, "garmin") == []


def test_garmin_aggregation_weights_sensors_and_spans_file_gap():
    parts = [_garmin(1, 6, 98000), _garmin(2, 12, 101000)]
    result = aggregate_garmin_recordings(parts)

    assert result["recording_count"] == 2
    assert result["distance_m"] == 199000
    assert result["moving_duration_s"] == 33600
    # 06:00 through 17:00, including the one-hour gap between five-hour files.
    assert result["duration_s"] == 39600
    assert round(result["average_hr"], 1) == 121.5
    assert result["max_hr"] == 162
    assert result["calories"] == 3000
    assert len(result["recording_parts"]) == 2


def test_strava_aggregation_preserves_links_and_weighted_values():
    rows = [
        {
            "strava_activity_id": 11, "name": "Part 1",
            "start_date": "2026-07-27T06:00:00Z",
            "distance": 100000, "elapsed_time": 18000,
            "moving_time": 16800, "total_elevation_gain": 900,
            "average_heartrate": 130, "average_watts": 150,
            "strava_url": "https://strava.test/11",
        },
        {
            "strava_activity_id": 12, "name": "Part 2",
            "start_date": "2026-07-27T12:00:00Z",
            "distance": 101000, "elapsed_time": 18000,
            "moving_time": 16800, "total_elevation_gain": 1100,
            "average_heartrate": 140, "average_watts": 170,
            "strava_url": "https://strava.test/12",
        },
    ]
    result = aggregate_strava_recordings(rows)

    assert result["recording_count"] == 2
    assert round(result["actual_distance_miles"], 1) == 124.9
    assert result["actual_elapsed_time_min"] == 660
    assert result["average_heartrate"] == 135
    assert result["average_watts"] == 160
    assert result["recording_parts"][1]["strava_url"].endswith("/12")


def test_sensor_discrepancies_preserve_both_provider_values():
    rows = build_provider_metric_comparison(
        {
            "avg_hr": 130,
            "max_hr": 170,
            "avg_watts": 150,
            "weighted_avg_watts": 165,
            "max_watts": 600,
            "average_cadence": 72,
        },
        {
            "average_hr": 142,
            "max_hr": 182,
            "average_power": 175,
            "normalized_power": 190,
            "max_power": 720,
            "average_cadence": 80,
        },
    )
    by_key = {row["key"]: row for row in rows}
    assert by_key["average_hr"]["strava"] == 130
    assert by_key["average_hr"]["garmin"] == 142
    assert by_key["average_hr"]["material"] is True
    assert by_key["normalized_power"]["delta"] == 25
    assert by_key["cadence"]["material"] is True


def test_stats_template_explains_split_and_recovery_context():
    from pathlib import Path
    root = Path(__file__).parents[1]
    page = (root / "templates" / "strava_ride_analysis.html").read_text()
    context = (
        root / "templates" / "_unified_provider_context.html"
    ).read_text()

    assert "Unified Brevet Stats" in page
    assert "Combined Strava Recording Summary" in page
    assert "Split Recording Group" in context
    assert "Garmin Recovery Context" in context
    assert "recorded on this ride date" in context


def test_refresh_persists_both_provider_split_parts():
    garmin = [_garmin(1, 6, 98000), _garmin(2, 12, 101000)]
    strava = [
        {
            "rider_id": 42, "strava_activity_id": 11,
            "activity_type": "Ride", "start_date": "2026-07-27T06:00:00Z",
            "distance": 99000, "elapsed_time": 18000,
        },
        {
            "rider_id": 42, "strava_activity_id": 12,
            "activity_type": "Ride", "start_date": "2026-07-27T12:00:00Z",
            "distance": 101000, "elapsed_time": 18000,
        },
    ]
    brevet = [{"ride_id": 10, "date": "2026-07-27", "distance_km": 200}]
    with patch("models.get_authoritative_brevet_source_links",
               return_value=[]), \
         patch("models.get_garmin_activities_for_matching",
               return_value=garmin), \
         patch("models.get_strava_activities_for_matching",
               return_value=strava), \
         patch("models.get_finished_brevets_for_matching",
               return_value=brevet), \
         patch("models.replace_activity_brevet_matches") as replace:
        from services.activity_matching import refresh_brevet_matches
        count = refresh_brevet_matches(42)

    assert count == 4
    saved = replace.call_args.args[1]
    assert {row.get("garmin_activity_id") for row in saved} == {None, 1, 2}
    assert {row.get("strava_activity_id") for row in saved} == {None, 11, 12}
