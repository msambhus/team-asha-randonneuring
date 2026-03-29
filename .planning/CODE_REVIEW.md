# Code Review — 5-Persona Audit
**Date:** 2026-03-24
**Reviewers:** Staff Engineer · Security Engineer · Privacy/CISO · Chief Architect · AI Skeptic
**Status:** Findings only — no fixes applied yet. Direct @msambhus to fix specific items.

---

## 🔧 Staff Engineer Review

### CRITICAL
- **[routes/cron.py:437-444]** N+1 HTTP calls in wind backfill loop — each ride calls `fetch_route()` (HTTP) with no error context; exceptions truncated to 100 chars. Can't distinguish RWGPS vs Open-Meteo failures. **Fix:** Log full traceback; separate exception handling per external call.
- **[services/weather.py:394-396]** Bare `except Exception` silences API errors, returns `(None, None)` with no failure mode distinction. Route handlers can't tell "no data" from "API error". **Fix:** Use distinct return tuples or custom exceptions to indicate failure mode.
- **[routes/admin.py:237-239]** Same truncated 80-char error logging in backfill handler. No HTTP 429 detection for Open-Meteo rate limits. **Fix:** Log full traceback; check `e.response.status_code == 429` explicitly.
- **[routes/riders.py:151,2153-2161,2244-2246]** Multiple `print()` statements in production code. On Vercel these go to stderr, not captured in structured logs. **Fix:** Replace all `print()` with `current_app.logger.debug/info`.

### HIGH
- **[services/custom_plan_service.py:138-200]** Complex cumulative time calc with `accumulated_time_from_removed` (line 110) has zero test coverage for hidden stop edge cases (single, consecutive, end-of-route). **Fix:** Add tests for all three hidden-stop scenarios.
- **[services/weather.py:274-276,309-311,335-337]** No retry logic on Open-Meteo errors. Transient 5xx permanently fails the page load. **Fix:** Exponential backoff for 5xx; return cached data as fallback for forecast requests.
- **[models.py:2654-2667]** `save_ride_wind_data()` runs one INSERT per stop in a loop — 20 stops = 20 DB round trips. **Fix:** Single multi-row INSERT with all values.
- **[services/fitness.py:37-45]** ISO week key without year prefix breaks at Dec 31→Jan 1 boundary. Week 1 of two different years collide. **Fix:** Use `f"{dt.isocalendar().year}-{dt.isocalendar().week}"` as key.
- **[routes/riders.py:214]** `get_ride_plan_stops()` result used without None check. **Fix:** Add explicit guard before use.

### MEDIUM
- **[services/weather.py:526-529]** Cache key uses `datetime.now()` (second-level granularity) — cache misses every second. **Fix:** Use `datetime.now().strftime('%Y%m%d%H')` (hour-level).
- **[routes/cron.py:220-222]** No `isinstance(ride_date, date)` check before `date.fromisoformat()` — fails if already a date object. **Fix:** Add type guard.
- **[routes/riders.py:2240-2262]** `api_update_custom_stop()` has debug prints and no idempotency guard. **Fix:** Use `RETURNING` clause; remove prints.
- **[models.py:2626-2668]** `ON CONFLICT DO NOTHING` means schema additions leave stale partial rows. **Fix:** Use `DO UPDATE SET` to merge new columns.

### LOW
- **[services/weather.py:103-107]** RGB color tuples for wind types should use named constants. **Fix:** `class WindColor` or named dict.
- **[routes/admin.py:164-172]** Inline imports inside route function body. **Fix:** Move to module-level imports.
- **[services/weather.py:560]** Minutes in `start_time_str` are silently dropped. Already fixed in fetch_stop_wind but verify `_fetch_forecast_wind` too.
- **[templates/ride_plan_detail.html:904-912]** Wind arrow macro ternary chains are hard to extend. **Fix:** Jinja2 if/elif blocks or a filter.

---

## 🔐 Security Engineer Review

### CRITICAL
- **[app.py:64-82]** Debug auto-login authenticates ANY request in debug mode — grants full auth without credentials. **Fix:** Remove entirely; never ship debug auth bypass; use synthetic test fixtures instead.
- **[.env:4,13]** DATABASE_URL (Supabase password) and OpenAI API key committed to repo. **Fix:** Rotate both keys NOW; scrub git history with BFG Repo Cleaner; verify `.gitignore` is correct.
- **[routes/auth.py:36-38,103-105]** Open redirect — `next` parameter accepted from user input without validation. `auth/login?next=https://evil.com` redirects after login. **Fix:** Use `url_has_allowed_host_and_scheme()` or whitelist internal paths only.
- **[routes/admin.py:90-91]** Same open redirect in admin login `next` param. **Fix:** Same as above.

### HIGH
- **[routes/riders.py:998-1013]** Debug mode skips auth entirely on `/my/strava-analysis` — `?rider_id=123` exposes any rider's private data. **Fix:** Never skip auth in debug; use fixture riders.
- **[config.py:6-7]** `SECRET_KEY` defaults to `'dev-key-change-in-prod'`, `ADMIN_PASSWORD` defaults to `'asha2026'`. **Fix:** Raise `RuntimeError` if not set in production; enforce 16+ char random values.
- **[routes/cron.py:18]** String comparison for cron secret is timing-attack vulnerable. **Fix:** `hmac.compare_digest(auth_header, f'Bearer {expected_secret}')`.
- **[models.py:888,1312,1958,2016,2075,2272,2713,2717]** SQL built with f-strings using user-controlled column names in UPDATE/WHERE clauses. **Fix:** Whitelist allowed column names; never interpolate user-controlled identifiers.
- **[routes/riders.py:1247]** IDOR: ownership check trusts session alone. If session secret is weak, session can be forged. **Fix:** Always re-verify ownership with a DB query, don't trust session value alone.
- **[auth.py:19]** Admin check based on `first_name` — user-editable field. Anyone who sets their first name to "mihir" could become admin. **Fix:** Add `is_admin` boolean column to `app_user` or `rider` table; never derive permissions from editable profile fields.

### MEDIUM
- **[routes/admin.py:104-106]** `_require_admin()` skipped in `app.debug` mode — debug mode bypasses admin check entirely. **Fix:** Never skip security in debug.
- **[models.py:524,680,700,2464]** f-string interpolation for `date_clause` and similar dynamic SQL fragments. Currently safe but pattern is accident-prone. **Fix:** Standardize on conditional WHERE builder; no f-string SQL.
- **[app wide]** No CSRF protection — no Flask-WTF, no CSRF tokens on any form. All POST endpoints are forgeable from external sites. **Fix:** Add Flask-WTF; add `csrf_token` to all forms; use `@csrf.protect` on all POST/PUT/DELETE routes.
- **[routes/riders.py:1001-1002]** Debug fallback `SELECT rider_id FROM strava_connection LIMIT 1` is non-deterministic. **Fix:** Use hardcoded test rider ID with synthetic data.

### LOW
- **[requirements.txt]** Flask 3.0.0, Werkzeug 3.0.1, Jinja2 3.1.2 — run `pip audit` to check for CVEs. **Fix:** Update to latest stable; add `pip audit` to CI.
- **[auth.py:59]** Plain text password comparison with no rate limiting. **Fix:** Add `Flask-Limiter`; limit `/admin/login` to 5 attempts/min/IP.
- **[routes/cron.py:109]** `'429' in str(e)` is fragile. **Fix:** Check `e.response.status_code == 429`.
- **[routes/riders.py:1272-1273]** `view` param not whitelisted. **Fix:** `if view not in ('base', 'custom'): view = 'base'`.

---

## 🔒 Privacy / CISO Review

### CRITICAL (Regulatory / Data Breach Risk)
- **[routes/riders.py:1001]** Debug mode serves any rider's Strava data (HR, power, GPS) without authentication. PII exposed. **Fix:** Remove debug bypass; require auth always.
- **[routes/riders.py:792-793]** `is_own_profile = True` in debug mode — overrides all privacy checks. Any rider's private Strava is visible. **Fix:** Never override privacy controls in any mode.
- **[routes/admin.py:141 + template]** Audit `get_strava_admin_summary()` template rendering — confirm access/refresh tokens never appear in HTML/JS. **Fix:** Audit template output; ensure tokens are never passed to template context.
- **[routes/riders.py:151, services/strava.py:205-206,222]** PII logged via `print()` — Strava activity IDs, rider IDs, exception details written to Vercel function logs accessible to log viewers. **Fix:** Replace all prints with structured logging; remove PII from log messages.

### HIGH
- **[routes/auth.py:88-98]** Strava connection profile setup doesn't verify `rider_id` belongs exclusively to the registering user — malicious user could claim another rider's profile. **Fix:** Validate uniqueness of rider_id claim at registration time.
- **[services/strava.py:36-79]** Token refresh response not validated — malformed response could persist invalid tokens or crash. **Fix:** Validate all required fields before persisting; add TTL bounds (1–6 hours).
- **[routes/admin.py:461-545,548-592]** Admin can inspect/manipulate any rider's Strava data with no audit log. No role-based separation. **Fix:** Log all admin Strava operations with `who+what+when`; move to explicit `is_admin` role.
- **[routes/riders.py:1238-1256]** Privacy toggle not CSRF-protected and not rate-limited. **Fix:** Add CSRF token; rate limit; consider password confirmation.
- **[config.py:28]** Strava scope is `activity:read_all` — fetches private activities without disclosure to user. **Fix:** Document clearly in onboarding; provide delete mechanism for private activities; consider downgrading to `activity:read`.

### MEDIUM
- **[routes/riders.py:940-984]** Debug endpoint `/debug/match-check/<rider_id>/<ride_id>` exposes Strava metadata in JSON. Could be accidentally enabled in production. **Fix:** Gate behind `ENV == 'development'` at app init, not `app.debug`.
- **[models.py (throughout)]** No data retention policy or user-initiated deletion mechanism. Strava tokens, activities, and coaching conversations persist indefinitely. **Fix:** Implement 2-year rolling retention; add Strava disconnect that deletes all activity data; document GDPR/CCPA rights.
- **[routes/admin.py]** Admin access from hardcoded first name list (`['sriharsha', 'venkatesh', 'mihir']`). If name changes or account is compromised, access breaks or is unrevokable. **Fix:** `is_admin` DB flag with explicit grant/revoke.

### LOW
- `SESSION_COOKIE_SAMESITE='Lax'` is acceptable; `'Strict'` would be stronger if no cross-site submissions needed.
- Google OAuth scope (`openid email profile`) is minimal — good.
- Strava token revocation on disconnect is implemented (best-effort) — good.
- `strava_data_private` flag respected throughout app — good pattern; ensure it covers all new endpoints.

---

## 🏛️ Chief Architect Review

### CRITICAL (System Reliability Risk)
- **[db.py:8]** Per-request `psycopg2.connect()` on Vercel serverless — no pooling, no reuse. At scale this will exhaust Supabase connection limits (~10ms overhead per request). **Fix:** Use Supabase's PgBouncer pooler endpoint (`port 6543` transaction mode) or `psycopg3` `AsyncConnectionPool`.
- **[cache.py:12-15]** `SimpleCache` is in-process memory — dies on every Vercel cold start. No shared cache across concurrent function instances. Cache key generation is string-based with no type safety. **Fix:** Replace with Redis (Upstash or Vercel KV) for distributed caching; reduce TTL to 60s for serverless context.
- **[app.py:64-82]** `debug_auto_login()` hook executes a DB query on every request in debug mode — privilege escalation vector if `app.debug` leaks to production. **Fix:** Wrap in `ENV == 'development'` env var check, not `app.debug`.
- **[services/rwgps.py:130]** No try/except on `fetch_route()` — if RWGPS is down, all ride plan pages 500. **Fix:** Catch `requests.exceptions.RequestException`; return None; show "Route data unavailable" gracefully.
- **[services/weather.py:275,309,335]** Same — no exception handling on Open-Meteo HTTP calls; outage causes 500s across all plan pages. **Fix:** Same pattern; log and return empty wind data.

### HIGH (Scalability / Maintainability)
- **[models.py (2960 lines, 138 functions)]** Monolithic data access layer — no domain separation, inconsistent caching, hidden N+1s, impossible to test in isolation. **Fix:** Split into `models/riders.py`, `models/rides.py`, `models/plans.py`, `models/chat.py`, `models/wind.py`, `models/base.py`.
- **[cache.py:29-31]** `cache.clear()` on any write nukes the entire cache — unrelated hot data (seasons, clubs) cleared on every signup. **Fix:** Targeted cache key invalidation per entity; preserve immutable caches.
- **[routes/riders.py:1450-1456]** Wind data fetched from Open-Meteo on every page load — 100 riders viewing the same plan = 100 identical API calls. **Fix:** Cache by `plan_id + date`; invalidate at 06:00 UTC; use DB-stored wind data if available.
- **[models.py:88-90,97-98,129-139]** `@cache.memoize()` queries with no documented invalidation point. **Fix:** Add `# CACHE: invalidated by X` comment on every memoized function; audit all write paths.
- **[vercel.json:19-23]** Cron jobs have no distributed lock — concurrent invocations can cause duplicate Strava syncs or backfill races. **Fix:** Supabase advisory lock or Redis `SETNX` at cron handler start.
- **[routes/riders.py:63-65,149-176]** Broad `except Exception → mock data` silently hides DB failures. **Fix:** Distinguish `DatabaseError` (re-raise → 500 + monitor) from `PermissionError` (403).
- **[services/custom_plan_service.py:16-42]** No cache invalidation on custom stop edits — user sees stale merged view until timeout. **Fix:** Cache by `custom_plan_id`; invalidate on every `update_custom_plan_stop()`.

### MEDIUM
- **[models.py:88-90]** Seasons cached with 300s TTL despite being immutable (change once/year). **Fix:** Cache forever; invalidate only on explicit season creation.
- **[services/weather.py:256-282]** Open-Meteo batch request has no size cap on `sample_points`. Large routes could hit URI limits. **Fix:** Cap at 50-100 coordinates; paginate if needed.
- **[services/strava.py, services/openai_coach.py]** Missing explicit `timeout` param on some external HTTP calls. **Fix:** Add `timeout=10` to all `requests.get/post`; `timeout=30` on OpenAI calls.
- **[app.py:99-108]** Hardcoded fallback seasons list `[2025-2026, 2022-2023...]` will be wrong when new seasons are added. **Fix:** Load fallback from config/fixture file; auto-generate from DB on startup.
- **[models.py]** No index on `strava_activity(rider_id, start_date)` — fitness score and recent activity queries do full scans. **Fix:** `CREATE INDEX idx_strava_activity_rider_date ON strava_activity(rider_id, start_date DESC)`.

### LOW
- `models.py` N+1 hidden in batch query loops — audit all callers of `get_all_rider_season_stats()`.
- `services/rwgps.py:102-148` Synchronous network I/O blocks page render for up to 30s on cache miss. Consider background pre-warming common routes.
- Config secrets require redeploy to rotate — no lazy-loading from secrets manager.
- No `rider_season_stats` denormalization — computed on every request; consider precomputed table updated post-sync.

---

## 🤖 AI Skeptic Review

### CRITICAL (Cost / Safety / Injection Risk)
- **[services/chat_service.py:750-753]** User messages sent to OpenAI embeddings API without PII filtering. Raw user text (potentially containing names, HR data, medical info) goes to OpenAI. **Fix:** Filter/hash PII before embedding; or use local embedding model.
- **[services/chat_service.py:818-822]** Rider Strava training data (distances, HR, power, fitness scores) injected into system prompt in plaintext on every message — no user consent for data sharing with OpenAI. **Fix:** Add explicit opt-out toggle; anonymize sensitive fields.
- **[services/chat_service.py:732-791]** Raw WhatsApp chat content (including usernames, private team discussions) injected into system prompt via RAG. No sanitization. **Fix:** Redact usernames; implement content filtering on retrieved chunks; consider local embeddings.
- **[evals/eval_e2e.py:10, evals/eval_intent.py:10]** Eval files make real OpenAI API calls with fixture data containing real team member names. If accidentally triggered in production = uncontrolled API cost. **Fix:** Gate all evals behind `ENVIRONMENT == 'test'`; use mocked OpenAI client in evals; separate eval API credentials.
- **[services/chat_service.py:476]** Max tokens set per call but no hard cap on total session cost. 8+ turns of history + context + tool results can easily hit 10k+ tokens/request. **Fix:** Enforce 6000 input + 2000 output hard cap; truncate history aggressively; implement per-user cost tracking.

### HIGH
- **[services/chat_service.py:583-593]** Guardrail rules from DB injected into system prompt via f-string — compromised DB or malicious admin could inject `"Ignore previous instructions"`. **Fix:** Validate guardrail content against regex whitelist; reject guardrails containing prompt-injection patterns.
- **[services/chat_service.py:612-654]** Detailed gear inventory (bike make/model, wheels, bags, navigation) sent to OpenAI in plaintext. Could reveal expensive equipment. **Fix:** Add gear-sharing privacy toggle; or anonymize brand/model names.
- **[routes/chat.py:20-27]** Message length capped at 2000 chars but no encoding validation — binary data or extreme Unicode bypasses length check. **Fix:** Validate UTF-8; reject non-printable characters; normalize whitespace.
- **[services/chat_tools.py:182-223]** `ALLOWED_QUERIES` validation only at line 196 — if intent classifier is compromised, unvalidated `query_type` could slip through. **Fix:** Assert `query_type in ALLOWED_QUERIES` in `run_agent_loop()` before calling `execute_allowed_query()`.
- **[services/openai_coach.py:468-547]** Coaching calls send full rider training data to OpenAI without privacy flag check. **Fix:** Respect `strava_data_private` flag; hash activity IDs.

### MEDIUM
- **[services/chat_service.py:487-501]** Moderation API calls not rate-limited per user/IP — attacker can exhaust OpenAI moderation quota via spam. **Fix:** Add per-user rate limit on moderation calls via Flask-Limiter.
- **[tests/test_chat_integration.py:161-171]** Injection defense test only checks string presence, doesn't verify LLM ignores adversarial guardrail content. **Fix:** Add test that attempts prompt injection via guardrail and asserts it's blocked.
- **[services/chat_service.py:462-472]** LLM instructed to "cite actual values" from tool results but no post-processing validation that cited numbers match tool output — hallucinations undetected. **Fix:** Extract numbers from LLM response; compare against tool result values; flag mismatches.
- **[evals/]** Eval guardrail files not reviewed — scan for hardcoded production API keys or conditionals that could enable them in prod.

### LOW
- **[services/chat_service.py:732-791]** RAG cosine similarity threshold (0.75) hardcoded — no monitoring of retrieved chunk relevance. **Fix:** Log retrieved chunks in dev; tune threshold based on observed off-topic retrievals.
- **[config.py:6-7]** `DEFAULT_SECRET_KEY='dev-key-change-in-prod'` and `ADMIN_PASSWORD='asha2026'` — should raise `RuntimeError` in production if not overridden.
- **[services/chat_service.py (overall)]** No `SECURITY.md` mapping control IDs (SEC-02, SEC-03, AGENT-*, etc.) to implementation — hard to audit. **Fix:** Add `SECURITY.md` documenting all security control IDs.
- OpenAI completions have no explicit request timeout — slow OpenAI response blocks Vercel function. **Fix:** Add `timeout=30` to all completion calls.

---

## Priority Matrix

| # | Finding | Severity | Persona | Effort |
|---|---------|----------|---------|--------|
| 1 | Rotate leaked .env credentials (DATABASE_URL, OPENAI_API_KEY) | 🔴 CRITICAL | Security | Minutes |
| 2 | Remove debug auto-login bypass | 🔴 CRITICAL | Security + Privacy + Arch | Small |
| 3 | Fix admin role check (first_name → is_admin DB flag) | 🔴 CRITICAL | Security + Privacy | Medium |
| 4 | Open redirect on `next` param in auth routes | 🔴 CRITICAL | Security | Small |
| 5 | Add CSRF protection (Flask-WTF) | 🔴 HIGH | Security | Medium |
| 6 | Fix f-string SQL in models.py (column name injection) | 🔴 HIGH | Security | Medium |
| 7 | PII in print()/logs — replace with app.logger | 🔴 HIGH | Privacy + Staff | Small |
| 8 | Strava `activity:read_all` scope disclosure | 🟠 HIGH | Privacy | Small |
| 9 | Eval files make real API calls — gate behind ENV check | 🟠 HIGH | AI Skeptic | Small |
| 10 | Open-Meteo + RWGPS — add try/except + graceful degradation | 🟠 HIGH | Arch + Staff | Medium |
| 11 | cron secret timing attack → `hmac.compare_digest` | 🟠 HIGH | Security | Tiny |
| 12 | Wind data cached in-memory only (SimpleCache on serverless) | 🟠 HIGH | Arch | Large |
| 13 | Rider Strava data sent to OpenAI without consent toggle | 🟠 HIGH | AI Skeptic + Privacy | Medium |
| 14 | Save wind data in single batch INSERT (not N+1 loop) | 🟡 MEDIUM | Staff | Small |
| 15 | Add `is_admin` DB column; remove hardcoded name list | 🟡 MEDIUM | Security + Privacy | Medium |
| 16 | Add GDPR/data retention + Strava disconnect with delete | 🟡 MEDIUM | Privacy | Large |
| 17 | Cache weather service at hour granularity | 🟡 MEDIUM | Staff | Tiny |
| 18 | Add DB index on strava_activity(rider_id, start_date) | 🟡 MEDIUM | Arch | Tiny |
| 19 | Config.py — raise RuntimeError if SECRET_KEY not set in prod | 🟡 MEDIUM | Security + Arch | Tiny |
| 20 | models.py monolith — split into domain modules | 🟢 LOW | Arch | Large |
