"""Encryption boundary for Garmin DI OAuth tokens.

The refresh token grants persistent Garmin-account access. Only encrypted
ciphertext crosses into models/database code; plaintext exists transiently in
the server process while connecting or syncing.
"""
from cryptography.fernet import Fernet, InvalidToken


class GarminTokenCipher:
    def __init__(self, key):
        if not key:
            raise ValueError("GARMIN_TOKEN_ENCRYPTION_KEY is not configured")
        try:
            self._fernet = Fernet(
                key.encode("ascii") if isinstance(key, str) else key)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "GARMIN_TOKEN_ENCRYPTION_KEY must be a Fernet key") from exc

    def encrypt(self, token_json):
        if not isinstance(token_json, str) or not token_json:
            raise ValueError("Garmin token JSON is empty")
        return self._fernet.encrypt(token_json.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext):
        if not isinstance(ciphertext, str) or not ciphertext:
            raise ValueError("Encrypted Garmin token is empty")
        try:
            return self._fernet.decrypt(
                ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Encrypted Garmin token could not be decrypted") from exc
