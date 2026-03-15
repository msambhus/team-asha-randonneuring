# Codebase Concerns

**Analysis Date:** 2026-03-14

## Tech Debt

**Excessive Debug Print Statements:**
- Issue: 293 `print()` statements scattered throughout codebase for debugging, many with [DEBUG] prefixes. Should use proper logging module.
- Files: `models.py` (40+ statements), `routes/riders.py` (30+ statements), `routes/main.py`, `routes/auth.py`, `routes/admin.py`
- Impact: Clutters stderr output in production, makes it difficult to identify real errors, creates security risks if sensitive data is printed
- Fix approach: Replace all `print()` calls with `logging.debug()`, `logging.info()`, or `logging.error()` calls. Set up centralized logger configuration in `config.py`. Use log levels instead of [DEBUG] prefixes.

**Hardcoded Admin User Check:**
- Issue: Admin authorization hardcoded to specific first names (`['sriharsha', 'venkatesh', 'mihir']`) duplicated across 3+ locations in `routes/riders.py`
- Files: `routes/riders.py` lines 19, 426, 495 (and similar patterns)
- Impact: Adding/removing admins requires code changes and deployments. Difficult to track who has admin access. No audit trail.
- Fix approach: Create `admin_riders` table with `rider_id` and `is_admin` boolean. Check this table instead of hardcoded names. Centralize admin check in single function.

**Monolithic Route File:**
- Issue: `routes/riders.py` is 2,393 lines - handles season view, rider profiles, custom plans, admin edits, API endpoints. Mixes business logic with route handlers.
- Files: `routes/riders.py`
- Impact: Hard to navigate, difficult to test in isolation, increased merge conflict risk, cognitive overload for maintainers
- Fix approach: Split into `routes/riders_public.py` (profiles, season leaderboard), `routes/riders_profile.py` (user profile view/edit), `routes/custom_plans.py` (custom plan CRUD), `routes/admin_rides.py` (admin edit endpoints)

**Large Monolithic Models File:**
- Issue: `models.py` is 2,343 lines, contains all data access code mixed with business logic (SR detection, Eddington calculation logic calls within model layer)
- Files: `models.py`
- Impact: Difficult to test models in isolation, hard to understand query patterns, cache invalidation logic spread throughout
- Fix approach: Extract data layer functions into separate `models/data.py` (pure SQL queries), keep business logic in `services/` or `models/business.py`

**Cache Invalidation Fragility:**
- Issue: `_clear_custom_plan_cache()` in `models.py` manually clears specific cache keys. Cache invalidation happens throughout code at different points.
- Files: `models.py` lines 1686-1706, multiple locations calling `cache.clear()` and manual key deletion
- Impact: Easy to miss clearing a cache key, causing stale data to be served. No centralized cache invalidation strategy.
- Fix approach: Use cache key generation helper consistently, implement cache invalidation decorator for functions that modify data, centralize cache clear logic

## Known Bugs

**Cache Clearing on Production Environment:**
- Symptoms: Using `cache.clear()` in `app.py` line 71 clears entire in-memory cache on every exception. In serverless (Vercel), each instance has separate cache, but clearing all can impact other concurrent requests.
- Files: `app.py` context_processor lines 62-83, `models.py` line 1706, `routes/riders.py` multiple locations
- Trigger: Any database connection failure during context processor initialization; any custom plan update
- Workaround: Restart the application to rebuild cache
- Fix approach: Implement selective cache invalidation using cache tags or namespaced keys instead of full clear

**Debug Print to Stderr with Binary Data:**
- Symptoms: `routes/riders.py` line 1963 prints request data directly which may contain binary or JSON data that breaks terminal output
- Files: `routes/riders.py` lines 1963, 2003-2005
- Trigger: Any custom plan stop update request with special characters or binary data
- Workaround: Manually log to file instead
- Fix approach: Use logging module with proper serialization, escape binary data before printing

## Security Considerations

**Weak Default Secrets:**
- Risk: `config.py` line 6 has `SECRET_KEY` default of `'dev-key-change-in-prod'` and line 7 has `ADMIN_PASSWORD` default of `'asha2026'`
- Files: `config.py` lines 6-7
- Current mitigation: Environment variables override defaults; .env file in .gitignore
- Recommendations:
  - Remove all default values (require env vars to be set)
  - Add startup validation to fail if critical secrets are missing
  - Rotate ADMIN_PASSWORD (appears to be used for admin login route)
  - Document which environment variables are required

**Debug Mode on Production:**
- Risk: `app.py` line 91 runs `app.run(debug=True)` unconditionally. While overridden by Flask environment, this could be accidentally enabled.
- Files: `app.py` line 91
- Current mitigation: Deployment platform (Vercel) overrides this with production settings
- Recommendations: Check `os.environ.get('FLASK_ENV')` and only enable debug locally

**Weak Authorization Check:**
- Risk: `auth.py` decorator `user_login_required` skips auth entirely when `current_app.debug` is True (line 19-20)
- Files: `auth.py` lines 19-20
- Current mitigation: Only applies in development (`debug=True`)
- Recommendations:
  - Use a separate `DEBUG_MODE` environment variable instead of Flask's debug flag
  - Add explicit list of whitelisted local IPs instead of blanket skip
  - Log when auth is skipped for audit trail

**Admin Check Hardcoded to Database Names:**
- Risk: `routes/riders.py` checks `first_name` field directly against hardcoded list. Typos in database could bypass auth.
- Files: `routes/riders.py` lines 19, 426, 495
- Current mitigation: Only 3 riders, unlikely to have typos
- Recommendations: Create dedicated `is_admin` column in database, add UNIQUE constraint on first_name, use case-insensitive comparison safely

**Linear API Credentials Hardcoded (Team ID and Label IDs):**
- Risk: UUIDs in `config.py` lines 18-20 are hardcoded but treated as public constants
- Files: `config.py` lines 18-20
- Current mitigation: UUIDs are somewhat opaque, not critical credentials
- Recommendations: Consider moving to database or secrets manager if this becomes a multi-team product

## Performance Bottlenecks

**Unoptimized Database Queries - N+1 Problem:**
- Problem: Multiple functions fetch data in loops without batch queries (documented in `PERFORMANCE_SUMMARY.md`)
- Files: `models.py`, `routes/riders.py` (season view, rider profiles)
- Cause: Individual queries for each rider's stats instead of batching
- Impact: 600-800ms for season leaderboard and rider profile pages
- Improvement path:
  - Already has batch functions: `get_all_rider_season_stats()`, `detect_sr_for_all_riders_in_season()`, `get_signup_counts_batch()`
  - Apply migration 006 (add composite indexes) - 60% improvement
  - Use batch functions exclusively instead of per-rider queries
  - Expected result: 250-300ms page load (58-62% improvement)

**Missing Database Indexes:**
- Problem: Queries on `ride(season_id, date)`, `rider_ride(rider_id, status)`, `rider_ride(ride_id, status)` lack composite indexes
- Files: Database schema (not in code)
- Cause: Initial schema was simple, not optimized for current query patterns
- Impact: Full table scans on medium-sized tables (1000+ rides)
- Improvement path: Apply `migrations/006_add_composite_indexes.sql` - creates 8 composite indexes. Safe to run with `CREATE INDEX CONCURRENTLY`.

**In-Memory Cache Only (Vercel Serverless):**
- Problem: Using `SimpleCache` (in-memory) means each Vercel instance has separate cache. With multiple serverless instances, cache is not shared.
- Files: `cache.py` line 13
- Cause: Vercel serverless doesn't have persistent storage between instances
- Impact: Cache hits only work within a single instance; high cache miss rate at scale
- Improvement path: Migrate to Redis cache (e.g., Redis Cloud on Vercel) or implement database-backed caching for critical queries

**Cache Timeout Too Short (5 minutes):**
- Problem: `cache.py` line 5 sets `CACHE_TIMEOUT = 300` (5 minutes)
- Files: `cache.py`
- Cause: Conservative timeout to avoid stale data
- Impact: Frequent cache misses, increased database load (one of the 60 queries per season page)
- Improvement path:
  - Season/ride data (rarely changes): 24 hours
  - Rider stats (daily cron updates): 2-4 hours
  - User-specific data: 15 minutes
  - Implement selective cache timeouts based on data type

## Fragile Areas

**Custom Plan Stop Update Logic:**
- Files: `models.py` lines 1708-1901, `routes/riders.py` lines 1977-2021
- Why fragile: Complex multi-case logic (3 different update paths: existing custom stop, override existing base stop, new override). Lots of duplicated code (same field update list repeated in each branch).
- Safe modification:
  - Add comprehensive test coverage for all 3 update cases
  - Extract duplicated update logic into helper function
  - Add validation for explicit_fields parameter
  - Document the 3 cases with examples
- Test coverage: Low - no unit tests for this function visible

**Cache Clearing with Cache.clear():**
- Files: `app.py` line 71, `cache.py` line 31, multiple locations in models.py
- Why fragile: `cache.clear()` is too broad - clears all cached data globally. If called during request processing, affects other concurrent requests (especially in serverless).
- Safe modification:
  - Never call `cache.clear()` - use selective key deletion instead
  - Use `cache.delete_memoized()` for specific functions
  - Use `cache.cache.delete(key)` for specific keys
  - Consider caching decorators with automatic invalidation
- Test coverage: Integration tests needed for cache invalidation

**Authorization Logic Across Three Files:**
- Files: `routes/riders.py` (is_admin_user), `auth.py` (login_required, user_login_required, profile_required), `routes/admin.py` (admin route)
- Why fragile: Authorization checks scattered across multiple files with no centralized policy. `is_admin_user()` is defined in routes but used for both read and write operations with unclear scope.
- Safe modification:
  - Create `auth/decorators.py` with all auth decorators
  - Create `auth/policies.py` with authorization rules
  - Document which endpoints require which permissions
  - Add role-based access control (RBAC) if expanding admin functionality
- Test coverage: Auth tests limited to admin login route

**Error Handling in API Routes:**
- Files: `routes/riders.py` lines 1950-1951, 2007-2021 (generic `except Exception`)
- Why fragile: 70 broad `except Exception` clauses swallow all errors and return generic 500 messages. Difficult to diagnose production issues. No logging of full traceback.
- Safe modification:
  - Catch specific exceptions (ValueError, KeyError, psycopg2.Error)
  - Log full traceback using logging module (not print)
  - Return meaningful error messages for client (not internal errors)
  - Add structured logging with request context
- Test coverage: Error cases not systematically tested

## Scaling Limits

**Vercel Serverless Cold Start Overhead:**
- Current capacity: Can handle burst traffic within Vercel's limits
- Limit: Each cold start takes 1-2 seconds (Flask import time). Supabase connection pool requires new connection per instance.
- Scaling path:
  - Keep database connections pooled (use PgBouncer)
  - Warm up instances with periodic cron jobs
  - Consider switching to serverless-native framework (Next.js API Routes, Node.js)
  - Monitor cold start metrics in Vercel dashboard

**Single In-Memory Cache Instance per Vercel Instance:**
- Current capacity: ~100MB in-memory cache per instance, suitable for 5-10 concurrent requests
- Limit: Multiple serverless instances don't share cache; N instances = N separate caches
- Scaling path: Migrate to Redis (shared cache across instances) or accept higher cache miss rate with database optimization

**Database Connection Per Request (Serverless):**
- Current capacity: Supabase free tier allows ~100 connections
- Limit: Each Vercel instance opens new connection per request (no persistent connection pool)
- Scaling path:
  - Implement PgBouncer connection pooling in front of Supabase
  - Use Supabase connection pooler (PgBouncer mode)
  - Limit concurrent requests per Vercel instance

**Strava API Rate Limiting Not Handled:**
- Current capacity: Strava allows 600 requests per 15 minutes per app
- Limit: No rate limit detection or backoff strategy in `services/strava.py` or sync logic
- Scaling path:
  - Implement exponential backoff on 429 (Too Many Requests)
  - Add request throttling in `routes/strava.py`
  - Cache Strava activity data longer (currently 1-year window fetched on every request)

## Dependencies at Risk

**Stale Flask Version:**
- Risk: `package.json` has minimal Python dependencies in `requirements.txt` (not visible but referenced). Flask versions matter for security patches.
- Impact: Missing security patches, incompatibility with third-party libraries
- Migration plan:
  - Add `requirements.txt` to git (currently has `package.json` but not clear Python version pins)
  - Use `pip freeze > requirements-lock.txt` for production
  - Set up Dependabot for automated version updates

**Hardcoded Database Connection (No Connection Pooling):**
- Risk: `db.py` line 8 creates new psycopg2 connection per request. In serverless, connections are expensive.
- Impact: Slow cold starts, connection pool exhaustion at scale
- Migration plan:
  - Implement pgBouncer or use Supabase connection pooling (PgBouncer mode)
  - Use `psycopg2.pool.SimpleConnectionPool` to reuse connections within instance
  - Document connection pool sizing

**OAuth Dependency on External Services:**
- Risk: Google OAuth (`routes/auth.py`) and Strava OAuth (`routes/strava.py`) have external dependencies. If providers are down, users can't authenticate or sync.
- Impact: Complete outage if Google/Strava OAuth is unavailable
- Migration plan:
  - Implement local session/token storage as fallback
  - Add graceful degradation (allow existing users to view cached data)
  - Monitor OAuth service status

## Missing Critical Features

**No Logging Infrastructure:**
- Problem: 293 print statements are only logging. No structured logging, no log aggregation, no audit trail.
- Blocks: Cannot diagnose production issues without access to stderr
- Recommendation: Add centralized logging with `logging` module, output to file or service (Vercel logging, Sentry, etc.)

**No Database Migrations Tool:**
- Problem: Manual migration scripts in `migrations/` folder. No rollback mechanism. No way to track which migrations have been applied.
- Blocks: Hard to deploy new database schema changes safely
- Recommendation: Use Alembic (SQLAlchemy) or Flyway for version-controlled migrations with rollback

**No Input Validation Framework:**
- Problem: Routes accept request data without validation. `update_custom_plan_stop()` has ad-hoc explicit_fields parameter.
- Blocks: No protection against invalid data types, missing required fields, or injection attacks
- Recommendation: Use Marshmallow or Pydantic for request validation and serialization

**No Rate Limiting on API Endpoints:**
- Problem: No rate limiting on custom plan edits, signup endpoints, or user edits
- Blocks: Vulnerable to abuse (e.g., spam updating a custom plan thousands of times)
- Recommendation: Use Flask-Limiter to add rate limits to API routes

**No Database Query Monitoring:**
- Problem: Cannot see which queries are slow in production (no query logging, no APM)
- Blocks: Performance regressions go unnoticed until users report slowness
- Recommendation: Use Supabase query logs + pgBadger, or add application-level query timing

## Test Coverage Gaps

**No Unit Tests for Models Layer:**
- What's not tested: `models.py` functions (SR detection, Eddington calculation, custom plan logic)
- Files: `models.py` - all 2,343 lines
- Risk: Refactoring could break complex business logic without detection
- Priority: High (business logic is core to application)

**No Integration Tests for Route Handlers:**
- What's not tested: Rider profile edit, custom plan CRUD, signup flow
- Files: `routes/riders.py`, `routes/signup.py`
- Risk: API contracts could break without detection
- Priority: High (routes are public-facing)

**No Tests for Cache Invalidation:**
- What's not tested: Cache clearing behavior, stale data scenarios
- Files: `cache.py`, `models.py` _clear_custom_plan_cache
- Risk: Stale data could be served without detection
- Priority: Medium (hard to test in CI, but important for data integrity)

**No Tests for Authorization Checks:**
- What's not tested: Admin user detection, profile_required decorator, user_login_required with debug mode
- Files: `auth.py`, `routes/riders.py` (is_admin_user)
- Risk: Unauthorized access could occur without detection
- Priority: High (security-critical)

**No Performance Regression Tests:**
- What's not tested: Page load times, query counts, cache effectiveness
- Files: All routes
- Risk: Performance regressions introduced unknowingly (already noted in PERFORMANCE_SUMMARY.md)
- Priority: Medium (can use Lighthouse or Vercel Analytics for some metrics)

**No Tests for Error Handling:**
- What's not tested: Database connection failures, API errors from Strava/RUSA, missing data scenarios
- Files: All routes and services
- Risk: Error handling code paths are untested, could fail in production
- Priority: Medium

---

*Concerns audit: 2026-03-14*
