from unittest.mock import MagicMock, patch

import models


def test_manual_match_rejects_foreign_garmin_activity():
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchone.return_value = None
    with patch("models.get_db", return_value=conn):
        try:
            models.set_manual_garmin_brevet_match(42, 991, 10)
            assert False, "foreign activity should be rejected"
        except ValueError as exc:
            assert "does not belong" in str(exc)
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_manual_match_rejects_unfinished_or_foreign_brevet():
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchone.side_effect = [{"owned": True}, None]
    with patch("models.get_db", return_value=conn):
        try:
            models.set_manual_garmin_brevet_match(42, 991, 10)
            assert False, "foreign brevet should be rejected"
        except ValueError as exc:
            assert "does not belong" in str(exc)
    assert cursor.execute.call_args_list[1].args[1] == (
        42, 10, models.RideStatus.FINISHED.value)
    conn.rollback.assert_called_once()


def test_manual_match_carries_owned_strava_provenance():
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchone.side_effect = [
        {"owned": True},
        {"finished": True},
        {"id": 7, "strava_activity_id": 88},
    ]
    with patch("models.get_db", return_value=conn):
        models.set_manual_garmin_brevet_match(42, 991, 10)
    insert = cursor.execute.call_args_list[-1]
    assert insert.args[1][:5] == (42, 10, 7, 991, 88)
    conn.commit.assert_called_once()


def test_match_routes_require_profile(client):
    assert client.get("/garmin/ride-matches").status_code in (302, 401)
    assert client.post(
        "/garmin/ride-matches/991",
        data={"action": "link", "ride_id": "10"},
    ).status_code in (302, 401)


def test_owned_rider_can_open_private_match_review(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["rider_id"] = 42
    with patch("services.activity_matching.refresh_activity_matches_safely"
               ) as refresh, \
         patch("models.get_garmin_brevet_match_review",
               return_value=[]) as activities, \
         patch("models.get_finished_brevets_for_matching",
               return_value=[]) as brevets:
        response = client.get("/garmin/ride-matches")
    assert response.status_code == 200
    refresh.assert_called_once_with(42)
    activities.assert_called_once_with(42)
    brevets.assert_called_once_with(42)


def test_link_route_passes_only_session_owner_to_model(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["rider_id"] = 42
    with patch("models.set_manual_garmin_brevet_match") as save:
        response = client.post(
            "/garmin/ride-matches/991",
            data={"action": "link", "ride_id": "10"},
        )
    assert response.status_code == 302
    save.assert_called_once_with(42, 991, 10)


def test_unlink_route_passes_only_session_owner_to_model(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["rider_id"] = 42
    with patch("models.reject_garmin_brevet_match",
               return_value=True) as reject:
        response = client.post(
            "/garmin/ride-matches/991",
            data={"action": "unlink"},
        )
    assert response.status_code == 302
    reject.assert_called_once_with(42, 991)


def test_review_query_includes_paired_strava_details_without_raw_payload():
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    with patch("models._execute", return_value=cursor) as execute:
        models.get_garmin_brevet_match_review(42)
    sql, params = execute.call_args.args
    assert params == (42, 50)
    assert "activity_source_match asm" in sql
    assert "strava_activity sa" in sql
    assert "sa.elapsed_time AS strava_elapsed_s" in sql
    assert "asm.reasons AS source_reasons" in sql
    assert "raw_ciphertext" not in sql


def test_review_template_shows_garmin_and_strava_recording_details():
    from pathlib import Path
    template = (
        Path(__file__).parents[1] / "templates" / "garmin_ride_matches.html"
    ).read_text()
    assert "Garmin recording" in template
    assert "Strava matched" in template
    assert "strava_distance_m" in template
    assert "source_confidence" in template
    assert "start_delta_minutes" in template
