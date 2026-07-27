"""Regression coverage for complete rider-owned integration deletion."""
from unittest.mock import Mock, patch

import models


def _login(client, rider_id=42):
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["rider_id"] = rider_id
        sess["profile_completed"] = True


def _db_with_rowcounts(*rowcounts):
    conn = Mock()
    cur = conn.cursor.return_value
    type(cur).rowcount = property(
        lambda self: rowcounts[min(self.execute.call_count - 1,
                                   len(rowcounts) - 1)])
    return conn, cur


def test_garmin_model_deletes_every_private_table_in_one_transaction():
    conn, cur = _db_with_rowcounts(1, 20, 1, 1)
    with patch("models.get_db", return_value=conn):
        result = models.delete_garmin_connection(42)

    statements = [call.args[0] for call in cur.execute.call_args_list]
    assert statements == [
        "DELETE FROM garmin_mfa_challenge WHERE rider_id = %s",
        "DELETE FROM garmin_activity WHERE rider_id = %s",
        "DELETE FROM garmin_performance_snapshot WHERE rider_id = %s",
        "DELETE FROM garmin_connection WHERE rider_id = %s",
    ]
    assert all(call.args[1] == (42,)
               for call in cur.execute.call_args_list)
    assert result == {
        "connections": 1,
        "challenges": 1,
        "activities": 20,
        "snapshots": 1,
    }
    conn.commit.assert_called_once_with()
    conn.rollback.assert_not_called()


def test_strava_model_deletes_matches_before_activities_and_tokens():
    conn, cur = _db_with_rowcounts(2, 30, 1)
    with patch("models.get_db", return_value=conn):
        result = models.delete_strava_connection(42)

    statements = [call.args[0] for call in cur.execute.call_args_list]
    assert statements == [
        "DELETE FROM strava_ride_match WHERE rider_id = %s",
        "DELETE FROM strava_activity WHERE rider_id = %s",
        "DELETE FROM strava_connection WHERE rider_id = %s",
    ]
    assert all(call.args[1] == (42,)
               for call in cur.execute.call_args_list)
    assert result == {"connections": 1, "activities": 30, "matches": 2}
    conn.commit.assert_called_once_with()


def test_strava_disconnect_is_session_scoped_and_deletes_after_revocation(client):
    _login(client, rider_id=42)
    connection = {"access_token": "private-token"}
    with patch("routes.strava.models.get_strava_connection",
               return_value=connection), \
         patch("routes.strava.deauthorize_strava") as revoke, \
         patch("routes.strava.models.delete_strava_connection") as delete:
        response = client.post(
            "/strava/disconnect",
            data={"rider_id": "999", "confirm_delete": "DELETE"},
        )

    assert response.status_code == 302
    revoke.assert_called_once_with("private-token")
    delete.assert_called_once_with(42)


def test_strava_disconnect_requires_explicit_confirmation(client):
    _login(client)
    with patch("routes.strava.models.get_strava_connection") as get_connection, \
         patch("routes.strava.models.delete_strava_connection") as delete:
        response = client.post("/strava/disconnect")

    assert response.status_code == 302
    get_connection.assert_not_called()
    delete.assert_not_called()


def test_strava_remote_revocation_failure_still_deletes_locally(client):
    _login(client, rider_id=42)
    with patch("routes.strava.models.get_strava_connection",
               return_value={"access_token": "private-token"}), \
         patch("routes.strava.deauthorize_strava",
               side_effect=RuntimeError("Strava unavailable")), \
         patch("routes.strava.models.delete_strava_connection") as delete:
        response = client.post(
            "/strava/disconnect",
            data={"confirm_delete": "DELETE"},
        )

    assert response.status_code == 302
    delete.assert_called_once_with(42)


def test_garmin_model_rolls_back_atomic_deletion_on_failure():
    conn, cur = _db_with_rowcounts(1)
    cur.execute.side_effect = [None, RuntimeError("database unavailable")]
    with patch("models.get_db", return_value=conn):
        try:
            models.delete_garmin_connection(42)
        except RuntimeError:
            pass
        else:
            raise AssertionError("delete_garmin_connection should raise")

    conn.rollback.assert_called_once_with()
    conn.commit.assert_not_called()


def test_strava_model_rolls_back_atomic_deletion_on_failure():
    conn, cur = _db_with_rowcounts(1)
    cur.execute.side_effect = [None, RuntimeError("database unavailable")]
    with patch("models.get_db", return_value=conn):
        try:
            models.delete_strava_connection(42)
        except RuntimeError:
            pass
        else:
            raise AssertionError("delete_strava_connection should raise")

    conn.rollback.assert_called_once_with()
    conn.commit.assert_not_called()
