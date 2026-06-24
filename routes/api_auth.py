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
