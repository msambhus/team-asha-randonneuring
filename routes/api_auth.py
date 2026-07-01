"""JSON auth endpoints for the native mobile app.

The web uses Google OAuth + Flask session cookies (routes/auth.py). Native apps
can't use that, so this blueprint exchanges a Google **ID token** (obtained by
the app's native Google Sign-In) for a stateless bearer token the app sends as
`Authorization: Bearer <token>` on subsequent API calls.

The Google account → user/rider mapping deliberately reuses the SAME model
helpers as the web OAuth callback so the two login paths stay in lockstep.
"""
from flask import Blueprint, request, jsonify, current_app

import models
from auth import mint_mobile_token

api_auth_bp = Blueprint('api_auth', __name__)


def _verify_google_id_token(id_token, audience):
    """Verify a Google ID token and return its claims. Raises on any failure.

    Isolated so tests mock this one function (no need for google-auth to be
    importable in the test env) and so the heavy import stays lazy.
    """
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests
    return google_id_token.verify_oauth2_token(
        id_token, google_requests.Request(), audience
    )


# Sign in with Apple identity tokens are RS256 JWTs signed by Apple. We verify
# the signature against Apple's published JWKS and check issuer + audience.
_APPLE_JWKS_URL = 'https://appleid.apple.com/auth/keys'
_APPLE_ISSUER = 'https://appleid.apple.com'
# The token's `aud` is our app's bundle identifier (native Sign in with Apple).
_APPLE_AUDIENCE_DEFAULT = 'org.teamasha.randonneuring'

# One JWKS client reused across requests so we don't refetch Apple's keys on
# every sign-in (PyJWKClient caches keys internally). Created lazily.
_apple_jwks_client = None


def _get_apple_jwks_client():
    global _apple_jwks_client
    if _apple_jwks_client is None:
        import jwt  # PyJWT; RS256 verification uses the cryptography lib
        _apple_jwks_client = jwt.PyJWKClient(_APPLE_JWKS_URL)
    return _apple_jwks_client


def _verify_apple_id_token(id_token, audience):
    """Verify an Apple identity token and return its claims. Raises on failure.

    Isolated (like _verify_google_id_token) so tests mock this one function and
    the PyJWT / JWKS-fetch import stays lazy and out of the test env.
    """
    import jwt  # PyJWT; RS256 verification uses the (already-present) cryptography lib
    signing_key = _get_apple_jwks_client().get_signing_key_from_jwt(id_token)
    return jwt.decode(
        id_token, signing_key.key, algorithms=['RS256'],
        audience=audience, issuer=_APPLE_ISSUER,
    )


@api_auth_bp.route('/google', methods=['POST'])
def google_signin():
    """Exchange a Google ID token for a native app bearer token.

    Body: {"id_token": "<google id token>"}
    Returns: {token, rider_id, profile_complete}
    """
    client_id = current_app.config.get('GOOGLE_IOS_CLIENT_ID')
    if not client_id:
        # Misconfiguration, not the caller's fault — make it obvious.
        return jsonify({'error': 'Mobile Google sign-in is not configured'}), 503

    body = request.get_json(silent=True) or {}
    id_token = (body.get('id_token') or '').strip()
    if not id_token:
        return jsonify({'error': 'id_token is required'}), 400

    # Verify the Google ID token (signature, expiry, issuer, and audience ==
    # our iOS client id).
    try:
        claims = _verify_google_id_token(id_token, client_id)
    except Exception as exc:  # noqa: BLE001 — any verification failure → 401
        current_app.logger.warning('mobile google sign-in: token verify failed: %s', exc)
        return jsonify({'error': 'Invalid Google token'}), 401

    google_id = claims.get('sub')
    email = claims.get('email')
    if not google_id or not email:
        return jsonify({'error': 'Google token missing sub/email'}), 401

    # Reuse the web OAuth callback's mapping: find-or-create the user by google_id.
    try:
        user = models.get_user_by_google_id(google_id)
        if not user:
            user = models.create_user(email, google_id)
            if not user:
                return jsonify({'error': 'Could not create account'}), 500
        else:
            models.update_user_login_time(user['id'])
            user = models.get_user_by_id(user['id'])
    except Exception:
        current_app.logger.exception('mobile google sign-in: user lookup failed')
        return jsonify({'error': 'Account lookup failed'}), 500

    rider_id = user.get('rider_id')
    profile_complete = bool(user.get('profile_completed') and rider_id)

    # Token is tied to user_id always; rider_id may be None until the rider
    # completes profile setup (the app can then prompt for it). Live endpoints
    # still require a rider_id (→ 403), matching the web's profile gate.
    token = mint_mobile_token(user['id'], rider_id)
    return jsonify({
        'token': token,
        'rider_id': rider_id,
        'profile_complete': profile_complete,
    }), 200


@api_auth_bp.route('/apple', methods=['POST'])
def apple_signin():
    """Exchange a Sign in with Apple identity token for a native app bearer token.

    Body: {"identity_token": "<apple id token>", "email": "<optional>"}
    Apple only includes the email in the token on the FIRST authorization; the
    app may also pass it in the body on that first sign-in. On later logins we
    already know the user by their Apple `sub`, so email is not needed.

    Returns: {token, rider_id, profile_complete} — same shape as /google.
    """
    audience = current_app.config.get('APPLE_BUNDLE_ID') or _APPLE_AUDIENCE_DEFAULT

    body = request.get_json(silent=True) or {}
    identity_token = (body.get('identity_token') or '').strip()
    if not identity_token:
        return jsonify({'error': 'identity_token is required'}), 400

    # Verify signature (Apple JWKS), issuer, expiry, and audience == our bundle id.
    try:
        claims = _verify_apple_id_token(identity_token, audience)
    except Exception as exc:  # noqa: BLE001 — any verification failure → 401
        current_app.logger.warning('mobile apple sign-in: token verify failed: %s', exc)
        return jsonify({'error': 'Invalid Apple token'}), 401

    apple_sub = claims.get('sub')
    if not apple_sub:
        return jsonify({'error': 'Apple token missing sub'}), 401

    # Email: prefer the verified token claim; fall back to the client-provided
    # value (first-login only); else synthesize a stable placeholder so the
    # NOT NULL email column is satisfied for a user who hid their address.
    email = (claims.get('email')
             or (body.get('email') or '').strip()
             or f'{apple_sub}@privaterelay.appleid.com')

    # Find-or-create by Apple sub (the stable per-app user id), mirroring /google.
    try:
        user = models.get_user_by_apple_sub(apple_sub)
        if not user:
            user = models.create_user_apple(email, apple_sub)
            if not user:
                return jsonify({'error': 'Could not create account'}), 500
        else:
            models.update_user_login_time(user['id'])
            user = models.get_user_by_id(user['id'])
    except Exception:
        current_app.logger.exception('mobile apple sign-in: user lookup failed')
        return jsonify({'error': 'Account lookup failed'}), 500

    rider_id = user.get('rider_id')
    profile_complete = bool(user.get('profile_completed') and rider_id)
    token = mint_mobile_token(user['id'], rider_id)
    return jsonify({
        'token': token,
        'rider_id': rider_id,
        'profile_complete': profile_complete,
    }), 200


# A dedicated, non-Google app_user that the demo login is pinned to, so reviewer
# sessions never collide with a real Google account.
DEMO_GOOGLE_ID = 'demo-reviewer'
DEMO_EMAIL = 'appreview@teamasha.demo'


@api_auth_bp.route('/demo', methods=['POST'])
def demo_signin():
    """Mint a bearer token for a demo/reviewer account.

    Apple App Review can't complete Google OAuth, so this issues a normal mobile
    token for a fixed rider (DEMO_RIDER_ID). It is invisible (404) unless
    DEMO_MODE_ENABLED is set, so it is not an auth path in normal production —
    enable it only while an app review is in flight.

    Returns: {token, rider_id, profile_complete} — same shape as /google.
    """
    if not current_app.config.get('DEMO_MODE_ENABLED'):
        # 404 (not 403) so the endpoint doesn't even advertise its existence.
        return jsonify({'error': 'Not found'}), 404

    raw_rider_id = current_app.config.get('DEMO_RIDER_ID')
    try:
        rider_id = int(raw_rider_id)
    except (TypeError, ValueError):
        current_app.logger.error('demo sign-in: DEMO_RIDER_ID is unset/invalid (%r)', raw_rider_id)
        return jsonify({'error': 'Demo login is not configured'}), 503

    try:
        if not models.get_rider_by_id(rider_id):
            current_app.logger.error('demo sign-in: rider %s not found', rider_id)
            return jsonify({'error': 'Demo login is not configured'}), 503

        # Find-or-create the dedicated demo user and keep it linked to the rider.
        user = models.get_user_by_google_id(DEMO_GOOGLE_ID)
        if not user:
            user = models.create_user(DEMO_EMAIL, DEMO_GOOGLE_ID)
            if not user:
                return jsonify({'error': 'Could not create demo account'}), 500
        if user.get('rider_id') != rider_id:
            models.complete_user_profile(user['id'], rider_id)
        else:
            models.update_user_login_time(user['id'])
    except Exception:
        current_app.logger.exception('demo sign-in: account setup failed')
        return jsonify({'error': 'Demo account setup failed'}), 500

    token = mint_mobile_token(user['id'], rider_id)
    return jsonify({
        'token': token,
        'rider_id': rider_id,
        'profile_complete': True,
    }), 200
