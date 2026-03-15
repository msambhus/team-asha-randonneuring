# Technology Stack

**Analysis Date:** 2026-03-14

## Languages

**Primary:**
- Python 3.x - Backend application, all route handlers, models, services
- JavaScript - Package management via npm only (Tailwind CSS build tooling)
- HTML/Jinja2 - Server-side templating in `templates/` and `templates/admin/`
- CSS - Tailwind CSS with custom variables and inline styles

**Secondary:**
- SQL - PostgreSQL queries via psycopg2 in `models.py` and service modules

## Runtime

**Environment:**
- Python 3.x (venv activation in `run_dev.sh`)

**Package Managers:**
- pip - Python dependencies via `requirements.txt`
- npm - Node.js dependencies via `package.json`
- Lockfile: `package-lock.json` present

## Frameworks

**Core:**
- Flask 3.0.0 - Web framework, app factory in `app.py`
- Jinja2 3.1.2 - Server-side HTML templating

**Web Server:**
- Werkzeug 3.0.1 - WSGI utilities
- gunicorn 21.2.0 - Production app server

**CSS/Frontend:**
- Tailwind CSS 3.4.1 - Utility-first CSS framework
- PostCSS plugins (postcss-import, postcss-nested) for CSS processing

**Authentication:**
- authlib 1.3.0 - OAuth 2.0 and OpenID Connect (Google login)

**AI/ML:**
- openai >=1.0.0 - Optional GPT-4o-mini API for coaching advice (falls back to rule-based)

**HTTP Client:**
- requests 2.31.0 - HTTP library for external API calls

**Caching:**
- Flask-Caching 2.1.0 - In-memory cache (SimpleCache) for Vercel serverless environment

**Web Scraping:**
- beautifulsoup4 4.12.3 - HTML parsing for RUSA results scraping
- lxml 5.1.0 - XML/HTML parser backend for BeautifulSoup

**Database:**
- psycopg2-binary 2.9.9 - PostgreSQL adapter

**Configuration:**
- python-dotenv 1.2.1 - Load environment variables from `.env`

## Key Dependencies

**Critical:**
- Flask 3.0.0 - Application framework; request routing, session management, blueprints
- psycopg2-binary 2.9.9 - Only PostgreSQL client; database connection pooling via Supabase
- authlib 1.3.0 - Google OAuth 2.0 integration for user authentication
- requests 2.31.0 - API calls to Strava, RUSA, RideWithGPS, OpenAI

**Infrastructure:**
- gunicorn 21.2.0 - WSGI server for Vercel deployment
- python-dotenv 1.2.1 - Secure environment variable loading
- Flask-Caching 2.1.0 - Cache layer (5-minute default TTL in `cache.py`)

## Configuration

**Environment:**
- Configuration class in `config.py` with environment variable overrides
- `.env` file required for production secrets (not committed)
- `.env.example` provided as template

**Build:**
- `tailwindcss` command in npm scripts (`package.json`)
- CSS input: `static/input.css`
- CSS output: `static/output.css` (minified in production)

**Application Configuration:**
- DATABASE_URL - Supabase PostgreSQL connection string (required)
- SECRET_KEY - Flask session secret (default: 'dev-key-change-in-prod')
- ADMIN_PASSWORD - Admin panel password (default: 'asha2026')
- GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET - Google OAuth credentials
- STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET - Strava OAuth credentials
- OPENAI_API_KEY - Optional; coaching advice disabled if missing
- RWGPS_API_KEY, RWGPS_AUTH_TOKEN - RideWithGPS API credentials (optional)
- CRON_SECRET - Bearer token for authenticated cron endpoints
- Session cookie security: HTTPS only in production, HttpOnly, SameSite=Lax

## Platform Requirements

**Development:**
- Python 3.x with venv
- Node.js with npm
- PostgreSQL client libraries (included via psycopg2-binary)
- macOS/Linux/Windows compatible

**Production:**
- Vercel serverless environment (`SESSION_COOKIE_SECURE` triggered by `VERCEL_ENV=production`)
- Supabase PostgreSQL database with transaction pooler (port 6543 for Vercel compatibility)
- Environment variables configured in Vercel project settings

---

*Stack analysis: 2026-03-14*
