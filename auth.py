from functools import wraps
from flask import session, redirect, url_for, request, current_app, flash, g
from itsdangerous import URLSafeTimedSerializer

# Native-app bearer token: a stateless, signed {user_id, rider_id} payload. Lets
# the iOS app authenticate the JSON API without a browser session cookie. Signed
# with the same SECRET_KEY as Flask sessions (no new secret/table); a distinct
# salt keeps it from colliding with any other signed payloads.
MOBILE_TOKEN_SALT = 'mobile-auth'
MOBILE_TOKEN_MAX_AGE = 30 * 24 * 3600   # 30 days


def _mobile_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=MOBILE_TOKEN_SALT)


def mint_mobile_token(user_id, rider_id):
    """Sign a {user_id, rider_id} bearer token for the native app."""
    return _mobile_serializer().dumps({'user_id': user_id, 'rider_id': rider_id})


def load_mobile_token(token):
    """Return the token payload dict, or None if missing/expired/tampered. Never raises."""
    if not token:
        return None
    try:
        data = _mobile_serializer().loads(token, max_age=MOBILE_TOKEN_MAX_AGE)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 — any decode failure (bad sig/expired/garbage) → unauth
        return None


def resolve_identity():
    """Resolve the caller to (user_id, rider_id) from a web session OR a mobile
    Bearer token. Returns (None, None) when neither is present. Either id may be
    None on its own (e.g. logged in but profile not completed → rider_id None)."""
    user_id = session.get('user_id')
    rider_id = session.get('rider_id')
    if user_id or rider_id:
        return user_id, rider_id
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        data = load_mobile_token(auth[len('Bearer '):].strip())
        if data:
            return data.get('user_id'), data.get('rider_id')
    return None, None


def token_or_session_required(f):
    """API auth accepting a web session OR a mobile Bearer token. 401 JSON if
    neither identifies a caller. Stashes user_id/rider_id on flask.g; handlers
    that need a completed profile still check g.rider_id themselves (→ 403),
    preserving the existing two-tier (401 unauthenticated / 403 no profile)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id, rider_id = resolve_identity()
        if not (user_id or rider_id):
            return {'error': 'Authentication required'}, 401
        g.user_id = user_id
        g.rider_id = rider_id
        return f(*args, **kwargs)
    return decorated


def login_required(f):
    """Require user to be logged in (for admin routes)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('admin.login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def user_login_required(f):
    """Require user authentication via Google OAuth. Skipped on localhost (debug mode)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_app.debug:
            return f(*args, **kwargs)
        if not session.get('user_id'):
            flash('Please log in to access this page', 'warning')
            return redirect(url_for('auth.login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def api_login_required(f):
    """Require authentication for API endpoints. Returns 401 JSON on failure.
    Does NOT redirect — use @user_login_required for page routes instead.
    NEVER skips auth in debug mode.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return {'error': 'Authentication required'}, 401
        return f(*args, **kwargs)
    return decorated


def profile_required(f):
    """Require user to have completed profile setup."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            flash('Please log in to access this page', 'warning')
            return redirect(url_for('auth.login', next=request.path))
        
        if not session.get('rider_id'):
            flash('Please complete your profile setup', 'warning')
            return redirect(url_for('auth.setup_profile'))
        
        return f(*args, **kwargs)
    return decorated


def verify_password(password):
    """Verify admin password."""
    return password == current_app.config['ADMIN_PASSWORD']
