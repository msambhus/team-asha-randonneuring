"""BrevetHub configuration.

Its own environment namespace and secret. It reuses Team Asha's existing Google
OAuth *client* (same GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET) — the owner just
adds BrevetHub's redirect URIs to that client — but everything else is separate.
No Team Asha config is imported.
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
    # Separate operator credential for national cache/pipeline maintenance. This is
    # intentionally not a club-owner permission: calendar and RUSA refreshes mutate
    # global BrevetHub rp_* data.
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD')

    # FIT merge uploads are parsed with a route-local limit. Keep this below
    # Vercel's request-body ceiling and keep every uploaded part in memory.
    FIT_MERGE_MAX_FILES = 20
    FIT_MERGE_MAX_BYTES = 4 * 1024 * 1024

    # Private Supabase Storage for evidence images. Images are uploaded one at a
    # time so Vercel never receives a large combined request body.
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_SERVICE_ROLE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
    EVIDENCE_BUCKET = os.environ.get('BREVETHUB_EVIDENCE_BUCKET', 'brevethub-evidence')
    EVIDENCE_IMAGE_MAX_BYTES = 10 * 1024 * 1024
    EVIDENCE_TOTAL_MAX_BYTES = 25 * 1024 * 1024

    # Google OAuth — reuse Team Asha's existing web client. The owner registers
    # BrevetHub's redirect URIs on that same client (see brevethub/README.md).
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
    GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
    # Optional override for OAuth redirect (local dev defaults to localhost:PORT).
    GOOGLE_OAUTH_REDIRECT_URI = os.environ.get('GOOGLE_OAUTH_REDIRECT_URI')

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
    # Runnernet (the Team Asha rebuild) runs on its own database, so it cannot read
    # the shared handoff table directly — it redeems via POST /strava/broker/redeem.
    BROKER_RUNNERNET_ORIGIN = 'runnernet'
    BROKER_ALLOWED_ORIGINS = {BROKER_TEAM_ASHA_ORIGIN, BROKER_RUNNERNET_ORIGIN}
    # Shared bearer secret a separate-DB consumer presents to redeem a one-time
    # handoff code for its tokens. Unset => the redeem endpoint 503s (never leaks).
    BROKER_REDEEM_SECRET = os.environ.get('BROKER_REDEEM_SECRET')
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

    # Per-deployment club identity — each BrevetHub instance serves one club.
    # HOST_CLUB_ID loads the club record from rp_club; HOST_REGION_PREFIX
    # (e.g. "CA: San Francisco") scopes the home-page schedule to that RBA area.
    HOST_CLUB_ID = int(os.environ['BREVETHUB_CLUB_ID']) if os.environ.get('BREVETHUB_CLUB_ID') else None
    HOST_REGION_PREFIX = os.environ.get('BREVETHUB_REGION_PREFIX')
    HOST_CLUB_ABBREV = os.environ.get('BREVETHUB_CLUB_ABBREV')
    HOST_HERO_HEADLINE = os.environ.get('BREVETHUB_HERO_HEADLINE')
    HOST_HERO_BODY = os.environ.get('BREVETHUB_HERO_BODY')
    HOST_NEW_RIDER_GUIDE_URL = os.environ.get('BREVETHUB_NEW_RIDER_GUIDE_URL')
    HOST_ABOUT_URL = os.environ.get('BREVETHUB_ABOUT_URL')

    # Session security — HTTPS-only cookies in production, 30-day persistent login.
    SESSION_COOKIE_SECURE = _IS_PRODUCTION
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
