---
phase: 03-agentic-pipeline
plan: 03
subsystem: agent-loop
tags: [agent-loop, sse, streaming, tool-results, xml-injection, token-logging]

# Dependency graph
requires: [03-01, 03-02]
provides:
  - "run_agent_loop() generator wiring intent classification to tool execution to streaming"
  - "_format_tool_results() XML formatter for tool result injection into messages"
  - "DATA_CITATION_INSTRUCTION constant for data-grounded responses"
  - "Coach persona routing (Shriram for bike topics, Venki for everything else)"
  - "process_message() updated to use run_agent_loop() instead of _stream_completion()"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns: ["SSE thinking indicator before classification", "XML tool_results block injection", "Coach persona SSE event", "Accumulator dict for cross-generator token tracking"]

key-files:
  created: []
  modified:
    - services/chat_service.py
    - tests/test_agent_pipeline.py
    - tests/test_chat_service.py

key-decisions:
  - "v1 is single-hop: classify once, execute at most one tool, stream — multi-hop deferred"
  - "MAX_ITERATIONS=5 and MAX_DB_QUERIES=3 as guard rails"
  - "Team-scoped queries (get_team_stats, get_team_leaderboard, get_eddington_scores) pass params=() — no rider_id"
  - "get_ride_plan passes params=(ride_name, ride_name) for dual ILIKE matching"
  - "Coach persona selected by keyword matching against bike-related terms"
  - "Token logging works through existing accumulator -> insert_chat_message path (AGENT-10)"
  - "Braintrust span wrapping integrated around agent loop in process_message()"
---

# Plan 03-03 Summary: Agent Loop

## What was built
- `run_agent_loop(client, user_message, messages, rider_id, user_id, accumulator)` — generator that:
  1. Yields `{"status": "thinking"}` SSE event
  2. Classifies intent via `classify_intent()`
  3. Emits coach persona SSE event (Shriram/Venki)
  4. Retrieves RAG knowledge context for non-off-topic intents
  5. Routes to tool execution based on intent type
  6. Formats tool results as XML and injects into messages with citation instruction
  7. Yields from `_stream_completion()` for final response
  8. Emits source cards for web search results
- `_format_tool_results()` — XML formatter for tool result injection
- `process_message()` updated to call `run_agent_loop()` with Braintrust span wrapping

## Tests added (in test_agent_pipeline.py)
- 12 tests covering: each intent routing path, team stats params, route discussion params, off-topic/coaching/knowledge no-DB, iteration guard, DB query cap, thinking event, token usage

## Requirements satisfied
- AGENT-07: Iteration and query guards (MAX_ITERATIONS=5, MAX_DB_QUERIES=3)
- AGENT-09: Tool result injection with data citation instruction
- AGENT-10: Token logging via accumulator -> insert_chat_message path
