"""Provider-sourced performance highlights on the private profile."""
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from shared.strava import fetch_athlete


def test_fetch_athlete_uses_authenticated_strava_endpoint():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"id": 99, "ftp": 265}
    with patch("shared.strava.requests.get", return_value=response) as get:
        result = fetch_athlete("secret", api_base="https://strava.test/api/v3")

    assert result["ftp"] == 265
    get.assert_called_once_with(
        "https://strava.test/api/v3/athlete",
        headers={"Authorization": "Bearer secret"},
        timeout=10,
    )
    response.raise_for_status.assert_called_once()


def test_sync_athlete_profile_persists_only_provider_ftp(app):
    connection = {
        "rider_id": 42,
        "access_token": "token",
        "refresh_token": "refresh",
        "expires_at": 9999999999,
    }
    with app.app_context(), \
         patch("services.strava._shared.fetch_athlete",
               return_value={"id": 99, "ftp": 272, "weight": 70}), \
         patch("models.update_strava_athlete_metrics") as update:
        from services.strava import sync_athlete_profile
        athlete = sync_athlete_profile(connection)

    assert athlete["ftp"] == 272
    update.assert_called_once_with(42, ftp=272)


def test_ftp_persistence_is_owned_and_invalidates_connection_cache():
    connection = MagicMock()
    cursor = connection.cursor.return_value
    with patch("models.get_db", return_value=connection), \
         patch("models.cache.delete_memoized") as invalidate:
        from models import get_strava_connection, update_strava_athlete_metrics
        update_strava_athlete_metrics(42, ftp=268)

    cursor.execute.assert_called_once_with(
        "UPDATE strava_connection SET ftp = %s WHERE rider_id = %s",
        (268, 42),
    )
    connection.commit.assert_called_once()
    invalidate.assert_called_once_with(get_strava_connection, 42)


def test_private_profile_highlights_all_available_sources(client, app):
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
    strava_connection = {
        "rider_id": 42, "ftp": Decimal("268"),
        "last_sync_at": datetime.now(),
    }
    fitness = {
        "total": 73, "frequency": 20, "volume": 28,
        "intensity": 14, "recency": 11,
    }
    snapshot = {
        "snapshot_date": "2026-07-27",
        "sleep_score": Decimal("84"),
        "body_battery": Decimal("71"),
        "training_readiness": Decimal("76"),
        "hrv_status": "BALANCED",
        "resting_heart_rate": Decimal("48"),
        "vo2_max_cycling": Decimal("54"),
        "recovery_time_minutes": Decimal("120"),
        "endurance_score": Decimal("7100"),
        "acute_training_load": Decimal("622"),
        "training_status": "NO_STATUS_AER_LOW_SHORT",
        "readiness_level": "HIGH",
        "readiness_feedback": "READY",
        "load_level_trend": "MAINTAINING",
    }

    with patch("routes.auth.models._execute", return_value=rider_cursor), \
         patch("routes.auth.models.get_rider_career_stats",
               return_value={"total_rides": 1, "total_kms": 200}), \
         patch("routes.auth.models.get_rider_total_srs", return_value=0), \
         patch("routes.auth.models.get_strava_connection",
               return_value=strava_connection), \
         patch("routes.auth.models.get_strava_activities_for_calendar",
               return_value=[{
                   "strava_activity_id": 123, "name": "Training Ride",
                   "activity_type": "Ride", "distance": 50000,
                   "moving_time": 7200, "total_elevation_gain": 500,
                   "start_date_local": "2026-07-27T08:00:00",
                   "activity_date": "2026-07-27",
                   "has_heartrate": True, "average_heartrate": 135,
                   "device_watts": True, "average_watts": 165,
                   "strava_url": "https://www.strava.com/activities/123",
               }]), \
         patch("services.fitness.calculate_fitness_score",
               return_value=fitness), \
         patch("routes.auth.models.get_garmin_connection",
               return_value={"rider_id": 42, "status": "connected"}), \
         patch("routes.auth.models.get_latest_garmin_performance_snapshot",
               return_value=snapshot), \
         patch("routes.auth.models.get_recent_garmin_activities",
               return_value=[]):
        response = client.get("/auth/my-profile")

    assert response.status_code == 200
    assert b"Performance &amp; Recovery" in response.data
    assert b"Sleep Score" in response.data
    assert b"Body Battery" in response.data
    assert b"Fitness Score" in response.data
    assert b"Team Asha from Strava" in response.data
    assert b"Cycling VO" in response.data
    assert b"FTP" in response.data
    assert b"268" in response.data
    assert b"Strava athlete profile" in response.data
    assert b"Training Readiness" in response.data
    assert b"HRV Status" in response.data
    assert b"No Training Status" in response.data
    assert b"Garmin needs more qualifying aerobic activity data." in response.data
    assert b"No Status Aer Low Short" not in response.data
