"""Garmin connection security and rider-ownership tests."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
from cryptography.fernet import Fernet

from services.garmin_tokens import GarminTokenCipher


def _login(client, rider_id=42):
    with client.session_transaction() as sess:
        sess["user_id"] = 7
        sess["rider_id"] = rider_id


def test_connect_page_explains_and_shows_submission_progress(client, app):
    _login(client)
    app.config["GARMIN_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

    with patch("routes.garmin.models.get_garmin_connection",
               return_value=None):
        response = client.get("/garmin/connect")

    assert response.status_code == 200
    assert b'id="garmin-connect-progress"' in response.data
    assert b'aria-live="polite"' in response.data
    assert b"Connecting securely" in response.data
    assert b"up to a minute" in response.data
    assert b"button.disabled = true" in response.data
    assert b"input.readOnly = true" in response.data


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

    def export_mfa_state(self):
        return {
            "version": 1,
            "flow": "portal",
            "method": "email",
            "login_params": {"clientId": "GarminConnect"},
            "post_headers": {},
            "service_url": "https://connect.garmin.com/app",
            "cookies": [],
            "session_kind": "requests",
        }

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


def test_mfa_detection_stores_only_encrypted_challenge(client, app):
    _login(client)
    app.config["GARMIN_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

    challenge = {}

    def capture(rider_id, ciphertext):
        challenge.update(rider_id=rider_id, ciphertext=ciphertext)

    with patch("routes.garmin.models.get_garmin_connection", return_value=None), \
         patch("routes.garmin.models.upsert_garmin_connection") as save, \
         patch("routes.garmin.models.save_garmin_mfa_challenge",
               side_effect=capture), \
         patch("routes.garmin.Client",
               return_value=FakeGarminAuth(mfa=True)):
        response = client.post("/garmin/connect", data={
            "email": "rider@example.com",
            "password": "transient-password",
        }, follow_redirects=True)

    assert response.status_code == 200
    assert b"verification code" in response.data
    save.assert_not_called()
    assert challenge["rider_id"] == 42
    decoded = GarminTokenCipher(
        app.config["GARMIN_TOKEN_ENCRYPTION_KEY"]).decrypt(
            challenge["ciphertext"])
    assert json.loads(decoded)["flow"] == "portal"
    assert "transient-password" not in decoded


class FakeMfaResume(FakeGarminAuth):
    def __init__(self):
        super().__init__()
        self.imported = None

    def import_mfa_state(self, state):
        self.imported = state

    def resume_login(self, client_state, code):
        assert client_state is None
        assert code == "123456"


class FakeRejectedMfa(FakeMfaResume):
    def resume_login(self, client_state, code):
        from vendor.python_garminconnect import GarminConnectAuthenticationError
        raise GarminConnectAuthenticationError("incorrect code")


def test_mfa_completion_is_rider_scoped_and_saves_tokens(client, app):
    _login(client, rider_id=42)
    app.config["GARMIN_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    state = FakeGarminAuth(mfa=True).export_mfa_state()
    ciphertext = GarminTokenCipher(
        app.config["GARMIN_TOKEN_ENCRYPTION_KEY"]).encrypt(json.dumps(state))
    auth = FakeMfaResume()

    with patch("routes.garmin.models.take_garmin_mfa_attempt",
               return_value={"state_ciphertext": ciphertext, "attempts": 1}), \
         patch("routes.garmin.models.upsert_garmin_connection") as save, \
         patch("routes.garmin.models.delete_garmin_mfa_challenge") as remove, \
         patch("routes.garmin.Client", return_value=auth):
        response = client.post("/garmin/mfa", data={
            "code": "123456", "rider_id": "999",
        })

    assert response.status_code == 302
    assert auth.imported["flow"] == "portal"
    assert save.call_args.args[0] == 42
    remove.assert_called_once_with(42)


def test_incorrect_mfa_code_can_retry_until_attempt_limit(client, app):
    _login(client, rider_id=42)
    app.config["GARMIN_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    state = FakeGarminAuth(mfa=True).export_mfa_state()
    ciphertext = GarminTokenCipher(
        app.config["GARMIN_TOKEN_ENCRYPTION_KEY"]).encrypt(json.dumps(state))

    with patch("routes.garmin.models.take_garmin_mfa_attempt",
               return_value={"state_ciphertext": ciphertext, "attempts": 2}), \
         patch("routes.garmin.models.upsert_garmin_connection") as save, \
         patch("routes.garmin.models.delete_garmin_mfa_challenge") as remove, \
         patch("routes.garmin.Client", return_value=FakeRejectedMfa()):
        response = client.post("/garmin/mfa", data={"code": "000000"})

    assert response.status_code == 200
    assert b"rejected that code" in response.data
    save.assert_not_called()
    remove.assert_not_called()


def test_vendor_mfa_state_round_trip_excludes_credentials():
    from vendor.python_garminconnect import Client

    source = Client()
    source._mfa_flow = "widget"
    source._mfa_method = "email"
    source._mfa_login_params = {"clientId": "GarminConnect"}
    source._mfa_post_headers = {"Referer": "https://sso.garmin.com"}
    source._mfa_service_url = "https://connect.garmin.com/app"
    source._mfa_session = requests.Session()
    source._mfa_session.cookies.set(
        "SESSION", "challenge-secret", domain=".garmin.com", path="/")
    source._mfa_session.cookies.set(
        "UNRELATED", "excluded", domain=".example.com", path="/")
    source._widget_last_resp = SimpleNamespace(
        text='<input name="_csrf" value="csrf-secret">', url="https://sso.garmin.com")

    state = source.export_mfa_state()
    serialized = json.dumps(state)
    assert "password" not in serialized.casefold()
    assert "excluded" not in serialized
    restored = Client()
    restored.import_mfa_state(state)

    assert restored._mfa_flow == "widget"
    assert restored._widget_last_resp.text == source._widget_last_resp.text
    assert restored._mfa_session.cookies.get(
        "SESSION", domain=".garmin.com", path="/") == "challenge-secret"


def test_disconnect_is_scoped_to_session_rider(client, app):
    _login(client, rider_id=42)
    app.config["GARMIN_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    with patch("routes.garmin.models.delete_garmin_connection") as remove:
        response = client.post("/garmin/disconnect",
                               data={"rider_id": "999",
                                     "confirm_delete": "DELETE"})
    assert response.status_code == 302
    remove.assert_called_once_with(42)


def test_disconnect_requires_explicit_confirmation(client):
    _login(client, rider_id=42)
    with patch("routes.garmin.models.delete_garmin_connection") as remove:
        response = client.post("/garmin/disconnect")
    assert response.status_code == 302
    remove.assert_not_called()


class FakePerformanceSync:
    loaded_tokens = None

    def load_tokens(self, token_json):
        self.loaded_tokens = token_json

    def performance_snapshot(self, on_date):
        return {
            "date": on_date.isoformat(),
            "resting_heart_rate": 48,
            "hrv_status": "BALANCED",
            "sleep_score": 83,
            "body_battery": 72,
            "training_readiness": 77,
            "vo2_max_cycling": 53,
            "training_status": "PRODUCTIVE",
            "raw": {"private": "recovery-payload"},
        }

    def dump_tokens(self):
        return json.dumps({"di_refresh_token": "refreshed-secret"})

    def activities(self, limit):
        assert limit == 20
        return [{
            "activityId": 123,
            "activityName": "Private ride",
            "activityType": {"typeKey": "cycling"},
            "distance": 100000,
            "averageHR": 130,
        }]

    def normalize_activity(self, activity):
        return {
            "garmin_activity_id": activity["activityId"],
            "activity_name": activity["activityName"],
            "activity_type": "cycling",
            "distance_m": activity["distance"],
            "average_hr": activity["averageHR"],
        }


def test_performance_sync_decrypts_tokens_and_encrypts_private_payload(
        client, app):
    _login(client, rider_id=42)
    app.config["GARMIN_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    cipher = GarminTokenCipher(app.config["GARMIN_TOKEN_ENCRYPTION_KEY"])
    stored = {}
    activities = {}

    def capture(rider_id, snapshot, raw_ciphertext, token_ciphertext):
        stored.update(rider_id=rider_id, snapshot=snapshot,
                      raw_ciphertext=raw_ciphertext,
                      token_ciphertext=token_ciphertext)

    def capture_activities(rider_id, rows):
        activities.update(rider_id=rider_id, rows=rows)

    with patch("routes.garmin.models.get_garmin_connection", return_value={
            "token_ciphertext": cipher.encrypt('{"di_token":"old-secret"}')}), \
         patch("routes.garmin.models.upsert_garmin_performance_snapshot",
               side_effect=capture), \
         patch("routes.garmin.models.upsert_garmin_activities",
               side_effect=capture_activities), \
         patch("routes.garmin.GarminPerformanceClient",
               FakePerformanceSync):
        response = client.post("/garmin/sync", data={"rider_id": "999"})

    assert response.status_code == 302
    assert stored["rider_id"] == 42
    assert stored["snapshot"]["training_readiness"] == 77
    assert "recovery-payload" not in stored["raw_ciphertext"]
    assert json.loads(cipher.decrypt(
        stored["raw_ciphertext"]))["private"] == "recovery-payload"
    assert "refreshed-secret" not in stored["token_ciphertext"]
    assert activities["rider_id"] == 42
    normalized, encrypted_raw = activities["rows"][0]
    assert normalized["garmin_activity_id"] == 123
    assert "Private ride" not in encrypted_raw
    assert json.loads(cipher.decrypt(
        encrypted_raw))["activityName"] == "Private ride"
