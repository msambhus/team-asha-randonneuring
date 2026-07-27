"""Garmin connection security and rider-ownership tests."""
import json
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from services.garmin_tokens import GarminTokenCipher


def _login(client, rider_id=42):
    with client.session_transaction() as sess:
        sess["user_id"] = 7
        sess["rider_id"] = rider_id


def test_token_cipher_round_trip_and_wrong_key_fails():
    token_json = json.dumps({
        "di_token": "access-secret",
        "di_refresh_token": "persistent-refresh-secret",
        "di_client_id": "client",
    })
    ciphertext = GarminTokenCipher(Fernet.generate_key()).encrypt(token_json)

    assert "access-secret" not in ciphertext
    assert "persistent-refresh-secret" not in ciphertext
    key = Fernet.generate_key()
    second = GarminTokenCipher(key)
    encrypted = second.encrypt(token_json)
    assert second.decrypt(encrypted) == token_json
    with pytest.raises(ValueError):
        GarminTokenCipher(Fernet.generate_key()).decrypt(encrypted)


def test_missing_or_invalid_encryption_key_fails_closed():
    with pytest.raises(ValueError):
        GarminTokenCipher(None)
    with pytest.raises(ValueError):
        GarminTokenCipher("not-a-fernet-key")


class FakeGarminAuth:
    def __init__(self, *, mfa=False):
        self.mfa = mfa

    def login(self, email, password, return_on_mfa=False):
        assert email == "rider@example.com"
        assert password == "transient-password"
        assert return_on_mfa is True
        return ("MFA_REQUIRED" if self.mfa else None), None

    def connectapi(self, path, **kwargs):
        assert path == "/userprofile-service/socialProfile"
        return {"displayName": "GarminRider"}

    def dumps(self):
        return json.dumps({
            "di_token": "access-secret",
            "di_refresh_token": "refresh-secret",
            "di_client_id": "client",
        })


def test_connect_persists_only_encrypted_tokens_for_session_rider(client, app):
    _login(client, rider_id=42)
    app.config["GARMIN_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    stored = {}

    def capture(rider_id, ciphertext, display_name):
        stored.update(rider_id=rider_id, ciphertext=ciphertext,
                      display_name=display_name)

    with patch("routes.garmin.models.get_garmin_connection", return_value=None), \
         patch("routes.garmin.models.upsert_garmin_connection",
               side_effect=capture), \
         patch("routes.garmin.Client", return_value=FakeGarminAuth()):
        response = client.post("/garmin/connect", data={
            "email": "rider@example.com",
            "password": "transient-password",
            # Must be ignored: ownership comes only from the session.
            "rider_id": "999",
        })

    assert response.status_code == 302
    assert stored["rider_id"] == 42
    assert stored["display_name"] == "GarminRider"
    assert "access-secret" not in stored["ciphertext"]
    assert "refresh-secret" not in stored["ciphertext"]
    decoded = GarminTokenCipher(
        app.config["GARMIN_TOKEN_ENCRYPTION_KEY"]).decrypt(stored["ciphertext"])
    assert json.loads(decoded)["di_refresh_token"] == "refresh-secret"


def test_mfa_detection_stores_nothing(client, app):
    _login(client)
    app.config["GARMIN_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

    with patch("routes.garmin.models.get_garmin_connection", return_value=None), \
         patch("routes.garmin.models.upsert_garmin_connection") as save, \
         patch("routes.garmin.Client",
               return_value=FakeGarminAuth(mfa=True)):
        response = client.post("/garmin/connect", data={
            "email": "rider@example.com",
            "password": "transient-password",
        }, follow_redirects=True)

    assert response.status_code == 200
    assert b"requires MFA" in response.data
    save.assert_not_called()


def test_disconnect_is_scoped_to_session_rider(client, app):
    _login(client, rider_id=42)
    app.config["GARMIN_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    with patch("routes.garmin.models.delete_garmin_connection") as remove:
        response = client.post("/garmin/disconnect",
                               data={"rider_id": "999"})
    assert response.status_code == 302
    remove.assert_called_once_with(42)
