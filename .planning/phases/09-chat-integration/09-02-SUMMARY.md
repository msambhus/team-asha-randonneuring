# Phase 09 Plan 02 Summary — Guardrails, Gear Context, and Wiring

## What was built
- `assemble_coach_context()` — loads guardrails from DB, appends as `<guardrails>` XML block to CHAT_SYSTEM_PROMPT (GUARD-07)
- `assemble_gear_context(rider_id)` — loads gear preferences, renders as `<gear_context>` XML block (GEAR-03)
- Wired `process_message()` to use `assemble_coach_context()` + `assemble_gear_context()` instead of `_get_system_prompt()`
- Wired `run_agent_loop()` to use `select_coach_for_message()` instead of inline `_BIKE_KEYWORDS`
- `seed_guardrails()` in seed script — 4 sample guardrail rules (scope, topic_block, escalation, tone_limit)
- `_get_system_prompt()` and inline `_BIKE_KEYWORDS` marked DEPRECATED

## Requirements covered
- GUARD-07: Guardrail rules loaded from DB and injected in system prompt
- GEAR-03: Gear preferences loaded for rider and injected in context

## Tests (11 new, 19 total in file)
- `test_assemble_coach_context_with_guardrails` — rules rendered in XML block
- `test_assemble_coach_context_no_guardrails` — returns base prompt unchanged
- `test_assemble_coach_context_db_error` — graceful fallback
- `test_assemble_coach_context_injection_defense` — defense note present
- `test_assemble_gear_context_with_data` — full gear rendered
- `test_assemble_gear_context_no_data` — empty string for no record
- `test_assemble_gear_context_no_rider` — empty string for None rider_id
- `test_assemble_gear_context_privacy_flag` — privacy respected
- `test_assemble_gear_context_sparse_data` — only non-null fields shown
- `test_process_message_uses_assemble_coach_context` — wiring verified via inspect
- `test_run_agent_loop_uses_select_coach` — wiring verified via inspect

## Commit
f79dfef feat(09-02): wire guardrails, gear context, and DB-driven routing into chat pipeline
