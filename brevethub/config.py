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

    # Strava OAuth — reuse Team Asha's existing Strava app (client 113090) by
    # default; the owner sets STRAVA_CLIENT_SECRET on the BrevetHub Vercel
    # project and registers BrevetHub's callback URL on that Strava app. These
    # are BrevetHub's own config keys (nothing is imported from Team Asha) but
    # they intentionally mirror Team Asha's Strava app so one Strava app serves
    # both. See brevethub/README.md.
    STRAVA_CLIENT_ID = os.environ.get('STRAVA_CLIENT_ID', '113090')
    STRAVA_CLIENT_SECRET = os.environ.get('STRAVA_CLIENT_SECRET')
    STRAVA_AUTH_URL = 'https://www.strava.com/oauth/authorize'
    STRAVA_TOKEN_URL = 'https://www.strava.com/oauth/token'
    STRAVA_API_BASE = 'https://www.strava.com/api/v3'
    STRAVA_SCOPE = 'activity:read_all'

    # Shared Strava OAuth broker. BrevetHub hosts the Strava callback for both
    # apps: /strava/connect accepts either a logged-in BrevetHub rider (unchanged)
    # or a signed Team-Asha origin, and /strava/callback routes tokens to
    # rp_strava_connection (BrevetHub rider) or to a one-time rp_strava_broker_handoff
    # row + 302 back to Team Asha (broker origin). BROKER_HMAC_SECRET must be the
    # SAME value set on the Team Asha Vercel project.
    BROKER_HMAC_SECRET = os.environ.get('BROKER_HMAC_SECRET')
    BROKER_STATE_MAX_AGE = 600           # signed-state freshness window (seconds)
    BROKER_HANDOFF_TTL = 300             # one-time handoff-code lifetime (seconds)
    # Origins allowed to broker a Strava connect through BrevetHub.
    BROKER_TEAM_ASHA_ORIGIN = 'team-asha'
    # Absolute return-URL allowlist (scheme://host) for the open-redirect guard —
    # only Team Asha's and BrevetHub's own origins. Overridable via env
    # (comma-separated) for preview deploys / custom domains.
    BROKER_RETURN_URL_ALLOWLIST = [
        o.strip() for o in os.environ.get(
            'BROKER_RETURN_URL_ALLOWLIST',
            'https://team-asha-randonneuring.vercel.app,https://brevethub.vercel.app',
        ).split(',') if o.strip()
    ]

    # Session security — HTTPS-only cookies in production, 30-day persistent login.
    SESSION_COOKIE_SECURE = _IS_PRODUCTION
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
