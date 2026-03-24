# External Integrations

**Analysis Date:** 2026-03-14

## APIs & External Services

**Authentication:**
- Google OAuth 2.0 - User login and identity
  - SDK/Client: authlib 1.3.0
  - Config: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
  - Discovery URL: `https://accounts.google.com/.well-known/openid-configuration`
  - Scope: openid, email, profile
  - Implementation: `routes/auth.py` - OAuth initialization, login, and callback

**Sports Data:**
- Strava OAuth v2 + API v3 - Activity sync and athlete data
  - SDK/Client: requests library (custom HTTP implementation)
  - Auth: OAuth 2.0 with access/refresh tokens stored in `strava_connection` table
  - Endpoints:
    - `https://www.strava.com/oauth/authorize` - Authorization
    - `https://www.strava.com/oauth/token` - Token exchange/refresh
    - `https://www.strava.com/api/v3` - Activity API base
  - Scope: `activity:read_all`
  - Implementation: `services/strava.py` - Token exchange, refresh, activity fetching
  - Routes: `routes/strava.py` - Connect, callback, sync, disconnect flows
  - Token refresh: Automatic 60-second buffer before expiration

- RideWithGPS API v1 - Route data and control point extraction
  - SDK/Client: requests library (custom HTTP)
  - Auth: API key and auth token headers (`x-rwgps-api-key`, `x-rwgps-auth-token`)
  - Endpoint: `https://ridewithgps.com/api/v1/routes/{route_id}.json`
  - Config: `RWGPS_API_KEY`, `RWGPS_AUTH_TOKEN`
  - Implementation: `services/rwgps.py` - Route fetching, control extraction, ride plan building

**Web Scraping:**
- RUSA (Randonneurs USA) - Official brevet results
  - No API; web scraping via BeautifulSoup
  - Endpoint: `https://rusa.org/cgi-bin/resultsearch_PF.pl?mid={rusa_id}`
  - User-Agent header required
  - Implementation: `services/rusa.py` - Parse HTML table of ride results
  - Output: date, distance_km, finish_time

**AI/LLM:**
- OpenAI GPT-4o-mini - Personalized coaching advice (optional)
  - SDK/Client: openai Python package (>=1.0.0)
  - Auth: `OPENAI_API_KEY` (Bearer token)
  - Model: gpt-4o-mini
  - Implementation: `services/openai_coach.py` - Generate ride-specific coaching recommendations
  - Fallback: Rule-based `generate_training_advice()` if API key missing or fails
  - Cache: In-memory 24-hour TTL per rider + data fingerprint

**Issue Tracking (Configured but Unused):**
- Linear API - Project management and bug tracking
  - Auth: `LINEAR_API_KEY`
  - Team ID: `33d7eaca-512f-4bac-b5cb-d6d61ac2fa74`
  - Label IDs: Bug (`f5529bdf-573a-47d3-8027-3d0cb6732e61`), Feature (`93914cc6-28ef-4397-a109-fe38ecfc3160`)
  - Config: `config.py` lines 16-20
  - Current status: Configured but no active implementation in routes/services

## Data Storage

**Databases:**
- PostgreSQL via Supabase
  - Connection: `DATABASE_URL` - Supabase URI (transaction pooler on port 6543)
  - Client: psycopg2-binary 2.9.9 with RealDictCursor for dict-like access
  - Schema: Managed via Alembic migrations in `migrations/` directory
  - Connection pooling: Vercel serverless compatible with transaction pooler

**File Storage:**
- Local filesystem only
  - Static files: `static/` directory (images, CSS, JS)
  - Rider photos: `static/riders/` (user uploads)
  - Max upload size: 2MB (`MAX_CONTENT_LENGTH` in `config.py`)

**Caching:**
- Flask-Caching SimpleCache (in-memory)
  - Type: SimpleCache (Python dict-based, not distributed)
  - TTL: 300 seconds (5 minutes) default, configurable via `CACHE_TIMEOUT` in `cache.py`
  - Note: In-memory cache loses data on app restart; suitable for serverless with automatic redeployment

## Authentication & Identity

**Auth Provider:**
- Google OAuth 2.0 (primary)
  - OpenID Connect flow via authlib
  - User creation on first login
  - Email and profile scopes

**Session Management:**
- Flask sessions with secure cookie configuration
  - Secret: `SECRET_KEY` env var
  - HttpOnly: True (JavaScript cannot access)
  - SameSite: Lax (CSRF protection)
  - Secure: True in production (HTTPS only)

**Credentials Storage:**
- Strava tokens: `strava_connection` table (access_token, refresh_token, expires_at)
- Google ID: `user` table (google_id field)
- Admin password: `ADMIN_PASSWORD` env var (plaintext comparison in `auth.py`)

## Monitoring & Observability

**Error Tracking:**
- Not detected - errors logged to stdout/stderr (Vercel captures logs)

**Logs:**
- Flask logger: `current_app.logger` for info/warning/error messages
  - Cron job logs in `routes/cron.py`
  - Strava sync logs in `services/strava.py`
  - OAuth errors logged in `routes/auth.py` and `routes/strava.py`

## CI/CD & Deployment

**Hosting:**
- Vercel serverless (implied by `SESSION_COOKIE_SECURE` check for `VERCEL_ENV`)

**CI Pipeline:**
- GitHub Actions (implied by cron job documentation)
  - Cron endpoint: `POST /api/cron/sync-strava` with Bearer token auth
  - Triggered periodically to sync Strava activities across all riders

**Configuration in Vercel:**
- Environment variables via Vercel project settings
- Database: Supabase PostgreSQL accessible from Vercel

## Environment Configuration

**Required env vars (production):**
- `DATABASE_URL` - Supabase connection string (critical)
- `GOOGLE_CLIENT_ID` - Google OAuth app ID
- `GOOGLE_CLIENT_SECRET` - Google OAuth secret
- `STRAVA_CLIENT_SECRET` - Strava OAuth secret (CLIENT_ID has default 113090)
- `SECRET_KEY` - Flask session encryption

**Optional env vars:**
- `OPENAI_API_KEY` - OpenAI API key (coaching falls back to rule-based if missing)
- `RWGPS_API_KEY`, `RWGPS_AUTH_TOKEN` - RideWithGPS credentials (route import disabled if missing)
- `LINEAR_API_KEY` - Linear issue tracking (unused)
- `CRON_SECRET` - Bearer token for GitHub Actions cron jobs
- `ADMIN_PASSWORD` - Admin panel password (default: 'asha2026')

**Secrets location:**
- `.env` file (development only, not committed)
- Vercel project settings (production)
- `.env.example` provides template with required fields

## Webhooks & Callbacks

**Incoming:**
- `POST /auth/google/callback` - Google OAuth callback (handles authorization code exchange)
- `POST /strava/callback` - Strava OAuth callback (handles authorization code exchange)
- `POST /api/cron/sync-strava` - Scheduled Strava sync endpoint (requires Bearer token)

**Outgoing:**
- None detected - Application does not push webhooks to external services

## Third-Party API Rate Limits & Handling

**Strava:**
- Rate limit: 600 requests per 15 minutes (per athlete token)
- Handling: Batch syncs limited to 50 riders per cron run to avoid quota exhaustion
- Backfill strategy: Progressive 90-day historical lookback across multiple cron runs

**RideWithGPS:**
- Rate limit: 429 responses handled with explicit error message
- Handling: Timeout set to 30 seconds; raises exception on API errors

**RUSA:**
- No rate limiting documented
- Scraper includes User-Agent header and 15-second timeout

**OpenAI:**
- Rate limit: Depends on API key plan
- Handling: Requests timeout; falls back to rule-based advice on any error

---

*Integration audit: 2026-03-14*
