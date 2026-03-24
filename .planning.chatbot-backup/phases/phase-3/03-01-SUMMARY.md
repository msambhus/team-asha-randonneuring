---
phase: 03-agentic-pipeline
plan: 01
subsystem: intent-classification
tags: [openai, pydantic, structured-outputs, gpt-4o-mini, intent-routing]

# Dependency graph
requires: []
provides:
  - "IntentResult Pydantic model with 6 Literal intent values (data_query, coaching, knowledge, route_discussion, web_search, off_topic)"
  - "classify_intent() function returning (IntentResult, usage) tuple"
  - "INTENT_CLASSIFICATION_PROMPT constant with all intent definitions and query_type enum"
affects: [03-03-PLAN]

# Tech tracking
tech-stack:
  added: []
  patterns: ["OpenAI chat.completions.parse() for structured outputs", "Pydantic Literal type for intent enum validation", "gpt-4o-mini for fast/cheap classification"]

key-files:
  created: []
  modified:
    - services/chat_service.py
    - tests/test_agent_pipeline.py

key-decisions:
  - "Used gpt-4o-mini for classification (fast, cheap, sufficient for intent routing)"
  - "6 intents instead of original 5 — added web_search for bike/gear spec questions"
  - "10 query_type values in INTENT_CLASSIFICATION_PROMPT (7 original + 3 added: get_team_leaderboard, get_eddington_scores, get_my_eddington)"
  - "Refusal (None parsed result) treated as off_topic — fail-safe default"
---

# Plan 03-01 Summary: Intent Classification

## What was built
- `IntentResult` Pydantic model with `intent` (6 Literal values), `query_type` (Optional[str]), `ride_name` (Optional[str])
- `classify_intent(client, user_message, conversation_messages)` function using `client.chat.completions.parse()` with structured outputs
- `INTENT_CLASSIFICATION_PROMPT` constant documenting all intents, query types, and routing rules

## Tests added (in test_agent_pipeline.py)
- 9 tests covering: each intent type classification, refusal handling, Literal validation, optional field defaults, model parameter verification

## Requirements satisfied
- AGENT-01: Intent classification implemented
- AGENT-02: 6 intent types with structured output parsing
