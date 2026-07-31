"""Read-only SRAM AXS Web client.

SRAM does not publish third-party application registration for AXS Web. The
web application uses an Auth0 login-ticket flow with a callback fixed to
axs.sram.com. This client follows that same first-party flow, never persists a
password, and stores only the resulting encrypted access/id-token envelope.
"""
from __future__ import annotations

import base64
import json
import re
import secrets
import time
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from cryptography.fernet import Fernet, InvalidToken


AUTH0_DOMAIN = "https://sramid-auth.sram.com"
AUTH0_CLIENT_ID = "zIvfleoh46jy4behzZdkFoUIiW70KX23"
AUTH0_AUDIENCE = "https://api.quarqnet.com"
AUTH0_REALM = "sramid-db"
AXS_ORIGIN = "https://axs.sram.com"
AXS_CALLBACK = f"{AXS_ORIGIN}/callback"
AXS_API = "https://api.quarqnet.com/api/v2"
AXS_SCOPE = (
    "openid email profile read:current_user "
    "update:current_user_identities"
)
_AXS_RESOURCE_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")


class SramAxsError(Exception):
    """Base SRAM AXS integration error."""


class SramAxsAuthenticationError(SramAxsError):
    """SRAM rejected or can no longer use the account session."""


class SramAxsRateLimitError(SramAxsError):
    """SRAM temporarily rate limited the request."""


class SramAxsConnectionError(SramAxsError):
    """SRAM could not be reached or returned an unsupported response."""


class SramTokenCipher:
    def __init__(self, key):
        if not key:
            raise ValueError("SRAM_AXS_TOKEN_ENCRYPTION_KEY is not configured")
        try:
            self._fernet = Fernet(
                key.encode("ascii") if isinstance(key, str) else key)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "SRAM_AXS_TOKEN_ENCRYPTION_KEY must be a Fernet key") from exc

    def encrypt(self, value):
        if not isinstance(value, str) or not value:
            raise ValueError("SRAM token payload is empty")
        return self._fernet.encrypt(value.encode()).decode("ascii")

    def decrypt(self, value):
        if not isinstance(value, str) or not value:
            raise ValueError("Encrypted SRAM token payload is empty")
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode()
        except InvalidToken as exc:
            raise ValueError("Encrypted SRAM tokens could not be decrypted") from exc


def _jwt_claims(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}


class SramAxsClient:
    """Small, bounded client for the private AXS activity endpoints."""

    def __init__(self, token_data=None, session=None, timeout=20):
        self.token_data = dict(token_data or {})
        self.session = session or requests.Session()
        self.timeout = timeout
        self.tokens_changed = False
        self._restore_session_cookies()

    def _restore_session_cookies(self):
        """Restore only the Auth0 cookie fields kept in encrypted storage."""
        for row in self.token_data.get("session_cookies") or []:
            if not isinstance(row, dict) or not row.get("name"):
                continue
            self.session.cookies.set(
                row["name"], row.get("value") or "",
                domain=row.get("domain") or None,
                path=row.get("path") or "/",
                secure=bool(row.get("secure")),
                expires=row.get("expires"),
            )

    def _session_cookies(self):
        """Return a JSON-safe cookie jar for the encrypted token envelope."""
        return [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": bool(cookie.secure),
                "expires": cookie.expires,
            }
            for cookie in self.session.cookies
            if (
                cookie.name
                and cookie.value
                and (cookie.domain or "").lower().endswith(
                    "sramid-auth.sram.com")
            )
        ]

    def _authorize(self, extra_params):
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        authorize_url = f"{AUTH0_DOMAIN}/authorize"
        params = {
            "client_id": AUTH0_CLIENT_ID,
            "audience": AUTH0_AUDIENCE,
            "response_type": "token id_token",
            "redirect_uri": AXS_CALLBACK,
            "scope": AXS_SCOPE,
            "state": state,
            "nonce": nonce,
            **extra_params,
        }
        location = None
        for _ in range(5):
            try:
                auth_response = self.session.get(
                    authorize_url, params=params, allow_redirects=False,
                    timeout=self.timeout)
            except requests.RequestException as exc:
                raise SramAxsConnectionError(
                    "Could not complete SRAM sign-in") from exc
            location = auth_response.headers.get("Location")
            if not location:
                break
            location = urljoin(authorize_url, location)
            if location.startswith(AXS_CALLBACK):
                break
            authorize_url, params = location, None
        fragment = parse_qs(urlparse(location or "").fragment)
        if fragment.get("state", [None])[0] != state:
            raise SramAxsAuthenticationError(
                "SRAM sign-in state could not be verified")
        access_token = fragment.get("access_token", [None])[0]
        id_token = fragment.get("id_token", [None])[0]
        if not access_token:
            error = fragment.get("error_description", [""])[0]
            raise SramAxsAuthenticationError(
                error or "SRAM did not return an access token")
        expires_in = int(fragment.get("expires_in", [86400])[0])
        self.token_data = {
            "access_token": access_token,
            "id_token": id_token,
            "expires_at": int(time.time()) + expires_in,
            "scope": fragment.get("scope", [AXS_SCOPE])[0],
            "session_cookies": self._session_cookies(),
        }
        self.tokens_changed = True
        return self.token_data

    def login(self, email, password):
        """Exchange request-local credentials for the AXS Web token envelope."""
        if not email or not password:
            raise SramAxsAuthenticationError("Email and password are required")
        try:
            response = self.session.post(
                f"{AUTH0_DOMAIN}/co/authenticate",
                headers={"Origin": AXS_ORIGIN, "Content-Type": "application/json"},
                json={
                    "client_id": AUTH0_CLIENT_ID,
                    "username": email,
                    "password": password,
                    "realm": AUTH0_REALM,
                    "credential_type":
                        "http://auth0.com/oauth/grant-type/password-realm",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SramAxsConnectionError("Could not reach SRAM sign-in") from exc
        if response.status_code == 429:
            raise SramAxsRateLimitError("SRAM is rate limiting sign-ins")
        if response.status_code in (401, 403):
            raise SramAxsAuthenticationError("SRAM rejected those credentials")
        if not response.ok:
            raise SramAxsConnectionError("SRAM sign-in did not complete")
        try:
            login_payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise SramAxsConnectionError(
                "SRAM sign-in returned an invalid response") from exc
        login_ticket = login_payload.get("login_ticket")
        if not login_ticket:
            raise SramAxsAuthenticationError(
                "SRAM requires an unsupported verification step")

        return self._authorize({"login_ticket": login_ticket})

    def dump_tokens(self):
        return json.dumps(self.token_data, separators=(",", ":"))

    @classmethod
    def from_token_json(cls, token_json, **kwargs):
        try:
            token_data = json.loads(token_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SramAxsAuthenticationError("Stored SRAM session is invalid") from exc
        if (
            int(token_data.get("expires_at") or 0) <= int(time.time()) + 30
            and not token_data.get("session_cookies")
        ):
            raise SramAxsAuthenticationError(
                "SRAM session expired; reconnect SRAM AXS")
        return cls(token_data=token_data, **kwargs)

    def renew_if_needed(self, leeway=300, force=False):
        """Use the encrypted Auth0 browser session to obtain a fresh token."""
        expires_at = int(self.token_data.get("expires_at") or 0)
        if not force and expires_at > int(time.time()) + max(30, leeway):
            return False
        if not self.token_data.get("session_cookies"):
            raise SramAxsAuthenticationError(
                "SRAM session expired; reconnect SRAM AXS")
        self._authorize({"prompt": "none"})
        return True

    def display_name(self):
        claims = _jwt_claims(self.token_data.get("id_token") or "")
        return claims.get("name") or claims.get("email")

    def _get(self, path, **params):
        access_token = self.token_data.get("access_token")
        if not access_token:
            raise SramAxsAuthenticationError("SRAM AXS is not connected")
        try:
            response = self.session.get(
                f"{AXS_API}/{path.lstrip('/')}",
                params=params or None,
                headers={"Authorization": f"Bearer {access_token}",
                         "Accept": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SramAxsConnectionError("Could not reach SRAM AXS") from exc
        if response.status_code in (401, 403):
            raise SramAxsAuthenticationError(
                "SRAM session expired; reconnect SRAM AXS")
        if response.status_code == 429:
            raise SramAxsRateLimitError("SRAM AXS is rate limiting sync")
        if not response.ok:
            raise SramAxsConnectionError(
                f"SRAM AXS returned HTTP {response.status_code}")
        try:
            return response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise SramAxsConnectionError(
                "SRAM AXS returned an invalid response") from exc

    def activities(self, page=1, page_size=20):
        if not 1 <= page <= 100 or not 1 <= page_size <= 100:
            raise ValueError("Invalid SRAM activity page bounds")
        payload = self._get("activities/", page=page, page_size=page_size)
        return payload if isinstance(payload, list) else []

    def activity(self, activity_id):
        if not _AXS_RESOURCE_ID.fullmatch(str(activity_id or "")):
            raise ValueError("Invalid SRAM activity id")
        payload = self._get(f"activities/{activity_id}/")
        return payload if isinstance(payload, dict) else {}

    def component_summary(self, summary_id):
        if not _AXS_RESOURCE_ID.fullmatch(str(summary_id or "")):
            raise ValueError("Invalid SRAM component summary id")
        payload = self._get(f"componentsummaries/{summary_id}/")
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def normalize_activity(activity):
        summaries = activity.get("activitysummary_set") or []
        summary = summaries[0].get("data", {}) if summaries else {}
        component_ids = [
            row.get("id") for row in activity.get("componentsummary_set", [])
            if isinstance(row, dict) and row.get("id") is not None
        ][:30]
        return {
            "sram_activity_id": str(activity.get("id") or ""),
            "activity_name": activity.get("name"),
            "activity_type": activity.get("type"),
            "started_at_epoch": activity.get("start_ts"),
            "ended_at_epoch": activity.get("end_ts"),
            "distance_m": summary.get("distance"),
            "duration_s": summary.get("duration") or (
                (activity.get("end_ts") or 0) - (activity.get("start_ts") or 0)),
            "elevation_gain_m": summary.get("ascent"),
            "average_power": summary.get("average_power"),
            "max_power": summary.get("max_power"),
            "normalized_power": summary.get("normalized_power"),
            "average_hr": summary.get("average_heartrate"),
            "max_hr": summary.get("max_heartrate"),
            "average_cadence": summary.get("average_cadence"),
            "max_cadence": summary.get("max_cadence"),
            "rear_shift_count": summary.get("rd_shift_count"),
            "front_shift_count": summary.get("fd_shift_count"),
            "component_ids": component_ids,
        }

    @staticmethod
    def normalize_components(components):
        normalized = []
        for component in components[:30]:
            data = component.get("data") or {}
            normalized.append({
                "summary_id": component.get("id"),
                "device_type": component.get("device_type"),
                "component": data.get("component"),
                "ant_component_id": data.get("ant_component_id"),
                "manufacturer": component.get("manufacturer"),
                "model": component.get("model"),
                "battery_status": component.get("battery_status"),
                "voltage": component.get("voltage"),
                "front_shift_count": data.get("fd_shift_count"),
                "rear_shift_count": data.get("rd_shift_count"),
                "num_chainrings": data.get("num_chainrings"),
                "num_cogs": data.get("num_cogs"),
                "front_histogram": (data.get("fd_histogram") or [])[:32],
                "rear_histogram": (data.get("rd_histogram") or [])[:32],
                "front_gears": (data.get("fd_gear") or [])[:20000],
                "rear_gears": (data.get("rd_gear") or [])[:20000],
                "timestamps": (data.get("time") or [])[:20000],
            })
        return normalized

    @staticmethod
    def gear_summary(components):
        gear = next((
            row for row in components
            if row.get("ant_component_id") == 2
            or row.get("device_type") == 34
        ), None)
        if not gear:
            return {}
        rear_hist = gear.get("rear_histogram") or []
        front_hist = gear.get("front_histogram") or []
        return {
            "rear_shift_count": gear.get("rear_shift_count"),
            "front_shift_count": gear.get("front_shift_count"),
            "num_cogs": gear.get("num_cogs"),
            "num_chainrings": gear.get("num_chainrings"),
            "rear_histogram": rear_hist,
            "front_histogram": front_hist,
            "most_used_rear_index": (
                rear_hist.index(max(rear_hist)) + 1 if rear_hist else None),
            "most_used_front_index": (
                front_hist.index(max(front_hist)) + 1 if front_hist else None),
        }
