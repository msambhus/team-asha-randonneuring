# Plan 09-01 Summary

**Status:** PLAN COMPLETE
**Commits:** a244b3c feat: community-first knowledge prioritization — ALWAYS lead with Team Asha context (WA-PRI-01–08)

## What Was Built
- `COMMUNITY_KNOWLEDGE_INSTRUCTION` constant — strengthened RAG injection with "ALWAYS present community knowledge FIRST", named attribution from brackets, and web comparison framing
- `WEB_WITH_COMMUNITY_INSTRUCTION` constant — structured response template when both community + web results are present (community first, then web under comparison heading)
- Updated tool result injection logic: detects `<knowledge_context>` in messages and switches to community-first instruction for web_search results
- Updated `CHAT_SYSTEM_PROMPT` with "COMMUNITY KNOWLEDGE PRIORITY:" section including contradiction/agreement framing guidance
- Bumped `max_tokens` to 800 for web_search intent via parameterized `_stream_completion(max_tokens=)`
- 5 new tests covering WA-PRI-01 through WA-PRI-08

## Test Results
- 5 new tests, all passing
- Full suite: 218 tests passing (6 skipped)

## Files Modified
- `services/chat_service.py` — COMMUNITY_KNOWLEDGE_INSTRUCTION, WEB_WITH_COMMUNITY_INSTRUCTION, parameterized max_tokens, community-aware tool result injection
- `services/openai_coach.py` — COMMUNITY KNOWLEDGE PRIORITY section in CHAT_SYSTEM_PROMPT, updated DATA NOTE
- `tests/test_agent_pipeline.py` — 3 new WA-PRI tests + **kwargs fix for mock helper
- `tests/test_rag_retrieval.py` — 1 new WA-PRI test + **kwargs fixes for mock helpers
- `tests/test_system_prompt.py` — 1 new WA-PRI test
- `tests/test_chat_service.py` — **kwargs fix for mock helper
- `tests/test_rwgps_chat.py` — **kwargs fix for 2 mock helpers
