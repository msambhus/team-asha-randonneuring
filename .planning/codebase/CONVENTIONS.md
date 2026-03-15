# Coding Conventions

**Analysis Date:** 2026-03-14

## Naming Patterns

**Files:**
- PascalCase for modules with uppercase abbreviations (e.g., `RideStatus`, `Enum`)
- snake_case for Python module files (e.g., `models.py`, `fitness.py`, `rusa_validator.py`, `custom_plan_service.py`)
- Descriptive names indicating purpose (e.g., `auth.py` for authentication, `db.py` for database, `cache.py` for caching)

**Functions:**
- snake_case for all function names: `get_all_seasons()`, `calculate_fitness_score()`, `validate_rusa_id()`, `mark_interested()`
- Verb-first pattern for functions that perform actions: `mark_interested()`, `remove_signup()`, `update_rider_profile()`
- Getter pattern `get_*()` for retrieving data: `get_rider_by_rusa()`, `get_season_stats()`, `get_signups_for_ride()`
- Helper functions prefixed with underscore: `_execute()`, `_parse_dt()`, `_grade_from_score()`
- Longer names for complex functions to be descriptive: `detect_sr_for_all_riders_in_season()`, `recalculate_cumulative_values()`

**Variables:**
- snake_case for all variables: `season_id`, `rider_id`, `total_distance_km`, `avg_rides_per_week`
- Abbreviated names for common patterns: `cur` for cursor, `conn` for database connection, `e` for exceptions
- Plural names for collections: `riders`, `events`, `weeks`, `matches`
- Single-letter for loop indices: `r` in `for r in rides`, `s` in `for s in seasons`
- Scoped names for context variables: `is_current`, `is_team_ride`, `past_only` for boolean flags

**Types/Classes:**
- PascalCase for classes: `RideStatus`, `Enum`
- All caps for enum values: `INTERESTED`, `FINISHED`, `DNF`, `OTL`

## Code Style

**Formatting:**
- No formatter enforced (no Prettier, Black, or autopep8 configuration found)
- 2-4 space indentation used (Python standard 4 spaces observed in codebase)
- Line length follows Python conventions (appears to be ~100 characters based on readable patterns)

**Linting:**
- No linter configuration detected (no `.flake8`, `pylint.ini`, or `ruff.toml`)
- Code assumes manual style compliance

**Comments:**
- Single-line comments with `#` for inline explanations: `# If already logged in, redirect to home`
- Comment markers for section separation: `# ========== SEASONS ==========`, `# ========== RIDERS ==========`

## Import Organization

**Order:**
1. Standard library (datetime, enum, re, math, requests)
2. Third-party packages (psycopg2, flask, authlib, beautifulsoup4, requests)
3. Local imports (db, cache, models, config, auth, utils)

**Pattern from models.py:**
```python
from datetime import datetime, date
from enum import Enum
import psycopg2.extras
from db import get_db
from cache import cache, CACHE_TIMEOUT
```

**Pattern from routes/main.py:**
```python
import requests as http_requests
from flask import Blueprint, render_template, request, jsonify, current_app
from models import (get_all_time_stats, get_all_seasons, get_current_season,
                    get_season_stats, get_upcoming_rusa_events, get_upcoming_rides)
from cache import cache, CACHE_TIMEOUT
```

**Path Aliases:**
- No explicit path aliases configured (relative imports used throughout)
- Absolute imports preferred: `from models import`, `from auth import`, `from services.fitness import`

## Error Handling

**Patterns:**
- Try-except blocks for database operations and external API calls: `try: response.requests.get(...); except requests.RequestException`
- Broad exception handling with fallback mock data in routes: `except Exception as e:` followed by `return render_template(..., stats=mock['stats'], ...)`
- Flash messages for user-facing errors: `flash('Please log in to access this page', 'warning')`
- HTTP status codes for API responses: 401 for unauthorized, 400 for bad request, 500 for server error
- Return None or empty dict for graceful degradation: `return None` when activities list is empty, `return {'total': 0, ...}`
- Validation returns dict with `valid`, `error`, and data keys:
  ```python
  return {
      'valid': True,
      'rusa_name': rusa_name,
      'rusa_club': rusa_club,
      'error': None
  }
  ```

**Example from utils/rusa_validator.py:**
```python
except requests.RequestException as e:
    return {
        'valid': False,
        'error': f'Error connecting to RUSA website: {str(e)}',
        'rusa_name': None,
        'rusa_club': None
    }
except Exception as e:
    return {
        'valid': False,
        'error': f'Validation error: {str(e)}',
        'rusa_name': None,
        'rusa_club': None
    }
```

## Logging

**Framework:** `print()` for debug output

**Patterns:**
- Simple print statements for debugging: `print(f"Database not available, using mock data: {e}")`
- No structured logging library configured
- Error messages printed directly before fallback behavior

## Docstrings

**Module-level:**
- All modules have docstrings describing purpose:
  - `app.py`: `"""Flask app factory for Team Asha Randonneuring."""`
  - `models.py`: `"""Data access layer — all SQL queries live here (PostgreSQL via psycopg2)."""`
  - `fitness.py`: Multi-line describing score calculation algorithm

**Function-level:**
- Docstrings for public functions explaining purpose, arguments, and return values
- Short single-line docstrings for simple getters: `"""Get rider by RUSA ID. NOT CACHED - rider data should not be cached in serverless environments."""`
- Detailed multi-line docstrings for complex functions with Args, Returns sections:
  ```python
  def calculate_fitness_score(activities):
      """Calculate fitness score (0-100) from recent activities.

      Args:
          activities: list of strava_activity dicts (last 28 days)

      Returns:
          dict with total, frequency, volume, intensity, recency scores
          or None if no activities
      """
  ```
- Docstrings for API endpoints describing purpose: `"""API endpoint to mark current user as interested in a ride. Allows status changes."""`

## Function Design

**Size:**
- Small focused functions for data access: 3-10 lines typical for getter functions
- Medium functions for business logic: 30-50 lines
- Longer functions acceptable for complex calculations (fitness.py functions are 80-150 lines with detailed logic)

**Parameters:**
- Single parameter or small number: `get_rider_by_rusa(rusa_id)`, `validate_rusa_id(rusa_id, first_name, last_name)`
- Multiple related parameters grouped logically: `calculate_per_ride_score(activity, previous_activities)`
- Optional parameters with default values: `get_season_stats(season_id, past_only=False)`

**Return Values:**
- Single values or tuples for simple operations: `fetchone()`, `fetchall()`
- Dictionaries for structured data: `{'total': 100, 'frequency': 25, 'volume': 35, 'intensity': 25, 'recency': 15}`
- None for missing data or optional results: `return None` if no activities
- Boolean for success/failure checks: `return success` in signup operations

## Module Design

**Exports:**
- No explicit `__all__` declarations observed
- All top-level functions are public (no private module-level functions prefixed with `_`)
- Helper functions prefixed with `_` are module-private: `_execute()`, `_parse_dt()`

**Barrel Files:**
- No barrel files (`__init__.py` with exports) observed
- `api/index.py` exists but is minimal (207 bytes)
- Routes registered explicitly in `app.py` via `app.register_blueprint()`

**Organization by Concern:**
- Database access layer: `models.py` (all SQL queries and data retrieval)
- Configuration: `config.py` (environment variables and Flask config)
- Authentication: `auth.py` (decorators and auth helpers), `routes/auth.py` (OAuth flow)
- Services: `services/` directory with domain-specific logic (`fitness.py`, `strava.py`, `openai_coach.py`)
- Routes: `routes/` directory organized by feature (`main.py`, `riders.py`, `signup.py`, `admin.py`)

---

*Convention analysis: 2026-03-14*
