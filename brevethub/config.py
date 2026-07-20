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

    # Scheduled-refresh auth. The Vercel cron that warms the calendar cache
    # (/cron/refresh-calendar) must present `Authorization: Bearer <CRON_SECRET>`.
    # Unset locally → the cron endpoint 500s (never runs an unauthenticated scrape);
    # set it on the BrevetHub Vercel project. Separate from Team Asha's CRON_SECRET.
    CRON_SECRET = os.environ.get('CRON_SECRET')

    # RideWithGPS API credentials — used by the reused shared/rwgps.py engine to
    # fetch real route data for club-owner-generated ride plans and the
    # /cron/warm-brevet-plans warmer. Both must be added to the BrevetHub Vercel
    # project before real plans can be generated; the shared engine reads neither
    # from a request context (they are passed in explicitly), so unset keys simply
    # make generation fail soft (the guest /plan page never calls RWGPS live — it
    # only reads persisted rows). Get them at ridewithgps.com → Account Settings →
    # Developers tab.
    RWGPS_API_KEY = os.environ.get('RWGPS_API_KEY')
    RWGPS_AUTH_TOKEN = os.environ.get('RWGPS_AUTH_TOKEN')

    # Mapbox GL token for the member live map (Surface B). Copy from Team Asha's
    # Vercel project. When UNSET the member map falls back to a clear "map
    # unavailable" state and never 500s — it does not block the build.
    MAPBOX_ACCESS_TOKEN = os.environ.get('MAPBOX_ACCESS_TOKEN')

    # Mobile demo/reviewer sign-in. A native client (or App Review) has no web
    # session cookie, so /api/auth/demo mints a Bearer token for a fixed rider —
    # but ONLY when DEMO_MODE_ENABLED is truthy (else the endpoint 404s and is not
    # an auth path in normal production). DEMO_RIDER_ID is the rider that token
    # authenticates as. Enable both only while an app review is in flight. Mirrors
    # Team Asha's demo login (BrevetHub carries no separate user table, so the
    # token is minted straight for the rider).
    DEMO_MODE_ENABLED = os.environ.get('DEMO_MODE_ENABLED', '').lower() in ('1', 'true', 'yes')
    DEMO_RIDER_ID = os.environ.get('DEMO_RIDER_ID')

    # Session security — HTTPS-only cookies in production, 30-day persistent login.
    SESSION_COOKIE_SECURE = _IS_PRODUCTION
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
