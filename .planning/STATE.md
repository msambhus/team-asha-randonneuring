---
gsd_state_version: 1.0
milestone: v2.0
milestone_name: Personality-Driven Coaching
status: complete
stopped_at: Phase 12 complete — Milestone 2 fully executed (16/16 plans)
last_updated: "2026-03-18T12:00:00.000Z"
last_activity: "2026-03-18 — Phase 12 executed: embed script + knowledge admin. All 6 phases complete."
progress:
  total_phases: 12
  completed_phases: 6
  total_plans: 16
  completed_plans: 16
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-17)

**Core value:** Coaching that feels like it comes from a real teammate who knows you — matching each rider's communication style and each coach's authentic personality, grounded in actual conversation data.
**Current focus:** Milestone 2 COMPLETE. All 12 phases executed.

## Current Position

Phase: 12 of 12 (Knowledge Base Expansion) — COMPLETE
Plan: 2 of 2 in current phase — all done
Status: Milestone 2 complete. All 16 plans executed across 6 phases.
Last activity: 2026-03-18 — Phase 12 executed: embed script + knowledge admin page.

Progress (Milestone 2): [██████████] 100% (16/16 plans complete)
Phase 7: COMPLETE — schema migration, 12 model functions, seed data
Phase 8: COMPLETE — schema extension, WhatsApp extraction, blog+merge
Phase 9: COMPLETE — DB-driven coach routing, guardrails, gear context, wiring
Phase 10: COMPLETE — personality admin, gear admin, coach/guardrail admin (3 plans)
Phase 11: COMPLETE — dynamic guardrail eval with LLMClassifier scoring + version stamps
Phase 12: COMPLETE — embed script (KB-01/02/03) + knowledge admin (KB-04/05/06)

## Performance Metrics

**Velocity (Milestone 2):**
- Total plans completed: 16
- Average duration: ~5 min per plan
- Total execution time: ~80 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 7 | 3/3 | ~15min | ~5min |
| 8 | 3/3 | ~12min | ~4min |
| 9 | 2/2 | ~8min | ~4min |
| 10 | 3/3 | ~10min | ~3min |
| 11 | 1/1 | ~5min | ~5min |
| 12 | 2/2 | ~8min | ~4min |

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
- [Phase 11-braintrust-evals]: Patch _classifier module-level instance in tests (not LLMClassifier class) — avoids re-import complexity; module-level instances must be patched directly
- [Phase 11-braintrust-evals]: CASE_GENERATORS dispatcher dict for eval test cases — adding new rule_type only requires new generator function, load_dataset() unchanged
- [Phase 12-01]: trafilatura import made lazy (try/except) — not installed in test env; tests mock it
- [Phase 12-02]: delete_knowledge_source() validates web_ prefix — prevents accidental deletion of WhatsApp chunks

### Pending Todos

None yet.

### Blockers/Concerns

- [Pre-Phase 9]: Venki and Shriram must review 5-10 sample AI responses before Phase 9 goes live to real riders (human review gate)
- [Pre-Phase 9]: Verify current Vercel plan timeout limit (Hobby: 60s vs Pro: 300s) before scoping chat integration

## Session Continuity

Last session: 2026-03-18T12:00:00.000Z
Stopped at: Milestone 2 complete — all 6 phases (7-12) executed
Resume file: None
