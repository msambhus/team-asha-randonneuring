import base64
import json
import time
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

from services.sram_axs import (
    SramAxsAuthenticationError,
    SramAxsClient,
    SramTokenCipher,
)


def _jwt(payload):
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload).encode()).decode().rstrip("=")
    return f"x.{encoded}.x"


def test_sram_token_cipher_round_trip_and_rejects_invalid_key():
    cipher = SramTokenCipher(Fernet.generate_key())
    encrypted = cipher.encrypt('{"access_token":"private"}')
    assert "private" not in encrypted
    assert cipher.decrypt(encrypted) == '{"access_token":"private"}'
    with pytest.raises(ValueError):
        SramTokenCipher("not-a-fernet-key")


def test_sram_login_uses_ticket_flow_and_keeps_password_out_of_tokens():
    session = MagicMock()
    ticket = MagicMock(ok=True, status_code=200)
    ticket.json.return_value = {"login_ticket": "ticket-1"}
    callback = MagicMock(
        headers={"Location": (
            "https://axs.sram.com/callback#access_token=access"
            f"&id_token={_jwt({'email': 'rider@example.com'})}"
            "&expires_in=3600&state=STATE"
        )})
    session.post.return_value = ticket
    session.get.return_value = callback
    client = SramAxsClient(session=session)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("services.sram_axs.secrets.token_urlsafe",
                      lambda _size: "STATE")
        tokens = client.login("rider@example.com", "never-store-me")

    assert tokens["access_token"] == "access"
    assert "never-store-me" not in client.dump_tokens()
    assert client.display_name() == "rider@example.com"
    assert session.post.call_args.kwargs["json"]["password"] == "never-store-me"


def test_expired_sram_session_requires_reconnect():
    with pytest.raises(SramAxsAuthenticationError):
        SramAxsClient.from_token_json(json.dumps({
            "access_token": "old",
            "expires_at": int(time.time()) - 1,
        }))


def test_sram_normalizes_gearing_summary():
    activity = SramAxsClient.normalize_activity({
        "id": 99,
        "name": "Long ride",
        "start_ts": 100,
        "end_ts": 200,
        "activitysummary_set": [{"data": {
            "distance": 200000,
            "rd_shift_count": 420,
            "average_power": 150,
        }}],
        "componentsummary_set": [{"id": 12}],
    })
    assert activity["sram_activity_id"] == "99"
    assert activity["rear_shift_count"] == 420
    assert activity["component_ids"] == [12]

    components = SramAxsClient.normalize_components([{
        "id": 12,
        "device_type": 34,
        "data": {
            "ant_component_id": 2,
            "rd_shift_count": 420,
            "fd_shift_count": 12,
            "rd_histogram": [5, 40, 10],
            "fd_histogram": [20, 35],
            "num_cogs": 3,
            "num_chainrings": 2,
        },
    }])
    summary = SramAxsClient.gear_summary(components)
    assert summary["most_used_rear_index"] == 2
    assert summary["most_used_front_index"] == 2
