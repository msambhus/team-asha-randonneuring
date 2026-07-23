import os
from datetime import timedelta

_VERCEL_ENV = os.environ.get('VERCEL_ENV', '')
_IS_PRODUCTION = _VERCEL_ENV == 'production'

_SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-key-change-in-prod')
_ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'asha2026')

if _IS_PRODUCTION:
    if _SECRET_KEY == 'dev-key-change-in-prod' or len(_SECRET_KEY) < 16:
        raise RuntimeError(
            "SECRET_KEY is not set or too weak. "
            "Set a random 32+ character value in Vercel environment variables."
        )
    if _ADMIN_PASSWORD == 'asha2026':
        raise RuntimeError(
            "ADMIN_PASSWORD is using the insecure default 'asha2026'. "
            "Set a strong password in Vercel environment variables."
        )


class Config:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATABASE_URL = os.environ.get('DATABASE_URL')
    SECRET_KEY = _SECRET_KEY
    ADMIN_PASSWORD = _ADMIN_PASSWORD
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'riders')
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # 2MB max upload

    # FIT merge tool (/tools/merge-fit) upload limits. The larger byte cap is
    # enforced route-locally inside routes/tools.py, which parses the multipart
    # body with werkzeug.formparser.parse_form_data(max_content_length=...) rather
    # than touching the global MAX_CONTENT_LENGTH above — that stays 2 MB so the
    # disk-writing rider-photo upload path it guards is not broadened.
    #
    # Capped at 4 MB, deliberately UNDER Vercel's ~4.5 MB serverless-function
    # request-body ceiling: this app is deployed as a single Python function
    # (api/index.py, see vercel.json), and Vercel rejects larger bodies with a
    # platform 413 before Flask runs. A higher cap here would be a promise the
    # production platform can't keep, so the app's limit must stay below it.
    FIT_MERGE_MAX_FILES = 20
    FIT_MERGE_MAX_BYTES = 4 * 1024 * 1024  # 4 MB total (under Vercel's ~4.5 MB body cap)

    # Google OAuth Configuration
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
    GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
    # iOS native Google Sign-In client id — the audience the mobile app's Google
    # ID tokens are verified against (POST /api/auth/google). Separate from the
    # web client above. Optional: the endpoint returns 503 until it's set.
    GOOGLE_IOS_CLIENT_ID = os.environ.get('GOOGLE_IOS_CLIENT_ID')

    # Demo / reviewer login (POST /api/auth/demo) — lets Apple App Review sign in
    # without Google OAuth. Disabled by default: the endpoint 404s unless
    # DEMO_MODE_ENABLED is truthy, and it issues a token for DEMO_RIDER_ID only.
    # Turn both on in the production environment during App Review, then off.
    DEMO_MODE_ENABLED = os.environ.get('DEMO_MODE_ENABLED', '').strip().lower() in ('1', 'true', 'yes', 'on')
    DEMO_RIDER_ID = os.environ.get('DEMO_RIDER_ID')

    # Linear API Configuration
    LINEAR_API_KEY = os.environ.get('LINEAR_API_KEY')
    LINEAR_TEAM_ID = '33d7eaca-512f-4bac-b5cb-d6d61ac2fa74'
    LINEAR_LABEL_BUG = 'f5529bdf-573a-47d3-8027-3d0cb6732e61'
    LINEAR_LABEL_FEATURE = '93914cc6-28ef-4397-a109-fe38ecfc3160'

    # Strava OAuth Configuration
    STRAVA_CLIENT_ID = os.environ.get('STRAVA_CLIENT_ID', '113090')
    STRAVA_CLIENT_SECRET = os.environ.get('STRAVA_CLIENT_SECRET')
    STRAVA_AUTH_URL = 'https://www.strava.com/oauth/authorize'
    STRAVA_TOKEN_URL = 'https://www.strava.com/oauth/token'
    STRAVA_API_BASE = 'https://www.strava.com/api/v3'
    STRAVA_SCOPE = 'activity:read_all'

    # Shared Strava OAuth broker (BrevetHub hosts the Strava callback for both
    # apps). When STRAVA_BROKER_ENABLED is on, /strava/connect signs a state with
    # BROKER_HMAC_SECRET (shared, identical value on both Vercel projects) and
    # redirects to STRAVA_BROKER_URL instead of straight to Strava; the tokens come
    # back via a one-time handoff row consumed at /strava/broker-return. Off by
    # default so the direct /strava/callback flow stays the rollback path.
    BROKER_HMAC_SECRET = os.environ.get('BROKER_HMAC_SECRET')
    STRAVA_BROKER_ENABLED = os.environ.get('STRAVA_BROKER_ENABLED', '').strip().lower() in ('1', 'true', 'yes', 'on')
    STRAVA_BROKER_URL = os.environ.get('STRAVA_BROKER_URL', 'https://brevethub.vercel.app/strava/connect')
    STRAVA_BROKER_ORIGIN = 'team-asha'   # origin token this app sends to the broker
    BROKER_STATE_MAX_AGE = 600           # signed-state freshness window (seconds)

    # RideWithGPS API Configuration
    RWGPS_API_KEY = os.environ.get('RWGPS_API_KEY')
    RWGPS_AUTH_TOKEN = os.environ.get('RWGPS_AUTH_TOKEN')

    # Cron Job Authentication
    CRON_SECRET = os.environ.get('CRON_SECRET')

    # Mapbox Configuration (for weather wind map)
    MAPBOX_ACCESS_TOKEN = os.environ.get('MAPBOX_ACCESS_TOKEN')

    # OpenAI Configuration (optional — AI coaching falls back to rule-based if not set)
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

    # Session configuration for production security
    SESSION_COOKIE_SECURE = _IS_PRODUCTION  # HTTPS only in prod
    SESSION_COOKIE_HTTPONLY = True  # Prevent JavaScript access to cookies
    SESSION_COOKIE_SAMESITE = 'Lax'  # CSRF protection
    # Keep web logins alive for 30 days (matching the native app's bearer token).
    # Without this the login cookie is a transient browser-session cookie that
    # mobile browsers/PWAs drop on backgrounding — causing frequent logouts.
    # Requires session.permanent = True at login (routes/auth.py). Flask's default
    # SESSION_REFRESH_EACH_REQUEST slides this window forward on each visit.
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
