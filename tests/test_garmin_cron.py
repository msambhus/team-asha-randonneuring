from datetime import date
from unittest.mock import patch

from cryptography.fernet import Fernet
from services.garmin_tokens import GarminTokenCipher


def test_scheduled_garmin_sync_requires_cron_secret(client, app):
    app.config["CRON_SECRET"] = "cron-secret"
    response = client.get("/api/cron/sync-garmin-performance")

    assert response.status_code == 401


def test_scheduled_garmin_sync_refreshes_today_and_advances_backfill(
        client, app):
    app.config["CRON_SECRET"] = "cron-secret"
    app.config["GARMIN_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    token_ciphertext = GarminTokenCipher(
        app.config["GARMIN_TOKEN_ENCRYPTION_KEY"]).encrypt(
            '{"token":"private"}')

    class Performance:
        def load_tokens(self, value):
            assert value == '{"token":"private"}'

    with patch(
            "models.get_garmin_connections_for_performance_sync",
            return_value=[{
                "rider_id": 42,
                "token_ciphertext": token_ciphertext,
            }]), \
         patch("services.garmin_connect.GarminPerformanceClient",
               Performance), \
         patch("routes.garmin._store_performance_snapshot") as store, \
         patch("routes.garmin.backfill_performance_history",
               return_value={"captured": 1}):
        response = client.get(
            "/api/cron/sync-garmin-performance",
            headers={"Authorization": "Bearer cron-secret"})

    assert response.status_code == 200
    assert response.get_json()["refreshed"] == 1
    assert response.get_json()["backfilled_days"] == 1
    store.assert_called_once_with(42, store.call_args.args[1], date.today())
