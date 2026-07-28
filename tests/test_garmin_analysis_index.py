"""Private analysis index coverage for Garmin-backed brevet Stats."""
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch


def _participation(ride_id=10, ride_date=date(2026, 7, 27)):
    return [{
        "ride_id": ride_id,
        "ride_name": "Garmin 200K",
        "date": ride_date,
        "distance_km": 200,
        "elevation_ft": 6500,
        "finish_time": "12:30",
        "ride_plan_id": 3,
        "status": "FINISHED",
    }]


def _query_cursor(rider):
    cursor = MagicMock()
    cursor.fetchone.return_value = rider
    return cursor


def test_analysis_index_accepts_garmin_without_strava(client):
    rider = {
        "id": 42, "rusa_id": 14680, "first_name": "Private",
        "last_name": "Rider", "photo_filename": None,
    }
    season = {"id": 7, "name": "2025-2026"}
    garmin = {
        "garmin_activity_id": 123,
        "distance_m": Decimal("201000"),
        "duration_s": Decimal("45000"),
        "moving_duration_s": Decimal("39600"),
        "elevation_gain_m": Decimal("2000"),
        "average_hr": 132,
        "average_power": 155,
        "device_name": "Edge 1050",
    }
    with client.session_transaction() as sess:
        sess["user_id"] = 7
        sess["rider_id"] = 42

    with patch("models.get_strava_connection", return_value=None), \
         patch("models.get_garmin_connection",
               return_value={"status": "connected"}), \
         patch("models.get_all_seasons", return_value=[season]), \
         patch("models.get_current_season", return_value=season), \
         patch("models.get_rider_participation",
               return_value=_participation()), \
         patch("models.get_garmin_metrics_for_brevet",
               return_value=garmin), \
         patch("models._execute",
               return_value=_query_cursor(rider)), \
         patch("services.strava_analysis.batch_match_rides",
               return_value={}):
        response = client.get("/my/strava-analysis")

    assert response.status_code == 200
    assert b"Garmin 200K" in response.data
    assert b"Garmin</span>" in response.data
    assert b"Strava</span>" not in response.data
    assert b"124.9 mi" in response.data
    assert b"Plan vs Actual Stats" in response.data
    assert b"View on Strava" not in response.data


def test_analysis_index_requires_at_least_one_activity_source(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 7
        sess["rider_id"] = 42

    rider = {"id": 42, "rusa_id": 14680}
    with patch("models._execute",
               return_value=_query_cursor(rider)), \
         patch("models.get_strava_connection", return_value=None), \
         patch("models.get_garmin_connection", return_value=None):
        response = client.get("/my/strava-analysis")

    assert response.status_code == 302
    assert response.location.endswith("/my-profile")


def test_analysis_index_contract_supports_combined_sources():
    from shared.strava_analysis_index import ride_card

    card = ride_card(
        ride_id=10,
        has_match=True,
        has_strava_match=True,
        has_garmin_match=True,
        sources=["strava", "garmin"],
    )

    assert card["sources"] == ["strava", "garmin"]
    assert card["has_strava_match"] is True
    assert card["has_garmin_match"] is True
