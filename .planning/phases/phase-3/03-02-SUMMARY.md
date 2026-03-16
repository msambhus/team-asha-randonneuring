---
phase: 03-agentic-pipeline
plan: 02
subsystem: tool-registry
tags: [sql, allowlist, parameterized-queries, timeout, psycopg2, security]

# Dependency graph
requires: []
provides:
  - "ALLOWED_QUERIES dict with 10 named SQL queries (7 original + 3 added)"
  - "execute_allowed_query() with 5s SET LOCAL statement_timeout and 50-row fetchmany cap"
  - "execute_web_search() using OpenAI Responses API with web_search tool"
  - "validate_sql_safety() secondary defense via sqlparse"
affects: [03-03-PLAN]

# Tech tracking
tech-stack:
  added: []
  patterns: ["SET LOCAL statement_timeout for per-query PostgreSQL timeout", "RealDictCursor for dict-style result rows", "fetchmany(50) row cap", "Belt-and-suspenders SQL validation"]

key-files:
  created: []
  modified:
    - services/chat_tools.py
    - tests/test_chat_tools.py

key-decisions:
  - "Direct get_db() + cursor instead of models._execute() — needed for SET LOCAL in same transaction"
  - "10 queries instead of 7 — added get_team_leaderboard, get_eddington_scores, get_my_eddington"
  - "execute_web_search() added for bike/gear spec questions using OpenAI Responses API web_search tool"
  - "Timeout errors return clean error dicts, never raise exceptions"
  - "rr.status = 'FINISHED' as string value per CLAUDE.md convention"
---

# Plan 03-02 Summary: Tool Registry

## What was built
- `ALLOWED_QUERIES` dict populated with 10 named parameterized SQL queries:
  - User-scoped (rider_id): fitness_score, brevet_history, upcoming_rides, career_stats, recent_activities, get_my_eddington
  - Team-scoped (no params): get_team_stats, get_team_leaderboard, get_eddington_scores
  - Route-scoped (slug/name): get_ride_plan (2 params for ILIKE matching)
- `execute_allowed_query()` upgraded with SET LOCAL statement_timeout='5000', fetchmany(50), RealDictCursor
- `execute_web_search()` for external bike/gear information via OpenAI Responses API

## Tests added (in test_chat_tools.py)
- 12 tests covering: query population, safety validation, parameter presence, timeout enforcement, error handling, row cap, cursor type, result format

## Requirements satisfied
- AGENT-03: SQL allowlist enforced
- AGENT-04: User-scoped parameterized queries
- AGENT-05: Team-scoped queries
- AGENT-06: Ride plan queries with dual ILIKE params
- AGENT-08: 5s timeout and 50-row cap
