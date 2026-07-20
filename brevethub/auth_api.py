"""BrevetHub-native bearer-token auth for a future BrevetHub mobile client.

The web signs in with Google OAuth + a Flask session cookie. A native app cannot
carry that cookie, so this module issues a stateless, signed ``{rider_id}`` token
the app sends as ``Authorization: Bearer <token>`` on subsequent API calls.

BH-native and self-contained: the token is signed with BrevetHub's own
``SECRET_KEY`` (a distinct salt keeps it from colliding with the Flask session or
any other signed payload) — no new secret, no token table, no dependency on Team
Asha's auth. It imports only flask / itsdangerous / stdlib / ``brevethub.*``, so
the isolation guard stays green.

No BrevetHub client consumes this yet — it is the server half of a future mobile
app. The mint endpoint is deliberately web-login-gated only (a completed-profile
session mints a token); there is no Google/Apple native token exchange.
"""
from flask import (Blueprint, current_app, g, jsonify, request, session)
from itsdangerous import URLSafeTimedSerializer

from brevethub import models
from brevethub.decorators import current_rider

# Distinct salt so a BrevetHub bearer token can never be confused with a Flask
# session cookie (both signed with SECRET_KEY). 30-day lifetime, like the web
# session, so the app re-mints roughly monthly.
_TOKEN_SALT = 'brevethub-mobile-auth'
TOKEN_MAX_AGE = 30 * 24 * 3600   # 30 days, in seconds


def _serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=_TOKEN_SALT)


def mint_token(rider_id):
    """Sign a ``{rider_id}`` bearer token for the native app."""
    return _serializer().dumps({'rider_id': rider_id})


def load_token(token):
    """Return the token payload dict, or None if missing/expired/tampered. Never
    raises — any decode failure (bad signature, expired, garbage) reads as
    unauthenticated."""
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=TOKEN_MAX_AGE)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 — every failure mode is simply "not authed"
        return None


def resolve_rider_id():
    """Resolve the caller to a rider_id from a web session OR a bearer token.

    Session wins (a browser caller), then a ``Authorization: Bearer <token>``
    header (the native app). Returns None when neither identifies a rider."""
    rider_id = session.get('rider_id')
    if rider_id:
        return rider_id
    authz = request.headers.get('Authorization', '')
    if authz.startswith('Bearer '):
        data = load_token(authz[len('Bearer '):].strip())
        if data:
            return data.get('rider_id')
    return None


def bearer_or_session_rider():
    """Resolve the caller to their rp_rider row from a session OR bearer token, or
    None. The row carries ``profile_completed`` so the caller applies the same
    completeness bar the member surface already enforces."""
    rider_id = resolve_rider_id()
    if not rider_id:
        return None
    return models.get_rider_by_id(rider_id)


api_auth_bp = Blueprint('api_auth', __name__)


@api_auth_bp.route('/api/auth/token', methods=['POST'])
def mint():
    """Login-gated mint: a completed-profile WEB session exchanges for a bearer
    token the native app then sends as ``Authorization: Bearer <token>``.

    Session-only (uses ``current_rider`` — a bearer token cannot mint another
    token). 401 when no session rider; 403 when the profile is incomplete."""
    rider = current_rider()
    if not rider:
        return jsonify({'error': 'Authentication required'}), 401
    if not rider['profile_completed']:
        return jsonify({'error': 'Complete your profile first'}), 403
    g.rider_id = rider['id']
    return jsonify({
        'token': mint_token(rider['id']),
        'token_type': 'Bearer',
        'expires_in': TOKEN_MAX_AGE,
        # Also echo the identity + profile state, matching the native session shape
        # ({token, rider_id, profile_complete}) the demo mint below returns.
        'rider_id': rider['id'],
        'profile_complete': bool(rider['profile_completed']),
    })


@api_auth_bp.route('/api/auth/demo', methods=['POST'])
def demo_signin():
    """Cookie-free bearer mint for a demo/reviewer account.

    A native client (or Apple App Review) has no web session cookie, so this issues
    a normal Bearer token for a fixed rider (``DEMO_RIDER_ID``) WITHOUT a session —
    the one sign-in path a cookie-less client can use. It is invisible (404) unless
    ``DEMO_MODE_ENABLED`` is set, so it is not an auth path in normal production;
    enable it only while an app review is in flight.

    Returns {token, rider_id, profile_complete} — the shared native session shape.
    Full email/password + email-OTP native sign-in is a documented follow-on (it
    needs a credential/OTP store migration and an email sender BrevetHub does not
    have yet); until then, demo is the cookie-free path and the web-gated
    /api/auth/token mint covers a logged-in browser."""
    if not current_app.config.get('DEMO_MODE_ENABLED'):
        # 404 (not 403) so the endpoint does not advertise its existence.
        return jsonify({'error': 'Not found'}), 404

    raw_rider_id = current_app.config.get('DEMO_RIDER_ID')
    try:
        rider_id = int(raw_rider_id)
    except (TypeError, ValueError):
        current_app.logger.error(
            'demo sign-in: DEMO_RIDER_ID is unset/invalid (%r)', raw_rider_id)
        return jsonify({'error': 'Demo login is not configured'}), 503

    rider = models.get_rider_by_id(rider_id)
    if not rider:
        current_app.logger.error('demo sign-in: rider %s not found', rider_id)
        return jsonify({'error': 'Demo login is not configured'}), 503

    g.rider_id = rider_id
    return jsonify({
        'token': mint_token(rider_id),
        'rider_id': rider_id,
        'profile_complete': bool(rider['profile_completed']),
    }), 200
