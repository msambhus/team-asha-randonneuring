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
         patch("routes.auth.models.get_garmin_performance_history_summary",
               return_value={"days_captured": 0, "first_date": None,
                             "latest_date": None}), \
         patch("routes.auth.render_template", return_value="profile ok"):
        response = client.get("/auth/my-profile")

    assert response.status_code == 200
    assert response.data == b"profile ok"
    get_garmin.assert_called_once_with(42)


def test_current_garmin_recovery_renders_on_my_profile_without_recent_rides(client, app):
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
    with patch("routes.auth.models._execute", return_value=rider_cursor), \
         patch("routes.auth.models.get_rider_career_stats",
               return_value={"total_rides": 1, "total_kms": 200}), \
         patch("routes.auth.models.get_rider_total_srs", return_value=0), \
         patch("routes.auth.models.get_strava_connection", return_value=None), \
         patch("routes.auth.models.get_garmin_connection",
               return_value={"rider_id": 42, "status": "connected"}), \
         patch("routes.auth.models.get_latest_garmin_performance_snapshot",
               return_value={
                   "snapshot_date": "2026-07-27",
                   "training_readiness": Decimal("74"),
                   "readiness_level": "HIGH",
                   "readiness_feedback": "READY",
                   "recovery_time_minutes": Decimal("90"),
                   "endurance_score": Decimal("7120"),
                   "acute_training_load": Decimal("642"),
                   "load_level_trend": "MAINTAINING",
               }), \
         patch("routes.auth.models.get_garmin_performance_history_summary",
               return_value={"days_captured": 12,
                             "first_date": "2026-07-16",
                             "latest_date": "2026-07-27"}):
            response = client.get("/auth/my-profile")

    assert response.status_code == 200
    assert b"Recent Garmin rides" not in response.data
    assert b"Endurance" in response.data
    assert b"7120" in response.data
    assert b"1.5h recovery" in response.data
    assert b"Load maintaining" in response.data
    assert b'id="garmin-sync-progress"' in response.data
    assert b'aria-live="polite"' in response.data
    assert b"Syncing Garmin performance and recent rides" in response.data
    assert b"button.disabled = true" in response.data
    assert b"Disconnect &amp; delete Garmin data" in response.data
    assert b'name="confirm_delete" value="DELETE"' in response.data
    assert b"permanently delete its encrypted tokens" in response.data
    assert b"12 daily snapshots stored" in response.data
    assert b"Backfill Garmin history" in response.data


def test_my_rides_collapses_explicit_garmin_strava_match(client, app):
    app.config["GARMIN_TOKEN_ENCRYPTION_KEY"] = "configured-for-test"
    with client.session_transaction() as sess:
        sess["user_id"] = 7
        sess["rider_id"] = 42

    strava = {
        "strava_activity_id": 101, "name": "Morning Ride",
        "start_date_local": "2026-07-27T06:00:00", "distance": 16093.44,
        "elapsed_time": 3600, "moving_time": 3400,
        "activity_type": "Ride", "total_elevation_gain": 300,
    }
    garmin = {
        "garmin_activity_id": 202, "strava_activity_id": 101,
        "activity_name": "Cycling", "strava_name": "Morning Ride",
        "started_at": "2026-07-27T06:00:02", "distance_m": 16093.44,
        "ride_id": 3090, "ride_name": "SCR 400K",
    }

    with patch("routes.auth.models.get_rider_by_id",
               return_value={"id": 42, "rusa_id": 12345}), \
         patch("routes.auth.models.get_strava_activities",
               return_value=[strava]), \
         patch("routes.auth.models.get_garmin_brevet_match_review",
               return_value=[garmin]), \
         patch("routes.auth.models.get_garmin_connection",
               return_value={"rider_id": 42}), \
         patch("routes.auth.models.get_rider_upcoming_signups",
               return_value=[{
                   "id": 3091, "name": "SCR 200K",
                   "date": "2026-08-15", "distance_km": 200,
                   "distance_miles": None, "elevation_ft": 5000,
                   "signup_status": "GOING", "plan_slug": "scr-200k",
               }]), \
         patch("routes.auth.models.get_strava_connection",
               return_value={"rider_id": 42}), \
         patch("routes.auth.models.get_current_season",
               return_value={"name": "2025-2026"}), \
         patch("routes.auth.models.get_all_seasons",
               return_value=[]):
        response = client.get("/auth/my-rides")

    assert response.status_code == 200
    assert response.data.count(b"SCR 400K") == 1
    assert b"Strava" in response.data
    assert b"Garmin" in response.data
    assert b"Timeline" in response.data
    assert b"Calendar" in response.data
    assert b"Brevet" in response.data
    assert b"SCR 400K" in response.data
    assert b"SCR 200K" in response.data
    assert b"<strong>200</strong> km" in response.data
    assert b"Workout rating" in response.data
    assert b"Readiness score" in response.data
    assert b'id="calendar-grid"' in response.data
    assert b'data-calendar-kind="brevet"' in response.data
    assert b'data-calendar-type="Ride"' in response.data
    assert b"activityIcon" in response.data
    assert b"AI coaching" in response.data
    assert b"Loading personalized coaching" in response.data
    assert b"/rider/12345/advice" in response.data
    assert b"pointerenter" in response.data
    assert response.data.index(b'id="calendar-grid"') < response.data.index(
        b'id="upcoming-brevets"')
