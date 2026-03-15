# Architecture

**Analysis Date:** 2026-03-14

## Pattern Overview

**Overall:** Server-side web application using Flask (Python) with three-tier architecture: presentation (Jinja2 templates + Tailwind CSS), business logic (routes + services), and data access (models + PostgreSQL via psycopg2).

**Key Characteristics:**
- Monolithic Flask application deployed on Vercel serverless
- Blueprint-based route organization by feature (auth, riders, main, admin, strava, cron)
- Service layer for domain logic (Strava sync, fitness scoring, event scraping)
- Data access layer (models.py) centralizes all SQL queries
- In-memory caching (Flask-Caching) for database queries and computed values
- OAuth2 integration (Google for users, Strava for athlete activities)

## Layers

**Presentation Layer:**
- Purpose: Render HTML responses and handle form submissions
- Location: `templates/` directory with Jinja2 templates
- Contains: HTML views for riders, seasons, profiles, admin dashboards
- Depends on: Flask request/response context, models for data
- Used by: Browser clients accessing Flask routes
- Key pattern: Template inheritance via `base.html`, custom filters (`commafy`, `clean_name`)

**Route Layer (Flask Blueprints):**
- Purpose: Define HTTP endpoints and orchestrate request handling
- Location: `routes/` directory (one blueprint per feature)
  - `main.py`: Home page, about, resources, feedback, season statistics
  - `riders.py`: Rider profiles, season participation, ride plans, fitness analysis
  - `auth.py`: Google OAuth login, profile setup, session management
  - `signup.py`: Rider signup for specific rides
  - `admin.py`: Admin dashboard, ride management, plan generation
  - `strava.py`: Strava OAuth callback, ride analysis
  - `cron.py`: Scheduled tasks (Strava sync, backfill, maintenance)
- Depends on: Models, services, authentication decorators
- Used by: URL routing and HTTP request handling

**Business Logic Layer (Services):**
- Purpose: Implement domain-specific calculations and integrations
- Location: `services/` directory
  - `fitness.py`: Fitness score calculation (0-100 scale, four components)
  - `strava.py`: Strava API token management and activity fetching
  - `strava_analysis.py`: Ride-specific analysis and grading
  - `rusa.py`: Event scraping from RUSA website
  - `rwgps.py`: RideWithGPS route integration
  - `eddington.py`: Eddington number calculation for distance tracking
  - `openai_coach.py`: AI-powered training advice generation
  - `custom_plan_service.py`: Custom ride plan creation and modification
- Depends on: Models, external APIs (Strava, OpenAI)
- Used by: Routes for computation and data transformation

**Data Access Layer (Models):**
- Purpose: Encapsulate all SQL queries and database operations
- Location: `models.py` (single monolithic file)
- Contains: Query functions organized by domain entity
  - Seasons: `get_all_seasons()`, `get_current_season()`, `get_season_by_name()`
  - Riders: `get_all_riders()`, `get_rider_by_rusa()`, `get_riders_for_season()`
  - Rides: `get_rides_for_season()`, `get_ride_by_id()`, `get_upcoming_rides()`
  - User accounts: `get_user_by_id()`, `create_user()`, `get_user_by_google_id()`
  - Strava connections: `get_strava_connection()`, `update_strava_tokens()`
  - Participation: `get_rider_participation()`, `get_participation_matrix()`
  - Statistics: `get_rider_career_stats()`, `get_season_stats()`, `detect_sr_for_rider_season()`
- Depends on: PostgreSQL connection via psycopg2, Flask's `g` object for request-scoped DB
- Used by: All routes and services for data access
- Pattern: RealDictCursor for dict-like row access; `@cache.memoize()` decorator for read-only queries

**Infrastructure Layer:**
- `app.py`: Flask app factory; initializes blueprints, cache, OAuth
- `config.py`: Configuration management (env vars for secrets, URLs, API keys)
- `db.py`: Database connection management using request-scoped `g` object
- `cache.py`: Caching configuration (5-minute TTL, SimpleCache for serverless)
- `auth.py`: Authentication decorators (`login_required`, `user_login_required`, `profile_required`)

## Data Flow

**Typical HTTP Request (e.g., View Rider Profile):**

1. Client requests `/riders/<season_name>/<rusa_id>`
2. Flask routes to `riders_bp.season_rider_profile(season_name, rusa_id)`
3. Route checks authorization via `@user_login_required` decorator
4. Route calls `get_season_by_name(season_name)` → checks cache → queries DB if miss
5. Route calls `get_rider_by_rusa(rusa_id)` → queries DB directly (not cached per CLAUDE.md)
6. Route calls `get_rider_season_stats(rider_id, season_id)` → cached query
7. Route calls `get_strava_connection(rider_id)` → fetches Strava token data
8. If Strava connected, route calls `services.fitness.calculate_fitness_score(activities)` to compute 0-100 score
9. Route renders `rider_profile.html` template with computed data
10. Template filters `clean_name` on ride names to unescape HTML entities
11. Client receives rendered HTML response

**Strava Background Sync (Cron Job):**

1. GitHub Actions triggers `/api/cron/sync-strava` with `Authorization: Bearer {CRON_SECRET}`
2. Route verifies auth, fetches all active Strava connections from DB
3. For each rider (up to 50 per run):
   - Calls `services.strava.sync_rider_activities(rider_id, days=7)` for recent sync
   - Handles token refresh via `_get_valid_token()` if expired
   - Fetches activities from Strava API v3 (last 7 days)
   - Calls `_process_activities()` to insert/update `strava_activity` table
   - Calls `services.eddington.calculate_eddington()` to update Eddington number
4. After recent sync, performs backfill for one rider (90 days further back each run)
5. Returns JSON with sync counts and per-rider details
6. Cache is cleared after write operations via `clear_cache_on_write()`

**User Signup & Profile Creation:**

1. New user logs in with Google OAuth
2. Route creates `user` record with `google_id`, `email`
3. Session set with `user_id`, `email`, `google_id`
4. Route detects `profile_completed = False`, redirects to `/auth/setup_profile`
5. User submits form with RUSA ID and profile data
6. Route validates RUSA ID via `services.rusa_validator.validate_rusa_id()`
7. Route links user to existing `rider` (by RUSA ID) or creates new rider record
8. Route updates `user.rider_id` and `user.profile_completed = True`
9. Route adds `rider_id` and `rider_name` to session
10. Redirect to home; cache is cleared on profile write

**State Management:**
- Session state: User ID, email, rider ID (stored in Flask session cookie)
- Database state: Riders, seasons, rides, participation records, Strava tokens
- Computed state: Fitness scores (computed on-demand from Strava activities), Eddington numbers (computed during sync)
- Cached state: Seasons, rider lists, participation matrices, statistics (5-minute TTL)

## Key Abstractions

**RideStatus Enum:**
- Purpose: Represents rider participation status for a specific ride (pre-ride and post-ride states)
- Examples: `models.py` lines 9-75
- Pattern: String-based enum that matches database TEXT values; supports legacy value normalization (`SIGNED_UP` → `GOING`)
- States:
  - Pre-ride: INTERESTED, MAYBE, GOING
  - Post-ride: FINISHED, DNF, DNS, OTL
  - Other: WITHDRAW
- Methods: `normalize()` (parse string), `is_pre_ride()`, `is_post_ride()`, `is_successful()`

**Fitness Score:**
- Purpose: 0-100 scale of current fitness level from recent Strava activities
- Examples: `services/fitness.py` lines 1-100+
- Pattern: Weighted sum of four components
  - Frequency (0-25): rides per week
  - Volume (0-35): distance + elevation
  - Intensity (0-25): heart rate, power, suffer score (adaptive)
  - Recency (0-15): exponential decay from last ride
- Result: Four-component dict + total score; None if no activities

**Ride Plan:**
- Purpose: Planned route with stops, distances, and elevation profiles
- Pattern: Base ride plan (from RUSA events) can be cloned as custom plan
- Components: `ride_plan` table with stops (`ride_plan_stop`), custom variants per user

**Super Randonneur (SR):**
- Purpose: Achievement for completing 200, 300, 400, 600 km brevets in one season
- Pattern: `detect_sr_for_rider_season(rider_id, season_id)` checks if all four distances finished
- Location: Called during data analysis routes and in admin statistics

**Eddington Number:**
- Purpose: Longest distance ridden that has been completed at least N times
- Pattern: Calculated from Strava activities, updated during cron sync
- Location: `services/eddington.py`

## Entry Points

**Web Server:**
- Location: `app.py` line 88
- Triggers: Vercel serverless function invocation
- Responsibilities: Create Flask app, initialize blueprints, cache, OAuth

**Flask Routes:**
- Location: Blueprints in `routes/` directory
- Triggers: HTTP requests to Flask routes
- Responsibilities: Handle requests, call services/models, render responses

**Cron Job (Background Tasks):**
- Location: `/api/cron/sync-strava` in `routes/cron.py`
- Triggers: GitHub Actions schedule (scheduled dispatch)
- Responsibilities: Sync Strava data, backfill historical activities, compute Eddington

**API Index:**
- Location: `api/index.py`
- Triggers: Vercel API route invocation
- Responsibilities: Delegate to Flask app

## Error Handling

**Strategy:** Try-except wrapping at route level; graceful fallback to mock data for UI views; JSON error responses for API endpoints.

**Patterns:**
- Database unavailable: Route catches exception, renders mock data (home page, season view)
- Strava API error: Service logs error, cron reports counts with error details
- Invalid user input: Form validation; RUSA validator raises `ValueError` if ID invalid
- OAuth failure: Flash error message, redirect to login
- Unauthorized access: Return 401 or redirect to login via decorators

## Cross-Cutting Concerns

**Logging:** Using Flask's built-in `current_app.logger` for info/warning/error messages; cron job logs per-rider sync results.

**Validation:** RUSA ID validation via `utils/rusa_validator.py`; form field validation in templates.

**Authentication:**
- User authentication: Google OAuth via `authlib`
- Admin authentication: Password-based in session (legacy, `@login_required` decorator)
- Cron authentication: Bearer token via `CRON_SECRET` env var

**Caching:**
- Query caching: `@cache.memoize(CACHE_TIMEOUT)` on read-only model functions
- Route caching: `@cache.cached(timeout=CACHE_TIMEOUT)` on read-only routes
- Cache invalidation: `cache.clear()` called after write operations (`clear_cache_on_write()`)
- Serverless consideration: SimpleCache (in-memory) suitable for Vercel (each invocation is isolated)

---

*Architecture analysis: 2026-03-14*
