# Blueprint

## Approach

Split the web home template by authentication state at render time. Anonymous visitors receive a designed Team Asha story/gallery page that uses only existing static imagery and public mission copy. Authenticated visitors continue receiving the current stats and season dashboard unchanged.

Add a small shared public-summary concept to mobile without introducing a server data contract: the anonymous index becomes a local, image-free (or bundled-image) Team Asha/randonneuring landing screen with sign-in CTA, while the authenticated index keeps its ride list and adds a concise summary/mission card above existing controls. Update the auth gate to allow only the public index through without a token; all private routes remain protected.

For rider detail, make route-param handling explicit (`string` only, no `string[]`), retain the existing `/api/riders/<rusa_id>` contract, and add a retry/error state. Add focused mobile tests for the constructed URL and public profile rendering or a backend regression test if the reproduced failure is server-side.

## Components affected

- `routes/main.py` and `templates/index.html`: choose anonymous vs authenticated home while preserving the authenticated dashboard.
- `mobile/app/_layout.tsx`, `mobile/app/index.tsx`, and mobile styling/types/tests: public landing, authenticated summary, and route access.
- `mobile/app/riders/[rusaId].tsx` and/or `mobile/hooks/usePublicRiders.ts`: robust public rider URL parameter contract and retryable failure state.
- Existing static assets only; no new external image service.

## Data and security contract

No database or API schema changes. Anonymous pages must not request private APIs. `/api/riders` remains token-gated as currently designed, and `/api/riders/<rusa_id>` continues to return only public brevet/permanent history, never Strava/Garmin/training data.

## Rollout and observability

Ship as one PR. Verify anonymous and authenticated web behavior in browser, mobile route/auth behavior with Jest/TypeScript/export checks, and the rider endpoint/client contract. Use existing cache key separation and clear/invalidate relevant caches only if needed; no migration or backfill.

## Verification shape

Custom combined verification: Flask/template tests plus mobile Jest/TypeScript/export checks, followed by browser checks for anonymous/authenticated web home and a mobile flow check for public landing → login and riders → rider detail.
