"""JSON auth endpoints for the native mobile app.

The web uses Google OAuth + Flask session cookies (routes/auth.py). Native apps
can't use that, so this blueprint exchanges a Google **ID token** (obtained by
the app's native Google Sign-In) for a stateless bearer token the app sends as
`Authorization: Bearer <token>` on subsequent API calls.

The Google account → user/rider mapping deliberately reuses the SAME model
helpers as the web OAuth callback so the two login paths stay in lockstep.
"""
import html
import json
from datetime import datetime, timedelta, timezone

import psycopg2
from flask import Blueprint, request, jsonify, current_app, g

import models
from auth import mint_mobile_token, token_or_session_required
from services import otp_service

api_auth_bp = Blueprint('api_auth', __name__)


def _client_ip():
    """Best-effort client IP for rate limiting. On Vercel the real client is the
    first hop of X-Forwarded-For; fall back to remote_addr. Truncated to the
    column width. Returns None if nothing is available (limiter treats None as
    'don't block')."""
    fwd = request.headers.get('X-Forwarded-For', '')
    ip = (fwd.split(',')[0].strip() if fwd else '') or (request.remote_addr or '')
    return ip[:64] or None


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
    """Cheap structural email check (not RFC-perfect; blocks obvious garbage).
    Length-capped to the column width so an overlong value can't reach the DB and
    500 on truncation."""
    import re
    return bool(email) and len(email) <= otp_service.MAX_EMAIL_LEN and \
        bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email))


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


# ===================== EMAIL OTP (passwordless login) =====================
#
# The iOS app dropped Google + Sign in with Apple (App Store Guideline 4.8) and
# offers passwordless email OTP alongside email+password. Requesting a code emails
# a 6-digit code AND a magic link (either redeems the SAME OTP). Verify
# find-or-creates the account by email, so an existing Google/Apple/password
# member simply gets a code at their verified email and lands on their SAME
# app_user row — removing the buttons orphans no one. Phase 2 reuses verify() for
# SMS. All responses avoid account enumeration.


@api_auth_bp.route('/otp/request', methods=['POST'])
def otp_request():
    """Issue a login OTP (6-digit code + magic link) to an email address.

    Body: {"email": "..."}
    Returns a generic 200 (no enumeration) on success. 400 on a malformed email,
    429 when rate-limited, 502 if the code email couldn't be sent. Account
    resolution/creation happens in /otp/verify, so requesting a code never
    mutates accounts.
    """
    body = request.get_json(silent=True) or {}
    email = (body.get('email') or '').strip().lower()
    if not _valid_email(email):
        return jsonify({'error': 'A valid email is required'}), 400

    ip = _client_ip()
    # Rate limits: (1) per-IP across ALL emails so the endpoint can't be used to
    # email-bomb many victims or brute the code space from one source; (2) per-email
    # hourly cap; (3) a short per-email cooldown between sends.
    try:
        now = datetime.now(timezone.utc)
        hour_ago = now - timedelta(hours=1)
        if models.count_recent_otps_by_ip(ip, hour_ago) >= otp_service.IP_MAX_PER_HOUR:
            return jsonify({'error': 'Too many code requests. Try again later.'}), 429
        if models.count_recent_otps(email, hour_ago) >= otp_service.MAX_PER_HOUR:
            return jsonify({'error': 'Too many code requests. Try again later.'}), 429
        latest = models.get_active_otp_by_identifier(email)
        if latest and latest['created_at'] > now - timedelta(seconds=otp_service.RESEND_COOLDOWN_SECONDS):
            return jsonify({'error': 'A code was just sent. Please wait a moment.'}), 429
    except Exception:
        current_app.logger.exception('otp request: rate-limit check failed')
        return jsonify({'error': 'Could not send a code'}), 500

    code = otp_service.generate_code()
    link_token = otp_service.new_link_token()
    try:
        # Supersede any still-live code for this email so only the newest is valid
        # (turns the per-code attempts cap into an effective per-email lockout).
        models.invalidate_active_otps(email)
        models.create_otp(
            email,
            otp_service.hash_code(code),
            otp_service.hash_link_token(link_token),
            otp_service.expiry_from_now(),
            request_ip=ip,
        )
    except Exception:
        current_app.logger.exception('otp request: could not store code')
        return jsonify({'error': 'Could not send a code'}), 500

    if not otp_service.send_otp_email(email, code, link_token):
        # Mail not configured or Resend failed — don't leak which; a retry is safe.
        current_app.logger.error('otp request: email send failed for %s', email)
        return jsonify({'error': 'Could not send the code email. Please try again.'}), 502

    return jsonify({'message': 'If that email can receive codes, one is on its way.'}), 200


@api_auth_bp.route('/otp/verify', methods=['POST'])
def otp_verify():
    """Verify a login OTP and mint a bearer token.

    Body (one of):
      {"email": "...", "code": "123456", "phone": "<optional>"}   # code path
      {"link_token": "<magic-link token>", "phone": "<optional>"} # magic-link path
    Find-or-creates the account by email; ``phone`` (if given) is stored
    UNVERIFIED for a future SMS OTP. Returns {token, rider_id, profile_complete}.
    """
    body = request.get_json(silent=True) or {}
    phone = (body.get('phone') or '').strip() or None
    link_token = (body.get('link_token') or '').strip()
    identifier = None

    # Validate the optional phone up front — before the code is consumed — so a
    # garbage/overlong value returns a clean 400 instead of a 500 that would burn
    # the (now single-use) code and leave the user unable to sign in.
    if phone is not None and not otp_service.valid_phone(phone):
        return jsonify({'error': 'Please enter a valid phone number, or leave it blank.'}), 400

    try:
        if link_token:
            otp = models.get_active_otp_by_link_hash(otp_service.hash_link_token(link_token))
            if not otp:
                return jsonify({'error': 'This link is invalid or has expired.'}), 401
            identifier = otp['identifier']
        else:
            email = (body.get('email') or '').strip().lower()
            code = (body.get('code') or '').strip()
            if not _valid_email(email) or not code:
                return jsonify({'error': 'Email and code are required'}), 400
            otp = models.get_active_otp_by_identifier(email)
            if not otp:
                return jsonify({'error': 'Incorrect or expired code'}), 401
            # Cap wrong tries per code so a 6-digit code can't be brute-forced.
            if otp['attempts'] >= otp_service.MAX_ATTEMPTS:
                return jsonify({'error': 'Too many attempts. Request a new code.'}), 429
            if not otp_service.verify_code(code, otp['code_hash']):
                models.increment_otp_attempts(otp['id'])
                return jsonify({'error': 'Incorrect or expired code'}), 401
            identifier = email

        # Single-use: only the caller that flips consumed_at proceeds (a
        # concurrent double-submit loses the race and is rejected).
        if not models.consume_otp(otp['id']):
            return jsonify({'error': 'This code was already used. Request a new one.'}), 401

        user = models.get_user_by_email(identifier)
        if not user:
            user = models.create_user_email_otp(identifier, phone)
            if not user:
                return jsonify({'error': 'Could not create account'}), 500
        else:
            models.update_user_login_time(user['id'])
            if phone:
                models.set_user_phone(user['id'], phone)
            user = models.get_user_by_id(user['id'])
    except psycopg2.errors.UniqueViolation:
        # Lost a signup race; the account now exists — fetch it and continue.
        user = models.get_user_by_email(identifier) if identifier else None
        if not user:
            return jsonify({'error': 'Could not create account'}), 500
    except Exception:
        current_app.logger.exception('otp verify: failed')
        return jsonify({'error': 'Sign-in failed'}), 500

    rider_id = user.get('rider_id')
    profile_complete = bool(user.get('profile_completed') and rider_id)
    token = mint_mobile_token(user['id'], rider_id)
    return jsonify({
        'token': token,
        'rider_id': rider_id,
        'profile_complete': profile_complete,
    }), 200


@api_auth_bp.route('/otp/magic', methods=['GET'])
def otp_magic():
    """Bounce a clicked magic link into the native app via the teamasha:// scheme.

    The email link is https (clickable from any mail client); this page redirects
    into the app, which then POSTs the token to /otp/verify to finish sign-in. We
    do NOT consume the OTP here — redemption stays single-use in verify. A raw 302
    to a custom scheme is unreliable, so we serve a tiny interstitial that
    auto-opens the app with a manual fallback link.
    """
    token = (request.args.get('token') or '').strip()
    if not token:
        return _magic_page(None, 'This sign-in link is missing its token.')

    # Non-authoritative liveness check so a dead link fails friendly. Only a token
    # that matches a real OTP row is ever echoed back (it was generated by us and
    # is URL-safe), so there's no reflected-input XSS surface.
    try:
        otp = models.get_active_otp_by_link_hash(otp_service.hash_link_token(token))
    except Exception:
        otp = None
    if not otp:
        return _magic_page(None, 'This sign-in link is invalid or has expired. Request a new code in the app.')

    return _magic_page(otp_service.app_deep_link(token), None)


def _magic_page(deep_link, error):
    """Minimal HTML interstitial for the magic-link hop into the native app."""
    if error:
        inner = f'<p>{error}</p>'
        status = 410
    else:
        # deep_link is only ever a server-generated token_urlsafe value that matched
        # a real OTP row, so it carries no HTML/JS metacharacters. We still escape
        # both output contexts as defense-in-depth against any future change to how
        # deep_link is built: html.escape for the href attribute, and a '<'→<
        # pass for the JS string (json.dumps does NOT escape '</script>').
        href_link = html.escape(deep_link, quote=True)
        js_link = json.dumps(deep_link).replace('<', '\\u003c')
        inner = (
            '<p>Signing you in…</p>'
            f'<p><a href="{href_link}" style="color:#1a2a4f;font-weight:600">Open the Team Asha app</a></p>'
            f'<script>window.location.replace({js_link});</script>'
        )
        status = 200
    page = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Team Asha sign-in</title></head>'
        '<body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;text-align:center;padding:48px 24px;color:#111827">'
        '<h2 style="color:#1a2a4f">Team Asha Randonneuring</h2>'
        f'{inner}</body></html>'
    )
    return current_app.response_class(page, status=status, mimetype='text/html')


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
