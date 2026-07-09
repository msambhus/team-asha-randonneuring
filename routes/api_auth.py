"""JSON auth endpoints for the native mobile app.

The web uses Google OAuth + Flask session cookies (routes/auth.py). Native apps
can't use that, so this blueprint exchanges a Google **ID token** (obtained by
the app's native Google Sign-In) for a stateless bearer token the app sends as
`Authorization: Bearer <token>` on subsequent API calls.

The Google account → user/rider mapping deliberately reuses the SAME model
helpers as the web OAuth callback so the two login paths stay in lockstep.
"""
from flask import Blueprint, request, jsonify, current_app, g

import models
from auth import mint_mobile_token, token_or_session_required

api_auth_bp = Blueprint('api_auth', __name__)


def _demo_rider_id():
    """The configured demo rider id as an int, or None if unset/invalid."""
    raw = current_app.config.get('DEMO_RIDER_ID')
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


@api_auth_bp.route('/account', methods=['DELETE'])
@token_or_session_required
def delete_account():
    """Permanently delete the authenticated user's account and data.

    App Store Guideline 5.1.1(v): an app that supports account creation must let
    users delete their account in-app. Auth accepts the mobile bearer token or a
    web session; a rider profile is NOT required (a user who never finished
    setup can still delete). The shared demo rider is preserved so App Review can
    exercise deletion without wiping the demo data.
    """
    user_id = g.get('user_id')
    if not user_id:
        # Session-only callers may have a rider_id but no user_id; deletion is
        # keyed on the app_user, so we need one.
        return jsonify({'error': 'Not authenticated'}), 401

    demo_rider_id = _demo_rider_id()
    preserve_rider = demo_rider_id is not None and g.get('rider_id') == demo_rider_id

    try:
        deleted = models.delete_account(user_id, preserve_rider=preserve_rider)
    except Exception:
        current_app.logger.exception('account deletion failed for user %s', user_id)
        return jsonify({'error': 'Account deletion failed'}), 500

    if not deleted:
        return jsonify({'error': 'Account not found'}), 404
    return jsonify({'deleted': True}), 200


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
    # Be tolerant of clients that accidentally wrap the token: a stray "Bearer "
    # prefix or surrounding quotes corrupt the JWT header segment and surface as
    # PyJWT "Invalid header padding". Normalize before verifying.
    if identity_token[:7].lower() == 'bearer ':
        identity_token = identity_token[7:].strip()
    identity_token = identity_token.strip('"').strip()
    if not identity_token:
        _raw = body.get('identity_token')
        current_app.logger.warning(
            'mobile apple sign-in: empty token | body_keys=%s raw_type=%s raw_len=%s '
            'ctype=%r content_len=%s email=%r',
            list(body.keys()), type(_raw).__name__,
            (len(_raw) if isinstance(_raw, str) else 'n/a'),
            request.content_type, request.content_length,
            (body.get('email') or '')[:30] if isinstance(body.get('email'), str) else body.get('email'),
        )
        return jsonify({'error': 'identity_token is required'}), 400

    # Verify signature (Apple JWKS), issuer, expiry, and audience == our bundle id.
    try:
        claims = _verify_apple_id_token(identity_token, audience)
    except Exception as exc:  # noqa: BLE001 — any verification failure → 401
        # Log a non-sensitive fingerprint (JWT header is public; segment lengths
        # reveal structure) so a malformed token can be diagnosed without leaking
        # the payload/signature.
        _segs = identity_token.split('.')
        current_app.logger.warning(
            'mobile apple sign-in: token verify failed: %s | len=%d segs=%d seglens=%s head=%r',
            exc, len(identity_token), len(_segs), [len(s) for s in _segs], identity_token[:24],
        )
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
        if user:
            models.update_user_login_time(user['id'])
            user = models.get_user_by_id(user['id'])
        else:
            # First Apple sign-in: link to an existing account by Apple's
            # VERIFIED email so a member who set up their profile on the web
            # (Google/email) keeps their rider profile — instead of getting a
            # fresh, empty account and being stuck on onboarding. Only the
            # token's own verified email is trusted for linking; never the
            # client-supplied body email or a Hide-My-Email privaterelay
            # placeholder (those must not attach a stranger to an account).
            token_email = claims.get('email')
            email_verified = str(claims.get('email_verified', '')).lower() == 'true'
            existing = (models.get_user_by_email(token_email)
                        if token_email and email_verified else None)
            if existing and not existing.get('apple_sub'):
                models.link_apple_sub(existing['id'], apple_sub)
                models.update_user_login_time(existing['id'])
                user = models.get_user_by_id(existing['id'])
            else:
                user = models.create_user_apple(email, apple_sub)
                if not user:
                    return jsonify({'error': 'Could not create account'}), 500
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


# ── Email + password (mobile's 3rd login option) ─────────────────────────
# First-party credential alongside Google + Sign in with Apple. Existing
# Google/Apple accounts are unaffected (password_hash stays NULL); password is
# for new members who prefer it. NOTE: no email-verification step in v1 — a
# follow-up if abuse ever appears; the account is inert until a rider profile is
# claimed, and signup refuses emails that already have ANY account (no hijack).
_MIN_PASSWORD_LEN = 8
# Upper bound so a multi-MB password body can't amplify into heavy scrypt CPU on
# a serverless function (DoS). 128 is well above any real password.
_MAX_PASSWORD_LEN = 128


def _valid_email(email):
    """Cheap structural email check (not RFC-perfect; blocks obvious garbage)."""
    import re
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email or ''))


@api_auth_bp.route('/signup', methods=['POST'])
def password_signup():
    """Create an email + password account and mint a bearer token.

    Body: {"email": "...", "password": "..."}
    Returns: {token, rider_id, profile_complete} — same shape as /google.
    Refuses an email that already has an account (409) so a password signup can
    never take over an existing Google/Apple account.
    """
    from werkzeug.security import generate_password_hash

    body = request.get_json(silent=True) or {}
    email = (body.get('email') or '').strip().lower()
    password = body.get('password') or ''

    if not _valid_email(email):
        return jsonify({'error': 'A valid email is required'}), 400
    if not (_MIN_PASSWORD_LEN <= len(password) <= _MAX_PASSWORD_LEN):
        return jsonify({'error': f'Password must be {_MIN_PASSWORD_LEN}-{_MAX_PASSWORD_LEN} characters'}), 400

    import psycopg2
    try:
        if models.get_user_by_email(email):
            # Existing account (Google/Apple/password) — don't leak which method.
            return jsonify({'error': 'An account with this email already exists. Try signing in.'}), 409
        user = models.create_user_password(email, generate_password_hash(password))
        if not user:
            return jsonify({'error': 'Could not create account'}), 500
    except psycopg2.errors.UniqueViolation:
        # Lost a TOCTOU race with a concurrent signup (unique lower(email) index).
        return jsonify({'error': 'An account with this email already exists. Try signing in.'}), 409
    except Exception:
        current_app.logger.exception('password signup: account creation failed')
        return jsonify({'error': 'Could not create account'}), 500

    rider_id = user.get('rider_id')
    profile_complete = bool(user.get('profile_completed') and rider_id)
    token = mint_mobile_token(user['id'], rider_id)
    return jsonify({
        'token': token,
        'rider_id': rider_id,
        'profile_complete': profile_complete,
    }), 200


@api_auth_bp.route('/login', methods=['POST'])
def password_login():
    """Verify an email + password and mint a bearer token.

    Body: {"email": "...", "password": "..."}
    Returns: {token, rider_id, profile_complete}. All failures return a generic
    401 (no account enumeration); a Google/Apple-only account (no password_hash)
    also gets the generic 401.
    """
    from werkzeug.security import check_password_hash

    body = request.get_json(silent=True) or {}
    email = (body.get('email') or '').strip().lower()
    password = body.get('password') or ''
    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400
    # An over-length password can never match a real (capped) one; reject before
    # hashing so it can't be used as a scrypt-CPU DoS. Generic 401, no leak.
    if len(password) > _MAX_PASSWORD_LEN:
        return jsonify({'error': 'Incorrect email or password'}), 401

    try:
        user = models.get_user_by_email(email)
        pw_hash = user.get('password_hash') if user else None
        if not user or not pw_hash or not check_password_hash(pw_hash, password):
            return jsonify({'error': 'Incorrect email or password'}), 401
        models.update_user_login_time(user['id'])
        user = models.get_user_by_id(user['id'])
    except Exception:
        current_app.logger.exception('password login: lookup failed')
        return jsonify({'error': 'Sign-in failed'}), 500

    rider_id = user.get('rider_id')
    profile_complete = bool(user.get('profile_completed') and rider_id)
    token = mint_mobile_token(user['id'], rider_id)
    return jsonify({
        'token': token,
        'rider_id': rider_id,
        'profile_complete': profile_complete,
    }), 200
