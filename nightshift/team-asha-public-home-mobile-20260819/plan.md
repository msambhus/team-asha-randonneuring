# Implementation plan

## Scope

One feature branch and one PR covering public web storytelling, mobile landing/auth routing, and the mobile rider-detail loading fix.

## Steps

1. Add a regression-friendly authenticated/anonymous branch in the web home render path and refactor the template so the existing statistics/season content is behind the authenticated branch. Build the anonymous story page with Team Asha mission copy, a clear explanation of brevets/randonneuring, calls to action, and the inspected local team/rider imagery.
2. Add a mobile public landing component/screen in the existing index route. Update the auth gate to permit only `/` without a token; keep every ride, calendar, profile, rider directory, and settings route protected. Preserve the existing authenticated ride list and add the concise mission summary above it.
3. Normalize the mobile rider route parameter and make the public rider query/error state robust. Add focused tests that assert a tapped RUSA ID produces `/api/riders/<id>` and renders the returned public history; keep the existing server-side privacy test.
4. Run focused Flask and mobile tests, then TypeScript/export checks and browser verification. Fix only in-scope findings.
5. Create or reuse a GitHub Issue, commit with a why-focused message, merge `main` into the feature branch, open the PR linking the issue, resolve checks/review, and merge the PR. Do not claim production release beyond what the repo/deployment evidence proves.

## Definition of done

- Anonymous web page is generic/story-led and has no aggregate stats cards.
- Authenticated web page remains stats/season-led.
- Mobile public landing works without login; authenticated mobile remains useful and preserves ride flows.
- Mobile rider detail works from the directory and remains public-results-only.
- Relevant tests and build checks pass; GitHub Issue/PR/merge evidence exists.

## Recovery and escalation

- Up to two implementation retries per finding, three per subtask, eight for the mission.
- Escalate if the available local imagery is insufficient and new photo sourcing/attribution is required, if the rider failure is an external API/deployment issue that cannot be reproduced, or if merging requires a policy/permission decision.
