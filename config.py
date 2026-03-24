import os

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

    # Google OAuth Configuration
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
    GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

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

    # RideWithGPS API Configuration
    RWGPS_API_KEY = os.environ.get('RWGPS_API_KEY')
    RWGPS_AUTH_TOKEN = os.environ.get('RWGPS_AUTH_TOKEN')

    # Cron Job Authentication
    CRON_SECRET = os.environ.get('CRON_SECRET')

    # OpenAI Configuration (optional — AI coaching falls back to rule-based if not set)
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

    # Session configuration for production security
    SESSION_COOKIE_SECURE = _IS_PRODUCTION  # HTTPS only in prod
    SESSION_COOKIE_HTTPONLY = True  # Prevent JavaScript access to cookies
    SESSION_COOKIE_SAMESITE = 'Lax'  # CSRF protection
