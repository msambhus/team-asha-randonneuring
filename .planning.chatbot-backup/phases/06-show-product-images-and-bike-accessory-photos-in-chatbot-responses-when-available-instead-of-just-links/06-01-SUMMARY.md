---
phase: 06-show-product-images-and-bike-accessory-photos-in-chatbot-responses-when-available-instead-of-just-links
plan: 01
subsystem: api
tags: [og-image, ssrf-defense, beautifulsoup, requests, flask-caching, image-preview]

# Dependency graph
requires:
  - phase: 01-foundation
    provides: Flask blueprint routing, @api_login_required decorator, Flask-Caching SimpleCache
provides:
  - "services/image_preview.py with ALLOWED_PREVIEW_DOMAINS, _is_safe_url(), fetch_og_image()"
  - "/api/image-preview GET endpoint with auth, validation, 1hr caching"
  - "SSRF defense pattern: HTTPS-only, domain allowlist, 2s timeout, no redirects, 100KB body limit"
affects: [06-02-frontend-image-cards]

# Tech tracking
tech-stack:
  added: []
  patterns: [ssrf-safe-url-fetching, og-metadata-extraction, domain-allowlist-validation]

key-files:
  created: [services/image_preview.py, tests/test_image_preview.py]
  modified: [routes/chat.py]

key-decisions:
  - "Excluded amazon.com and rei.com from allowlist -- these sites block server-side OG fetches"
  - "No CSP header change needed (IMG-08) -- no CSP set in vercel.json or Flask, browser default permits HTTPS images"
  - "Used lxml parser for BeautifulSoup -- already in requirements.txt, faster than html.parser"

patterns-established:
  - "SSRF defense: HTTPS-only + domain allowlist + timeout + no redirects + body limit"
  - "OG extraction: og:image -> twitter:image fallback, relative URL resolution, HTTPS-only result"

requirements-completed: [IMG-01, IMG-02, IMG-05, IMG-06, IMG-08]

# Metrics
duration: 4min
completed: 2026-03-16
---

# Phase 6 Plan 01: Image Preview Service Summary

**OG image preview service with SSRF defenses (allowlist, HTTPS-only, 2s timeout, 100KB limit) and /api/image-preview endpoint with 1hr caching**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-17T02:57:29Z
- **Completed:** 2026-03-17T03:01:29Z
- **Tasks:** 2 (TDD RED + GREEN)
- **Files modified:** 3

## Accomplishments
- Image preview service (`services/image_preview.py`) with full SSRF defense suite: HTTPS-only, 12-domain allowlist, 2s timeout, no redirect following, 100KB body limit
- `/api/image-preview` GET endpoint with `@api_login_required`, domain validation, fetch-on-miss, 1-hour cache TTL
- 25 new tests covering URL validation, OG extraction, fallbacks, error handling, auth gating, and cache behavior
- Confirmed IMG-08 (CSP): no Content-Security-Policy header in vercel.json or Flask app -- browsers will load external HTTPS images without issues

## Task Commits

Each task was committed atomically:

1. **TDD RED: Failing tests** - `08b92cc` (test)
2. **TDD GREEN: Service + endpoint implementation** - `81193f3` (feat)

_TDD plan: RED (failing tests) -> GREEN (implementation passes all 25 tests) -> No refactor needed._

## Files Created/Modified
- `services/image_preview.py` - Image preview service with SSRF defenses, OG metadata extraction
- `routes/chat.py` - Added `/api/image-preview` endpoint with auth, validation, caching
- `tests/test_image_preview.py` - 25 tests: 8 URL validation, 11 fetch/extraction, 6 endpoint integration

## Decisions Made
- **Excluded amazon.com and rei.com:** Research notes confirm these sites block server-side OG fetches; including them would cause silent failures
- **No CSP change for IMG-08:** Verified `vercel.json` has no `headers` section and no Flask middleware sets `Content-Security-Policy`. Browser default policy permits loading images from any HTTPS origin.
- **lxml parser:** Already in requirements.txt, faster than built-in html.parser for OG tag extraction

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Installed lxml locally for tests**
- **Found during:** TDD GREEN phase (running tests)
- **Issue:** `lxml` was in requirements.txt but not installed in local dev environment; BeautifulSoup raised `FeatureNotFound`
- **Fix:** `pip install lxml` (no requirements.txt change needed, already listed)
- **Files modified:** None (local environment only)
- **Verification:** All 25 tests pass after install
- **Committed in:** 81193f3 (part of GREEN commit)

---

**Total deviations:** 1 auto-fixed (1 blocking -- local env only)
**Impact on plan:** Minimal. Only a local package install; no code or dependency changes.

## Issues Encountered
None beyond the lxml local install noted above.

## User Setup Required
None - no external service configuration required.

## CSP Verification (IMG-08)

Confirmed that no Content-Security-Policy header is set anywhere:
- `vercel.json`: Only has `version`, `git`, `buildCommand`, `builds`, `routes` -- no `headers` section
- Flask app: No `after_request` handler, no CSP references in any `.py` file
- **Conclusion:** Browser default policy permits loading images from any origin. External HTTPS images from og:image URLs will render without CSP issues. IMG-08 requires NO code change.

## Next Phase Readiness
- Backend image preview API is complete and tested
- Plan 02 (frontend) can now call `GET /api/image-preview?url=<url>` to render image cards below chatbot responses
- The endpoint returns `{image_url, title, domain}` JSON ready for card rendering

## Self-Check: PASSED

- All 3 source/test files exist on disk
- Both commit hashes (08b92cc, 81193f3) found in git log
- SUMMARY.md created at expected path

---
*Phase: 06-show-product-images-and-bike-accessory-photos-in-chatbot-responses-when-available-instead-of-just-links*
*Completed: 2026-03-16*
