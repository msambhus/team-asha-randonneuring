"""BrevetHub configuration.

Its own environment namespace and secret. It reuses Team Asha's existing Google
OAuth *client* (same GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET) — the owner just
adds BrevetHub's redirect URIs to that client — but everything else is separate.
No Strava defaults, no admin password, no Team Asha config is imported.
"""
import os
from datetime import timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()  # local dev: read brevethub/.env / repo-root .env
except ImportError:  # dotenv is optional; production sets real env vars
    pass

_VERCEL_ENV = os.environ.get('VERCEL_ENV', '')
_IS_PRODUCTION = _VERCEL_ENV == 'production'

_SECRET_KEY = os.environ.get('BREVETHUB_SECRET_KEY') or os.environ.get('SECRET_KEY', 'dev-key-change-in-prod')

if _IS_PRODUCTION:
    if _SECRET_KEY == 'dev-key-change-in-prod' or len(_SECRET_KEY) < 16:
        raise RuntimeError(
            "BREVETHUB_SECRET_KEY is not set or too weak. "
            "Set a random 32+ character value in the Vercel environment variables."
        )


class Config:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    # Same Supabase Postgres as Team Asha, but BrevetHub only ever touches rp_* tables.
    DATABASE_URL = os.environ.get('DATABASE_URL')
    SECRET_KEY = _SECRET_KEY

    # Google OAuth — reuse Team Asha's existing web client. The owner registers
    # BrevetHub's redirect URIs on that same client (see brevethub/README.md).
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
    GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

    # Session security — HTTPS-only cookies in production, 30-day persistent login.
    SESSION_COOKIE_SECURE = _IS_PRODUCTION
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
