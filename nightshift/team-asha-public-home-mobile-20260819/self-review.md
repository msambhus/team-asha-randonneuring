# Build self-review

## Reviewed subject

Feature branch `feature/team-asha-public-home-mobile`, working tree diff from `main` after the approved frame plan.

## Review result

`PASS` with one external verification limitation: the repository Playwright command fails while loading the existing TypeScript config (`SyntaxError: Cannot use import statement outside a module`) before any browser test starts.

## Requirement checks

- Anonymous web home returns before aggregate-stat queries and renders the Team Asha story, local imagery, randonneuring explanation, education mission, and sign-in/navigation calls to action.
- Authenticated web home follows the existing stats/season branch; a session-backed render check confirms the stats dashboard remains present.
- Mobile root is the only unauthenticated landing route permitted by the auth gate. Private routes and token-gated APIs remain protected.
- Mobile authenticated ride list remains intact and gains a concise mission summary.
- Rider route params are normalized from Expo scalar/array shapes, malformed IDs are rejected, the public endpoint path is encoded, and the error screen offers retry.
- Existing Flask privacy tests still prove public rider responses contain brevet history without training/provider data.

## Checks observed

- `python3 -m pytest -q tests/test_api_auth.py`: 106 passed.
- `npm test -- --runInBand` in `mobile`: 14 suites, 71 tests passed.
- `npm run lint` in `mobile`: passed.
- `npm run build:css`: passed.
- Direct anonymous and session-backed Flask render assertions: passed.
- `npm run test:e2e`: unproven due pre-existing Playwright TypeScript config loading failure.

## Lenses and residual risk

- Security/isolation: no private API was added to the public root; rider endpoint remains public-results-only behind existing token/session middleware.
- Compatibility: existing web authenticated content and mobile ride flows remain in place; full mobile suite passed.
- Diff hygiene: generated Team Asha CSS is retained for new utility classes; unrelated BrevetHub CSS copies were restored after the root CSS build script touched them.
- UX/error path: public landing has a sign-in CTA; rider detail has a retry path.
- Residual: visual browser evidence remains pending until the Playwright config/dependency issue is resolved in the environment.
