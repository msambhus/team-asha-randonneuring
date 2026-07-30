"""SRAM reconnect must renew tokens without deleting imported ride data."""
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet


def _login(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 42
        sess["rider_id"] = 42


def _configure(client):
    client.application.config["SRAM_AXS_TOKEN_ENCRYPTION_KEY"] = (
        Fernet.generate_key().decode())


def test_active_sram_connection_still_redirects_without_renewal(client):
    _login(client)
    _configure(client)
    with patch("models.get_sram_axs_connection", return_value={
        "rider_id": 42,
        "status": "connected",
        "display_name": "rider@example.com",
    }):
        response = client.get("/sram-axs/connect")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/my-profile")


def test_expired_sram_connection_shows_reconnect_form(client):
    _login(client)
    _configure(client)
    with patch("models.get_sram_axs_connection", return_value={
        "rider_id": 42,
        "status": "reauth_required",
        "display_name": "rider@example.com",
    }):
        response = client.get("/sram-axs/connect")

    assert response.status_code == 200
    assert b"Reconnect SRAM AXS" in response.data
    assert b"rider@example.com" in response.data
    assert b"Renew SRAM AXS Session" in response.data


def test_sram_reconnect_replaces_token_without_deleting_data(client):
    _login(client)
    _configure(client)
    connection = {
        "rider_id": 42,
        "status": "reauth_required",
        "display_name": "rider@example.com",
    }
    sram_client = MagicMock()
    sram_client.dump_tokens.return_value = (
        '{"access_token":"new","expires_at":9999999999}')
    sram_client.display_name.return_value = "rider@example.com"

    with patch("models.get_sram_axs_connection", return_value=connection), \
            patch("routes.sram_axs.SramAxsClient",
                  return_value=sram_client), \
            patch("models.upsert_sram_axs_connection") as upsert, \
            patch("models.delete_sram_axs_connection") as delete:
        response = client.post("/sram-axs/connect", data={
            "email": "rider@example.com",
            "password": "request-only-password",
        })

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/my-profile")
    sram_client.login.assert_called_once_with(
        "rider@example.com", "request-only-password")
    upsert.assert_called_once()
    assert upsert.call_args.args[0] == 42
    assert "request-only-password" not in str(upsert.call_args)
    delete.assert_not_called()


def test_connected_rider_can_proactively_renew_with_query_flag(client):
    _login(client)
    _configure(client)
    with patch("models.get_sram_axs_connection", return_value={
        "rider_id": 42,
        "status": "connected",
        "display_name": "rider@example.com",
    }):
        response = client.get("/sram-axs/connect?reconnect=1")

    assert response.status_code == 200
    assert b"Renew SRAM AXS Session" in response.data
