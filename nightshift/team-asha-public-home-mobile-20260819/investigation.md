# Investigation

## Repository and constraints

- Repository: `msambhus/team-asha-randonneuring` from `git remote -v`; default branch is `main`.
- The checkout is clean apart from the pre-existing untracked `.worktrees/` directory.
- Repository rules require a feature branch, a merge from `main` before the PR, a GitHub Issue, and PR-based merging.
- The app is Flask/Jinja on the web and Expo Router/React Native on mobile. No schema change is indicated.

## Requirements mapped to current behavior

1. Anonymous web home must be a generic Team Asha showcase rather than expose aggregate statistics. Current `routes/main.py:index` always loads statistics and `templates/index.html` always renders them.
2. Authenticated web home must remain the existing stats/season dashboard. `app.py` injects `user_logged_in` into templates and the home cache already varies by user/session.
3. The public story should explain Team Asha, randonneuring, the Asha for Education mission, and use available imagery. `static/team_photo.jpg` is an inspected, relevant team image; rider portraits are also available under `static/riders/`.
4. Mobile must allow a non-authenticated landing view. `mobile/app/_layout.tsx` currently redirects any no-token route except login/auth to `/login`; `mobile/app/index.tsx` is an authenticated rides list and assumes `profileComplete`.
5. Authenticated mobile should keep ride access and gain a concise summary consistent with the web story. Existing navigation links from `mobile/app/index.tsx` are the right place to preserve.
6. Mobile rider detail must load a public record. `mobile/app/riders/[rusaId].tsx` calls `/api/riders/<rusaId>` through `usePublicRiders.ts`; Flask serves that endpoint from `routes/live.py` behind `token_or_session_required` and existing API tests prove the intended public brevet-only response.

## Prior art and reusable patterns

- The existing web hero and CSS-variable palette in `templates/index.html`, `templates/base.html`, and `static/style.css` provide brand styling.
- `templates/about.html` and `templates/privacy.html` contain established language about randonneuring and the education mission.
- `mobile/lib/theme.ts` centralizes the matching navy/red/blue palette.
- `tests/test_api_auth.py::test_public_rider_profile_returns_brevet_history_not_training` verifies the server contract.

## Risks and open questions

- There are no additional action/randonneuring photos beyond the inspected team photo and rider portraits in the checkout. The implementation should use those existing assets and avoid inventing attribution or external image dependencies.
- The rider-detail symptom is under-specified. The likely high-value regression is to normalize Expo route params before constructing the endpoint and to make the API error visible/retryable; the server contract should remain token-authenticated and brevet-only. If a live reproduction reveals a different backend error, fix that root cause within the same scope.
- Do not remove authenticated web stats or private-data boundaries.
