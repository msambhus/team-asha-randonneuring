"""Shared broker state signing — framework-free and stdlib-only.

The shared Strava OAuth broker sends an authenticated ``state`` from Team Asha to
BrevetHub (and back through Strava). This module is the single implementation of
that state contract, imported by both apps so they can never disagree on the
format or the crypto.

A state is an HMAC-SHA256 signature over a canonical JSON payload carrying:
  - ``origin``       which app initiated the connect (e.g. ``"team-asha"``)
  - ``ta_rider_id``  the Team Asha rider id the tokens will belong to
  - ``return_url``   the absolute URL BrevetHub bounces back to after Strava
  - ``iat``          issued-at (epoch seconds) — freshness, enforced on verify
  - ``nonce``        random per-flow value — the single-use replay guard's key

The secret (``BROKER_HMAC_SECRET``) is passed in explicitly by each caller; this
module never reads Flask config or any application global, so it stays portable
and testable. ``verify_state`` is total: any tampering, wrong secret, malformed
input, or a stale/future ``iat`` returns ``None`` rather than raising, and the
signature compare is constant-time.

``tests/brevethub/test_shared_isolation.py`` fails the build if this module ever
imports a Team Asha module or reaches for a Flask application global.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time

# Small forward tolerance for clock skew between the two Vercel projects when
# checking that a state's issued-at is not implausibly in the future.
_CLOCK_SKEW_SECONDS = 60


def _b64url_encode(raw):
    """URL-safe base64 without padding (safe to carry in a query string)."""
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _b64url_decode(text):
    """Inverse of :func:`_b64url_encode`; raises on malformed input."""
    padding = '=' * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _canonical_payload(payload):
    """Deterministic JSON bytes so signing and verifying agree byte-for-byte."""
    return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')


def sign_state(*, secret, origin, ta_rider_id, return_url, issued_at=None,
               nonce=None):
    """Sign a broker state and return it as a compact ``payload.signature`` string.

    ``issued_at`` defaults to the current epoch second and ``nonce`` to a fresh
    random token; both are accepted as arguments so tests are deterministic.
    """
    if not secret:
        raise ValueError("BROKER_HMAC_SECRET is required to sign broker state")
    if issued_at is None:
        issued_at = int(time.time())
    if nonce is None:
        nonce = secrets.token_urlsafe(16)

    payload = {
        'origin': origin,
        'ta_rider_id': ta_rider_id,
        'return_url': return_url,
        'iat': int(issued_at),
        'nonce': nonce,
    }
    payload_bytes = _canonical_payload(payload)
    payload_b64 = _b64url_encode(payload_bytes)
    signature = hmac.new(
        secret.encode('utf-8'), payload_b64.encode('ascii'), hashlib.sha256
    ).digest()
    return f"{payload_b64}.{_b64url_encode(signature)}"


def verify_state(state, *, secret, max_age, now=None):
    """Verify a broker state and return its payload dict, or ``None`` if invalid.

    Rejects (returns ``None``, never raises) a state that is malformed, carries a
    bad signature, was signed with a different secret, is older than ``max_age``
    seconds, or is issued implausibly far in the future. The signature check is a
    constant-time compare so verification does not leak timing.

    Authenticity + freshness only — this does NOT prove the state is unused. The
    caller enforces single-use with a durable nonce claim (see the broker's
    ``claim_broker_state``); ``payload['nonce']`` is that claim's key.
    """
    if not secret or not state or not isinstance(state, str):
        return None
    if now is None:
        now = int(time.time())

    parts = state.split('.')
    if len(parts) != 2:
        return None
    payload_b64, signature_b64 = parts

    expected = hmac.new(
        secret.encode('utf-8'), payload_b64.encode('ascii'), hashlib.sha256
    ).digest()
    try:
        provided = _b64url_decode(signature_b64)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(expected, provided):
        return None

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    iat = payload.get('iat')
    if not isinstance(iat, int):
        return None
    if iat > now + _CLOCK_SKEW_SECONDS:  # issued in the future → reject
        return None
    if now - iat > max_age:  # stale → reject
        return None

    for key in ('origin', 'return_url', 'nonce'):
        if key not in payload:
            return None
    return payload
