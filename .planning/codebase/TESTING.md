# Testing Patterns

**Analysis Date:** 2026-03-14

## Test Framework

**Status:** Not detected

**Analysis:**
- No `pytest.ini`, `setup.py`, `tox.ini`, or `pyproject.toml` found at root level
- No test configuration files (`conftest.py` or test fixtures) detected
- No test runner dependencies in `requirements.txt` (contains only Flask, Werkzeug, psycopg2, authlib, requests, beautifulsoup4, python-dotenv, openai, Flask-Caching)
- No test files (`*.test.py` or `*.spec.py`) found in codebase
- No `tests/`, `test/`, or `__tests__/` directory

**Implication:**
- Project currently has **zero automated tests**
- All validation is manual (developer testing against live database)
- Risk: No regression detection, difficult refactoring safety

## Test File Organization

**Location:** Not applicable (no test files)

**Recommended Pattern (when tests are added):**
- Co-locate tests with source code: `routes/test_signup.py` next to `routes/signup.py`
- Alternatively: `tests/routes/test_signup.py` for centralized test structure
- Services tests: `tests/services/test_fitness.py` for `services/fitness.py`

## Codebase Testability Analysis

### Highly Testable Components

**services/fitness.py** - Pure functions ideal for unit testing:
```python
def calculate_fitness_score(activities):
    """Calculate fitness score (0-100) from recent activities.

    Takes list of activity dicts, returns dict with score breakdown.
    No database dependencies.
    """
```

**utils/rusa_validator.py** - Self-contained validation logic:
```python
def validate_rusa_id(rusa_id, first_name, last_name):
    """Validate RUSA ID by scraping RUSA website."""
    # Can mock requests.get() for testing
```

**models.py functions** - Database access layer (testable with mocked database):
```python
def calculate_per_ride_score(activity, previous_activities):
    # Pure logic, no I/O except parameter inspection
```

### Moderately Testable Components

**routes/** - Flask blueprint routes (testable with Flask test client):
- Need Flask app context and session management
- Can mock database calls in `models` module
- Example: `routes/signup.py` API endpoints return JSON suitable for assertions

**services/strava.py** - API integration (testable with mocked HTTP):
- Makes HTTP calls to Strava API
- Can mock `requests` module or use responses library

### Difficult to Test (High Coupling)

**routes/riders.py** - Complex route with many dependencies:
- 400+ lines mixing data access, business logic, and rendering
- Multiple database queries with conditional logic
- Template rendering tightly coupled

**routes/main.py** - Error handling with fallback mock data:
- Catches broad exceptions, returns mock data
- Difficult to verify behavior in error cases

**services/openai_coach.py** - External API dependency:
- Depends on OpenAI API (optional fallback exists)
- Would need API mocking for reliable tests

## Recommended Test Strategy

### Phase 1: Utility Functions (Lowest Risk)

**Start with:** `tests/utils/test_rusa_validator.py`
```python
# Would test:
# - normalize_last_name() with edge cases (McDonald, O'Brien, MacKenzie)
# - validate_rusa_id() with mocked requests
# - get_rusa_info() with valid/invalid IDs
```

**Framework:** `pytest` with `pytest-mock` for mocking HTTP requests

### Phase 2: Service Functions (Business Logic)

**Target:** `tests/services/test_fitness.py`
```python
# Would test:
# - calculate_fitness_score() with various activity lists
# - _parse_dt() with different datetime formats
# - calculate_per_ride_score() with edge cases
# - score_all_activities() batch processing
```

**Test data:** Fixture with sample Strava activity dicts

### Phase 3: Route Integration Tests (Flask Routes)

**Target:** `tests/routes/test_signup.py`
```python
# Would test:
# - @signup_bp.route('/api/<ride_id>/signup', methods=['POST'])
# - @signup_bp.route('/api/<ride_id>/interested', methods=['POST'])
# - Error responses (401 unauthorized, 400 bad request)
```

**Setup:** Flask test client with in-memory database or mocked models

### Phase 4: Model/Database Access Tests

**Target:** `tests/test_models.py`
```python
# Would test:
# - get_season_stats() with test data
# - RideStatus enum normalization
# - Query parameter handling
```

**Setup:** Test database or mocked psycopg2

## Code Coverage Gaps (High Priority)

**Critical paths with no tests:**

1. **Authentication & Session Management** (`routes/auth.py`):
   - Google OAuth callback handling
   - Session creation and user lookup
   - Profile completion validation
   - Risk: Silent auth failures, session leaks

2. **Signup State Transitions** (`routes/signup.py` + `models.py`):
   - INTERESTED → MAYBE → GOING transitions
   - Withdrawal logic (GOING → WITHDRAW)
   - Post-ride status changes (FINISHED, DNF, DNS, OTL)
   - Risk: Invalid state transitions allowed, data corruption

3. **Cache Invalidation** (`cache.py`, all routes):
   - `cache.clear()` called after every write
   - No verification that stale data is not served
   - Risk: Users see outdated signup counts, season stats

4. **Error Fallbacks** (routes/main.py, auth.py):
   - Database unavailable fallback to mock data
   - No verification mock data is actually returned
   - Risk: Silent failures, user confusion

5. **Fitness Score Calculation** (`services/fitness.py`):
   - Edge cases: No activities, missing data fields, timezone handling
   - Per-ride grading logic
   - Risk: Incorrect fitness assessments, wrong training advice

## Mocking Strategy (When Testing)

**What to Mock:**

- **Database calls:** Mock `get_db()` in `db.py` to return test connection
- **HTTP requests:** Mock `requests.get()` for Strava API, RUSA website scraping
- **Session data:** Flask test client handles session in context
- **Cache operations:** Mock `cache.memoize()` decorator to bypass caching
- **External APIs:** OpenAI API, RideWithGPS API (use responses library)

**What NOT to Mock:**

- Business logic functions (they should be pure)
- Fitness calculations (core domain logic)
- Validation rules (RideStatus enum, RUSA name normalization)

## Environment Configuration for Testing

**Current setup (observed):**
- `.env` file present (credentials not committed)
- `.env.example` shows required variables
- No test-specific `.env.test` configuration

**For tests, would need:**
```bash
# .env.test or conftest.py fixture
DATABASE_URL=sqlite:///:memory:  # or test PostgreSQL
STRAVA_CLIENT_ID=test-id
STRAVA_CLIENT_SECRET=test-secret
GOOGLE_CLIENT_ID=test-id
GOOGLE_CLIENT_SECRET=test-secret
CRON_SECRET=test-secret
FLASK_ENV=testing
```

## Current Testing Approach

**Actual practice observed:**
- Manual testing via Flask development server (`run_dev.sh`)
- Database changes tested against live PostgreSQL (Supabase)
- No automated verification of fixes or regressions
- Reliance on developer review and manual browser testing

---

*Testing analysis: 2026-03-14*
