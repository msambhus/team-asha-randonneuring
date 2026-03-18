---
gsd_state_version: 1.0
milestone: v2.0
milestone_name: Personality-Driven Coaching
status: executing
stopped_at: Phase 11 planned (1 plan, 2 tasks, verified by plan-checker)
last_updated: "2026-03-18T11:00:00.000Z"
last_activity: "2026-03-18 — Phase 11 planned: 1 plan (dynamic eval script + tests), all 6 EVAL2 requirements covered, verification passed."
progress:
  total_phases: 12
  completed_phases: 4
  total_plans: 14
  completed_plans: 13
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-17)

**Core value:** Coaching that feels like it comes from a real teammate who knows you — matching each rider's communication style and each coach's authentic personality, grounded in actual conversation data.
**Current focus:** Phase 11 (Braintrust Evals) — planned, ready to execute.

## Current Position

Phase: 11 of 12 (Braintrust Evals) — PLANNED
Plan: 0 of 1 in current phase — ready to execute
Status: Phase 11 planned. Ready to execute.
Last activity: 2026-03-18 — Phase 11 planned: 1 plan (dynamic eval script + tests), all 6 EVAL2 requirements covered.

Progress (Milestone 2): [█████████░] 93% (13/14 plans complete)
Phase 7: COMPLETE — schema migration, 12 model functions, seed data
Phase 8: COMPLETE — schema extension, WhatsApp extraction, blog+merge
Phase 9: COMPLETE — DB-driven coach routing, guardrails, gear context, wiring
Phase 10: COMPLETE — personality admin, gear admin, coach/guardrail admin (3 plans)
Phase 11: PLANNED — 1 plan (dynamic eval script + LLMClassifier scoring + version stamps)
Phase 12: Not started — embed script, knowledge admin page

## Performance Metrics

**Velocity (Milestone 2):**
- Total plans completed: 13
- Average duration: ~5 min per plan
- Total execution time: ~65 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 7 | 3/3 | ~15min | ~5min |
| 8 | 3/3 | ~12min | ~4min |
| 9 | 2/2 | ~8min | ~4min |
| 10 | 3/3 | ~10min | ~3min |

**Recent Trend:**
- Last 3 plans: 10-01 (personality), 10-02 (gear), 10-03 (coach/guardrail)
- Trend: fast execution, clean test results

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Research]: GPT-4o (not GPT-4o-mini) required for personality extraction from noisy WhatsApp data
- [Research]: instructor 1.14.5, trafilatura 2.0.0, pdfplumber 0.11.9 are the three new libraries — all others ruled out for Vercel bundle size or pattern inconsistency
- [Research]: All heavy operations (extraction, embedding) run as local CLI scripts, never as Flask request handlers — Vercel serverless constraint
- [Research]: Personality traits stored as structured typed fields with character limits, not free-text blobs — prompt injection defense (OWASP LLM01)
- [Research]: Two-stage guardrail architecture — classifier pass before persona prompt; DENY rules use canned redirects, not model-generated responses
- [Research]: Phase 12 (knowledge expansion) is architecturally independent and can parallelize with Phases 8-11 after Phase 7 is complete
- [Phase 08-01]: UNIQUE constraint changed from (rider_id, profile_type) to (rider_id, profile_type, extraction_source) to allow per-source rows for extraction pipeline
- [Phase 08-01]: extraction_source CHECK expanded to include 'merged' for post-merge combined profiles
- [Phase 08-02]: technical_depth made Optional in PersonalityExtraction — test fixture omits it; GPT-4o prompt still requests it
- [Phase 08-02]: group_by_sender accepts Union[str, list] — supports both filepath and pre-parsed list call patterns
- [Phase 09]: Module-level _BIKE_KEYWORDS kept as fallback; inline version in run_agent_loop replaced with select_coach_for_message()
- [Phase 10]: Profile priority for admin display: merged > manual > whatsapp > blog
- [Phase 10]: POST saves use extraction_source='manual' to prevent overwriting merged/whatsapp rows

### Pending Todos

None yet.

### Blockers/Concerns

- [Pre-Phase 9]: Venki and Shriram must review 5-10 sample AI responses before Phase 9 goes live to real riders (human review gate)
- [Pre-Phase 9]: Verify current Vercel plan timeout limit (Hobby: 60s vs Pro: 300s) before scoping chat integration

## Session Continuity

Last session: 2026-03-18T10:00:00.000Z
Stopped at: Phase 10 complete. Ready to plan Phase 11.
Resume file: None
