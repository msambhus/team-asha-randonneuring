"""Regression coverage for the key-enabled private Garmin profile path."""
from decimal import Decimal
from unittest.mock import MagicMock, patch


def test_my_profile_reads_garmin_when_encryption_key_is_configured(client, app):
    app.config["GARMIN_TOKEN_ENCRYPTION_KEY"] = "configured-for-test"
    with client.session_transaction() as sess:
        sess["user_id"] = 7
        sess["rider_id"] = 42
        sess["email"] = "rider@example.com"

    rider_cursor = MagicMock()
    rider_cursor.fetchone.return_value = {
        "id": 42, "rusa_id": 12345, "first_name": "Test",
        "last_name": "Rider", "photo_filename": None, "bio": None,
        "pbp_2023_registered": False, "pbp_2023_status": None,
        "strava_data_private": True,
    }
    garmin_connection = {"rider_id": 42, "status": "connected"}

    with patch("routes.auth.models._execute", return_value=rider_cursor), \
         patch("routes.auth.models.get_rider_career_stats",
               return_value={"total_rides": 1, "total_kms": 200}), \
         patch("routes.auth.models.get_rider_total_srs", return_value=0), \
         patch("routes.auth.models.get_strava_connection", return_value=None), \
         patch("routes.auth.models.get_garmin_connection",
               return_value=garmin_connection) as get_garmin, \
         patch("routes.auth.models.get_latest_garmin_performance_snapshot",
               return_value=None), \
         patch("routes.auth.models.get_recent_garmin_activities",
               return_value=[]) as get_activities, \
         patch("routes.auth.render_template", return_value="profile ok"):
        response = client.get("/auth/my-profile")

    assert response.status_code == 200
    assert response.data == b"profile ok"
    get_garmin.assert_called_once_with(42)
    get_activities.assert_called_once_with(42, limit=10)


def test_garmin_decimal_distance_renders_on_my_profile(client, app):
    app.config["GARMIN_TOKEN_ENCRYPTION_KEY"] = "configured-for-test"
    with client.session_transaction() as sess:
        sess["user_id"] = 7
        sess["rider_id"] = 42
        sess["email"] = "rider@example.com"

    rider_cursor = MagicMock()
    rider_cursor.fetchone.return_value = {
        "id": 42, "rusa_id": 12345, "first_name": "Test",
        "last_name": "Rider", "photo_filename": None, "bio": None,
        "pbp_2023_registered": False, "pbp_2023_status": None,
        "strava_data_private": True,
    }
    activity = {
        "activity_name": "Garmin Decimal Ride",
        "started_at": None,
        "distance_m": Decimal("16093.44"),
        "average_hr": Decimal("132"),
        "normalized_power": Decimal("177"),
        "average_power": Decimal("154"),
        "aerobic_training_effect": Decimal("4.2"),
    }

    with patch("routes.auth.models._execute", return_value=rider_cursor), \
         patch("routes.auth.models.get_rider_career_stats",
               return_value={"total_rides": 1, "total_kms": 200}), \
         patch("routes.auth.models.get_rider_total_srs", return_value=0), \
         patch("routes.auth.models.get_strava_connection", return_value=None), \
         patch("routes.auth.models.get_garmin_connection",
               return_value={"rider_id": 42, "status": "connected"}), \
         patch("routes.auth.models.get_latest_garmin_performance_snapshot",
               return_value=None), \
         patch("routes.auth.models.get_recent_garmin_activities",
               return_value=[activity]):
        response = client.get("/auth/my-profile")

    assert response.status_code == 200
    assert b"Garmin Decimal Ride" in response.data
    assert b"10.0" in response.data
