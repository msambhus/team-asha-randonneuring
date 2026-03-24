# Codebase Structure

**Analysis Date:** 2026-03-14

## Directory Layout

```
team-asha-randonneuring/
├── app.py                      # Flask app factory — creates and initializes app
├── auth.py                     # Authentication decorators (login_required, profile_required)
├── config.py                   # Configuration from environment variables
├── db.py                       # Database connection management (psycopg2)
├── cache.py                    # Caching configuration (Flask-Caching)
├── models.py                   # Data access layer — all SQL queries
├── routes/                     # Flask blueprints (one per feature)
│   ├── __init__.py
│   ├── main.py                 # Home, about, resources, season stats
│   ├── riders.py               # Rider profiles, season views, ride plans
│   ├── auth.py                 # Google OAuth login, profile setup
│   ├── signup.py               # Rider signup for specific rides
│   ├── admin.py                # Admin dashboard, ride management
│   ├── strava.py               # Strava OAuth callback, ride analysis
│   └── cron.py                 # Background jobs (sync, backfill, maintenance)
├── services/                   # Business logic layer
│   ├── __init__.py
│   ├── fitness.py              # Fitness score calculation (0-100)
│   ├── strava.py               # Strava API token management
│   ├── strava_analysis.py      # Per-ride analysis and grading
│   ├── rusa.py                 # RUSA event web scraping
│   ├── rwgps.py                # RideWithGPS route data
│   ├── eddington.py            # Eddington number calculation
│   ├── openai_coach.py         # AI training advice generation
│   └── custom_plan_service.py  # Custom ride plan operations
├── templates/                  # Jinja2 HTML templates
│   ├── base.html               # Base layout (navigation, user context)
│   ├── index.html              # Home page with season stats
│   ├── about.html              # Team information
│   ├── resources.html          # Learning resources
│   ├── login.html              # Google OAuth login page
│   ├── signup.html             # New user signup page
│   ├── riders.html             # Rider list for season
│   ├── rider_profile.html      # Individual rider profile
│   ├── rider_edit.html         # Edit rider profile
│   ├── my_profile.html         # Current user's profile
│   ├── my_strava_analysis.html # User's fitness analysis
│   ├── ride_plans.html         # List of ride plans
│   ├── ride_plan_detail.html   # Single ride plan with stops
│   ├── ride_plan_compare.html  # Compare multiple plans
│   ├── custom_ride_plan.html   # User's custom plan editor
│   ├── upcoming_brevets.html   # Upcoming RUSA events
│   ├── upcoming.html           # Upcoming Team Asha rides
│   ├── strava_ride_analysis.html # Analysis of single ride
│   ├── admin/                  # Admin-only templates
│   │   ├── login.html          # Admin password login
│   │   ├── dashboard.html      # Admin overview
│   │   ├── add_ride.html       # Add new ride
│   │   ├── mark_status.html    # Mark rider status for ride
│   │   ├── generate_plan.html  # Generate ride plan from RUSA
│   │   ├── generate_plan_preview.html # Plan preview before commit
│   │   └── strava_status.html  # View Strava sync status
│   └── setup_profile.html      # New user profile setup
├── static/                     # Static assets
│   ├── input.css               # Tailwind CSS input
│   ├── output.css              # Generated/minified Tailwind CSS
│   ├── img/                    # Images (logos, icons)
│   └── riders/                 # User profile photo uploads
├── schema/                     # Database schema definitions
│   └── (SQL migration files)
├── migrations/                 # Alembic or manual migration history
│   └── (version-numbered migration files)
├── scripts/                    # Utility scripts
│   ├── (Python scripts for maintenance tasks)
├── utils/                      # Utility modules
│   ├── __init__.py
│   └── rusa_validator.py       # RUSA ID validation and lookup
├── docs/                       # Documentation
│   └── (Markdown files for guides)
├── .github/workflows/          # GitHub Actions CI/CD
│   └── (YAML workflow files)
├── .planning/                  # GSD planning documents (this analysis)
│   └── codebase/
│       ├── ARCHITECTURE.md
│       └── STRUCTURE.md
├── package.json                # Node.js dependencies (Tailwind CSS only)
├── requirements.txt            # Python dependencies
├── CLAUDE.md                   # Claude Code guidelines and tech notes
├── PERFORMANCE_SUMMARY.md      # Performance metrics and optimizations
├── README.md                   # Project overview
├── .env                        # Environment variables (secrets, not committed)
├── .env.example                # Example environment variables
├── .gitignore                  # Git ignore rules
├── vercel.json                 # Vercel deployment configuration
├── tailwind.config.js          # Tailwind CSS configuration
├── run_dev.sh                  # Development run script
└── venv/                       # Python virtual environment (not committed)
```

## Directory Purposes

**`routes/`:**
- Purpose: HTTP endpoint definitions organized by feature
- Contains: Flask blueprints with request handlers
- Key files:
  - `main.py`: Statistics and informational pages
  - `riders.py`: Rider-focused routes (profiles, seasons, plans, analysis)
  - `auth.py`: User authentication (Google OAuth, profile setup)
  - `admin.py`: Admin-only routes (rides, plans, settings)
  - `strava.py`: Strava integration callback and analysis
  - `cron.py`: Scheduled background jobs (auth required)

**`services/`:**
- Purpose: Business logic and domain calculations
- Contains: Stateless service functions for computation and external API integration
- Key files:
  - `fitness.py`: Fitness score algorithm (4-component calculation)
  - `strava.py`: Token management and API communication
  - `strava_analysis.py`: Per-ride metrics and grading
  - `rusa.py`: Web scraping RUSA event calendar
  - `eddington.py`: Eddington number algorithm
  - `openai_coach.py`: Optional AI coaching via OpenAI API

**`templates/`:**
- Purpose: Jinja2 HTML templates for rendering responses
- Contains: HTML with inline styles and Tailwind CSS classes
- Pattern: Inheritance from `base.html`; context variables passed from routes
- Key:
  - `base.html`: Navigation, session info, global context (seasons, current user)
  - Feature-specific: One template per primary route (riders, plans, profile, etc.)
  - Admin-specific: Separated into `admin/` subdirectory

**`static/`:**
- Purpose: Static files (CSS, images)
- Contains:
  - `input.css`: Tailwind CSS input (utilities and component definitions)
  - `output.css`: Minified Tailwind output (generated by build)
  - `img/`: Logos, team icons
  - `riders/`: User-uploaded profile photos

**`schema/` and `migrations/`:**
- Purpose: Database schema and version control
- Pattern: Schema defines current table structure; migrations are historical records
- Key:
  - `schema/`: SQL CREATE TABLE statements
  - `migrations/`: Alembic or manual version-numbered migration files

**`utils/`:**
- Purpose: Shared utility functions
- Contains: RUSA ID validation, helper functions
- Key: `rusa_validator.py` for validating RUSA member IDs and fetching member info

**`docs/`:**
- Purpose: Documentation (guides, setup instructions)
- Contains: Markdown files for user and developer documentation

**`.github/workflows/`:**
- Purpose: GitHub Actions CI/CD definitions
- Contains: YAML files for automated testing, building, or scheduled jobs (e.g., cron trigger)

## Key File Locations

**Entry Points:**
- `app.py`: Flask app factory; defines `create_app()` and instantiates `app`
- `api/index.py`: Vercel serverless function entry point; imports and runs Flask app
- `routes/main.py`: Home page route (`/`)

**Configuration:**
- `config.py`: Environment-based configuration; loads from env vars
- `.env`: Runtime secrets (DATABASE_URL, API keys, secrets)
- `vercel.json`: Vercel deployment config (serverless function settings)

**Core Logic:**
- `models.py`: All SQL queries (1 file, ~89KB); organized by domain entity
- `services/fitness.py`: Fitness score calculation (0-100 scale)
- `services/strava.py`: Strava API token and activity fetching
- `services/rusa.py`: RUSA event scraping
- `routes/cron.py`: Background job orchestration

**Testing:**
- Not detected: No test files found; tests likely in separate location or not yet implemented

## Naming Conventions

**Files:**
- Route blueprints: `snake_case.py` (e.g., `auth.py`, `admin.py`)
- Service modules: `snake_case.py` (e.g., `fitness.py`, `strava.py`)
- Template files: `snake_case.html` (e.g., `rider_profile.html`, `ride_plans.html`)
- Admin templates: `snake_case.html` in `admin/` subdirectory

**Directories:**
- Feature routes: `routes/`
- Business logic: `services/`
- Presentation: `templates/`
- Static files: `static/`
- Utilities: `utils/`
- Migrations: `migrations/`

**Python Functions (in models.py, routes, services):**
- Query functions: `get_*` (e.g., `get_rider_by_rusa()`, `get_season_stats()`)
- Computation functions: `calculate_*` or `compute_*` (e.g., `calculate_fitness_score()`)
- Setter functions: `update_*` or `create_*` (e.g., `update_rider_profile()`)
- Detector functions: `detect_*` (e.g., `detect_sr_for_rider_season()`)
- Helper functions: `_prefix_private()` for private helpers

**Template Variables (Jinja2):**
- camelCase for data objects (e.g., `riderProfile`, `ridePlan`)
- UPPERCASE for constants (e.g., `CACHE_TIMEOUT`)
- Context helpers injected via `@app.context_processor` (e.g., `seasons`, `current_season`, `user_logged_in`)

## Where to Add New Code

**New Feature (e.g., New Page):**
- Primary code: Add route in appropriate file in `routes/` (create new file if feature is large)
- Template: Add HTML file in `templates/` or `templates/admin/` if admin-only
- Service logic: Extract domain logic to `services/` if shared with other routes
- Tests: Would go in test directory (not yet established; see CONCERNS.md)

**New Route/Endpoint:**
- Location: Add function to appropriate blueprint in `routes/` (group by feature, not size)
- Pattern: Use `@bp.route()` decorator; call `@cache.cached()` if read-only
- Example: `/api/new-endpoint` → Create function in `routes/cron.py` (for API), or `routes/main.py` (for UI)

**New Calculation/Algorithm:**
- Location: Create module in `services/` (e.g., `services/my_algorithm.py`)
- Pattern: Stateless functions; take required data as parameters
- Example: New fitness metric → Add to `services/fitness.py` or create `services/fitness_advanced.py`

**Utilities & Helpers:**
- Shared utilities: `utils/` directory (e.g., validation, formatting)
- Template filters: Define in `app.py` via `@app.template_filter()`
- Model helpers: Add to `models.py` if accessing database; otherwise to `utils/`

**Database Changes:**
- Schema updates: Add new migration file to `migrations/` (numbered sequentially)
- Model queries: Add query function to appropriate section in `models.py` (organized by entity)
- Example: New rider field → Create migration, add getter to `models.py` under "RIDERS" section

## Special Directories

**`.planning/`:**
- Purpose: GSD (Goal-Seeking Delivery) planning documents
- Generated: Yes (created by analysis tool)
- Committed: Yes (tracked in git for reference)
- Contains: ARCHITECTURE.md, STRUCTURE.md, and other analysis docs

**`venv/`:**
- Purpose: Python virtual environment
- Generated: Yes (created by `python -m venv venv`)
- Committed: No (in .gitignore)

**`node_modules/`:**
- Purpose: Node.js dependencies (Tailwind CSS build tool)
- Generated: Yes (created by `npm install`)
- Committed: No (in .gitignore)

**`.github/workflows/`:**
- Purpose: GitHub Actions automation
- Generated: No (manually created)
- Committed: Yes (workflow definitions)

---

*Structure analysis: 2026-03-14*
