"""Private Garmin additions to the existing Team Asha brevet Stats contract."""
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import models
from services.activity_matching import build_garmin_brevet_summary


def test_garmin_metrics_lookup_is_owned_and_contains_no_raw_payload():
    cursor = MagicMock()
    cursor.fetchall.return_value = [{
        "garmin_activity_id": 123,
        "normalized_power": 177,
        "device_name": "Edge",
    }]
    with patch("models._execute", return_value=cursor) as execute:
        result = models.get_garmin_metrics_for_brevet(42, 10)

    sql, params = execute.call_args_list[0].args
    assert params == (42, 10)
    assert "abm.rider_id=%s AND abm.ride_id=%s" in sql
    assert "raw_ciphertext" not in sql
    assert result["normalized_power"] == 177


def test_ride_date_recovery_lookup_is_owned_and_date_scoped():
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "snapshot_date": "2026-07-27", "sleep_score": 80,
    }
    with patch("models._execute", return_value=cursor) as execute:
        result = models.get_garmin_performance_snapshot_for_date(
            42, "2026-07-27")

    sql, params = execute.call_args.args
    assert "rider_id=%s AND snapshot_date=%s" in sql
    assert params == (42, "2026-07-27")
    assert result["sleep_score"] == 80


def test_stats_template_labels_garmin_without_replacing_strava_contract():
    template = (
        Path(__file__).parents[1] / "templates" / "strava_ride_analysis.html"
    ).read_text()
    assert "Garmin Device Metrics" in template
    assert "Supplemental device-recorded values" in template
    assert "Strava remains the source for the route, stops, and segment" in template
    assert "{% if is_own_profile and garmin_metrics %}" in template
    assert "{% if is_own_profile and provider_comparison %}" in template
    assert "Recording Source Comparison" in template
    assert "Device temperature:" in template
    assert "View on Strava" in template
    assert "Route Map" in template
    assert "Plan vs Actual Summary" in template
    lap_partial = (
        Path(__file__).parents[1] / "templates" / "_garmin_lap_details.html"
    ).read_text()
    assert "Garmin Lap Details" in lap_partial
    assert "Private device-recorded laps" in lap_partial
    assert "do not replace Strava route, stop, or control analysis" in lap_partial


def test_sram_stats_are_private_and_provider_attributed():
    template = (
        Path(__file__).parents[1] / "templates" / "_sram_axs_stats.html"
    ).read_text()
    assert "{% if is_own_profile and sram_metrics %}" in template
    assert "SRAM AXS Drivetrain" in template
    assert "Rear gear-position distribution" in template
    assert "Drivetrain observations" in template
    assert "not cassette tooth counts" in template
    assert "Source: SRAM AXS Web" in template
    stats_template = (
        Path(__file__).parents[1] / "templates" / "strava_ride_analysis.html"
    ).read_text()
    assert "partials/_sram_axs_status.html" in stats_template


def test_sram_metrics_lookup_accepts_unified_provider_identity():
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "sram_activity_id": "axs-1",
        "duration_s": 3600,
        "rear_shift_count": 12,
        "front_shift_count": 0,
        "gear_summary": {},
        "components": [],
    }
    with patch("models._execute", return_value=cursor) as execute:
        result = models.get_sram_axs_metrics_for_activity(
            42, ride_id=10, strava_activity_id=999)

    sql, params = execute.call_args.args
    assert "m.rider_id=%s" in sql
    assert "m.strava_activity_id=%s" in sql
    assert params == (42, 10, 999, 999, None, None)
    assert result["coaching"]["total_shifts"] == 12
    assert result["coaching"]["shifts_per_hour"] == 12.0


def test_sram_detail_backfill_prioritizes_brevet_matches():
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    with patch("models._execute", return_value=cursor) as execute:
        models.get_sram_axs_detail_backfill_candidates(42, limit=5)

    sql, params = execute.call_args.args
    assert "ORDER BY (m.ride_id IS NULL),a.started_at DESC" in sql
    assert params == (42, 5)


def test_stats_template_rounds_decimal_garmin_metrics():
    template = (
        Path(__file__).parents[1] / "templates" / "strava_ride_analysis.html"
    ).read_text()
    assert template.count(
        "(value|round(0)|int) if suffix else (value|round(2))"
    ) == 2


def test_garmin_lap_lookup_is_owned_and_excludes_encrypted_payload():
    cursor = MagicMock()
    cursor.fetchall.return_value = [{
        "garmin_activity_id": 123,
        "laps": [{"lap_number": 1, "distance_m": 10000}],
        "synced_at": "now",
    }]
    with patch("models._execute", return_value=cursor) as execute:
        result = models.get_garmin_laps_for_brevet(42, 10)

    sql, params = execute.call_args.args
    assert params == (42, 10)
    assert "abm.rider_id=%s AND abm.ride_id=%s" in sql
    assert "raw_ciphertext" not in sql
    assert result == [{
        "lap_number": 1,
        "distance_m": 10000,
        "recording_number": 1,
        "garmin_activity_id": 123,
    }]


def test_garmin_only_summary_compares_brevet_and_device_headlines():
    summary = build_garmin_brevet_summary(
        {
            "distance_km": Decimal("200"),
            "time_limit_hours": Decimal("13.5"),
        },
        {
            "distance_m": Decimal("205000"),
            "duration_s": Decimal("45000"),
            "moving_duration_s": Decimal("39600"),
        },
    )

    assert summary == {
        "planned_distance_miles": 124.3,
        "actual_distance_miles": 127.4,
        "distance_delta_miles": 3.1,
        "elapsed_time_min": 750,
        "moving_time_min": 660,
        "stopped_time_min": 90,
        "official_limit_min": 810,
        "limit_margin_min": 60,
        "average_moving_speed_mph": 11.6,
    }


def test_garmin_only_stats_route_is_owner_only(client):
    rider = {
        "id": 42, "rusa_id": 14680, "first_name": "Private",
        "last_name": "Rider", "strava_data_private": False,
    }
    ride = {
        "id": 10, "name": "Garmin 200K", "date": "2026-07-27",
        "distance_km": 200, "time_limit_hours": 13.5,
    }
    garmin = {
        "garmin_activity_id": 123, "distance_m": 201000,
        "duration_s": 45000, "moving_duration_s": 39600,
        "normalized_power": 177, "device_name": "Edge 1050",
    }

    with client.session_transaction() as sess:
        sess["rider_id"] = 42
        sess["user_id"] = 7
    with patch("routes.riders.get_rider_by_rusa", return_value=rider), \
         patch("models.get_ride_by_id_full", return_value=ride), \
         patch("models.get_strava_ride_match", return_value=None), \
         patch("services.strava_analysis.find_matching_activity",
               return_value=None), \
         patch("models.get_garmin_metrics_for_brevet",
               return_value=garmin) as get_garmin, \
         patch("models.get_garmin_laps_for_brevet", return_value=[]), \
         patch("models.get_garmin_performance_snapshot_for_date",
               return_value={
                   "training_status": "NO_STATUS_AER_LOW_SHORT",
               }) as get_recovery, \
         patch("models.get_strava_recordings_for_brevet",
               return_value=[]):
        response = client.get(
            "/rider/14680/ride/10/strava-analysis")

    assert response.status_code == 200
    assert b"Unified Brevet Stats" in response.data
    assert b"Brevet Plan vs Garmin Recording" in response.data
    assert b"Edge 1050" in response.data
    assert b"Route maps, detected stops" in response.data
    assert b"No Training Status" in response.data
    assert b"Garmin needs more qualifying aerobic activity data." in response.data
    assert b"No Status Aer Low Short" not in response.data
    get_garmin.assert_called_once_with(42, 10)
    get_recovery.assert_called_once_with(42, "2026-07-27")

    with client.session_transaction() as sess:
        sess.clear()
    with patch("routes.riders.get_rider_by_rusa", return_value=rider), \
         patch("models.get_ride_by_id_full", return_value=ride), \
         patch("models.get_strava_ride_match", return_value=None), \
         patch("services.strava_analysis.find_matching_activity",
               return_value=None), \
         patch("models.get_garmin_metrics_for_brevet") as get_garmin:
        response = client.get(
            "/rider/14680/ride/10/strava-analysis")

    assert response.status_code == 200
    assert b"Unified Brevet Stats" not in response.data
    assert b"No Matching Strava Activity" in response.data
    get_garmin.assert_not_called()
