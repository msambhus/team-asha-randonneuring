"""Chat service — moderation, message construction, streaming completions,
personalized context assembly.

Security controls: moderation pre-filter, max_tokens enforcement, specific
error handling (no broad except Exception), prompt injection defense.
"""
import os
import json
import logging
from typing import Literal, Optional

from openai import OpenAI, RateLimitError, APITimeoutError, InternalServerError, APIError
from pydantic import BaseModel

try:
    import braintrust
except ImportError:
    braintrust = None

import models
from services.fitness import calculate_fitness_score
from services.openai_coach import _build_training_summary, _build_brevet_history_summary
from services.chat_tools import ALLOWED_QUERIES, execute_allowed_query, execute_web_search

logger = logging.getLogger(__name__)

_client = None

# Braintrust observability — graceful degradation if no API key or SDK
_bt_logger = None
if braintrust is not None and os.environ.get('BRAINTRUST_API_KEY'):
    try:
        _bt_logger = braintrust.init_logger(
            project="Team Asha",
            async_flush=False,  # CRITICAL for Vercel serverless
        )
    except Exception:
        logger.warning("Braintrust logger init failed — spans disabled")


def _get_client():
    global _client
    if _client is None:
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")
        _client = OpenAI(api_key=api_key)
    return _client


class IntentResult(BaseModel):
    """Structured intent classification result for the agentic pipeline."""
    intent: Literal['data_query', 'coaching', 'knowledge', 'route_discussion', 'web_search', 'off_topic']
    query_type: Optional[str] = None   # e.g. "fitness_score", "brevet_history"
    ride_name: Optional[str] = None    # for route_discussion intent


INTENT_CLASSIFICATION_PROMPT = """\
Classify the user's message intent for a cycling/randonneuring coaching chatbot for Team Asha.

Intents:
- data_query: User wants stats, scores, leaderboards, rankings, history, or ride data.
  This includes personal stats AND team-wide data (leaderboard, rankings, Eddington scores).
  Set query_type to one of:
    fitness_score - personal fitness/training stats from Strava
    brevet_history - personal completed brevet rides
    upcoming_rides - personal upcoming ride signups
    career_stats - personal all-time ride stats
    recent_activities - recent Strava activities
    get_team_stats - current season team summary
    get_team_leaderboard - all-time team rankings by total km
    get_eddington_scores - team Eddington number leaderboard
    get_my_eddington - personal Eddington number
- coaching: User wants training advice, strategy, or personalized coaching.
- knowledge: User wants general randonneuring info (rules, cutoffs, nutrition, general training).
- web_search: User asks about specific bike models, gear specs, product recommendations, \
component comparisons, current pricing, tire/wheel reviews, or any cycling question that \
requires up-to-date external information beyond general randonneuring knowledge. Examples: \
"What's a good bike for randonneuring under $2000?", "Is the Shimano 105 groupset good for \
brevets?", "Schwalbe Marathon vs Continental Gatorskin for long rides?", "Best dynamo hub \
for randonneuring?", "Trek Checkpoint vs Surly Long Haul Trucker?"
- route_discussion: User asks about a specific ride plan, route, or control stops.
  Set ride_name to the full ride name including distance (e.g., "Cascade 400").
- off_topic: Question is NOT related to cycling, randonneuring, bikes, or Team Asha.

IMPORTANT: Questions about team data, leaderboards, rankings, scores, and rider comparisons
are data_query — NOT off_topic. Team Asha questions are always relevant.
IMPORTANT: Questions about specific bike brands, models, components, or gear comparisons \
are web_search — NOT knowledge or off_topic.

Return the intent and, where applicable, the query_type or ride_name.
"""


def classify_intent(client, user_message, conversation_messages):
    """Classify user message intent using OpenAI structured outputs.

    Args:
        client: OpenAI client instance
        user_message: The user's message text
        conversation_messages: Recent conversation history (unused in v1, reserved for context)

    Returns:
        Tuple of (IntentResult, usage) where usage is the CompletionUsage object.
    """
    response = client.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": INTENT_CLASSIFICATION_PROMPT},
            {"role": "user", "content": user_message},
        ],
        response_format=IntentResult,
        max_tokens=200,
        timeout=10,
    )
    result = response.choices[0].message.parsed
    if result is None:
        # Refusal or parse failure — treat as off_topic
        result = IntentResult(intent='off_topic')
    return result, response.usage


MAX_ITERATIONS = 5
MAX_DB_QUERIES = 3

DATA_CITATION_INSTRUCTION = (
    "IMPORTANT: The <tool_results> block above contains real data from the database. "
    "You MUST reference specific numbers, dates, and values from this data in your response. "
    "Do not estimate or hedge — cite the actual values."
)


def _format_tool_results(tool_results):
    """Format tool results as XML block for injection into messages.

    Args:
        tool_results: List of {'tool': name, 'result': dict} entries

    Returns:
        XML string with <tool_results> wrapper, or empty string for empty list.
    """
    if not tool_results:
        return ''

    parts = ['<tool_results>']
    for entry in tool_results:
        tool_name = entry['tool']
        result = entry['result']
        if 'error' in result:
            parts.append(f'<tool_result tool="{tool_name}">Error: {result["error"]}</tool_result>')
        else:
            parts.append(f'<tool_result tool="{tool_name}">{json.dumps(result["rows"], default=str)}</tool_result>')
    parts.append('</tool_results>')
    return '\n'.join(parts)


def run_agent_loop(client, user_message, messages, rider_id, user_id, accumulator=None):
    """Agent loop: classify intent, execute tools if needed, stream response.

    Generator function yielding SSE chunks (same interface as _stream_completion).
    Classifies intent once, executes at most one tool, then streams the final answer.

    Args:
        client: OpenAI client instance
        user_message: The user's message text
        messages: Full message list (system + history + user)
        rider_id: Rider ID for user-scoped queries
        user_id: Authenticated user ID from session
        accumulator: Optional dict to collect full_content and token counts
    """
    if accumulator is None:
        accumulator = {}
    # Yield thinking indicator before classification
    yield f'data: {json.dumps({"status": "thinking"})}\n\n'

    # Classify intent
    system_content = messages[0]['content'] if messages else ''
    intent_result, _intent_usage = classify_intent(client, user_message, system_content)

    # Send coach persona: Coach Shriram for bike-specific topics, Coach Venki for everything else
    _BIKE_KEYWORDS = {
        'bike', 'bicycle', 'tire', 'tyre', 'chain', 'derailleur', 'brake',
        'groupset', 'cassette', 'crankset', 'handlebar', 'seatpost', 'headset',
        'spoke', 'hub', 'axle', 'pedal', 'cleat', 'tubeless', 'puncture',
        'flat fix', 'tube', 'rim', 'fork', 'frame', 'stem', 'dropout',
        'maintenance', 'repair', 'mechanic', 'lube', 'grease', 'shifting',
        'bottom bracket', 'saddle height', 'bike fit',
    }
    if intent_result.intent != 'off_topic':
        msg_lower = user_message.lower()
        is_bike = any(kw in msg_lower for kw in _BIKE_KEYWORDS)
        coach = 'shriram' if is_bike else 'venki'
        yield f'data: {json.dumps({"coach": coach})}\n\n'

    tool_results = []
    db_query_count = 0

    for _iteration in range(MAX_ITERATIONS):
        if intent_result.intent == 'off_topic':
            # Append system message instructing polite cycling redirect
            messages.append({
                'role': 'system',
                'content': 'The user asked an off-topic question. Politely redirect them to cycling and randonneuring topics.'
            })
            break

        elif intent_result.intent == 'data_query':
            if db_query_count < MAX_DB_QUERIES and intent_result.query_type and intent_result.query_type in ALLOWED_QUERIES:
                # Team-scoped queries don't need rider_id
                _TEAM_QUERIES = {'get_team_stats', 'get_team_leaderboard', 'get_eddington_scores'}
                if intent_result.query_type in _TEAM_QUERIES:
                    params = ()
                else:
                    params = (rider_id,)
                result = execute_allowed_query(
                    query_type=intent_result.query_type,
                    params=params,
                    user_id=user_id,
                )
                tool_results.append({'tool': intent_result.query_type, 'result': result})
                db_query_count += 1
            break

        elif intent_result.intent == 'route_discussion':
            if intent_result.ride_name and db_query_count < MAX_DB_QUERIES:
                result = execute_allowed_query(
                    query_type='get_ride_plan',
                    params=(intent_result.ride_name, intent_result.ride_name),
                    user_id=user_id,
                )
                tool_results.append({'tool': 'get_ride_plan', 'result': result})
                db_query_count += 1
            break

        elif intent_result.intent == 'web_search':
            result = execute_web_search(client, user_message)
            tool_results.append({'tool': 'web_search', 'result': result})
            break

        else:
            # coaching / knowledge — no tool execution needed
            break

    # Inject tool results into messages if any
    if tool_results:
        formatted = _format_tool_results(tool_results)
        messages.append({
            'role': 'system',
            'content': f'{formatted}\n\n{DATA_CITATION_INSTRUCTION}',
        })

    # Stream the final response
    yield from _stream_completion(messages, accumulator)

    # Emit source cards for web search results (after response stream completes)
    for entry in tool_results:
        if entry['tool'] == 'web_search' and 'rows' in entry.get('result', {}):
            rows = entry['result']['rows']
            if rows and isinstance(rows[0], dict) and rows[0].get('sources'):
                yield f'data: {json.dumps({"sources": rows[0]["sources"]})}\n\n'


def moderate_input(message):
    """Check message against OpenAI Moderation API.
    Returns True if safe, False if flagged or on API failure (fail closed).
    """
    try:
        result = _get_client().moderations.create(
            input=message, model="omni-moderation-latest"
        )
        if result.results[0].flagged:
            logger.warning("Content flagged by moderation")
            return False
        return True
    except Exception:
        logger.error("Moderation API failure — failing closed")
        return False


def build_messages(user_message, history, system_prompt):
    """Construct the message list for chat completion.
    System prompt first, history in order, user message last.
    User content is NEVER concatenated into the system prompt.
    """
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})
    return messages


def _stream_completion(messages, accumulator):
    """Stream chat completion, yielding SSE data lines.
    Accumulator dict is mutated with full_content, prompt_tokens, completion_tokens.
    """
    accumulator['full_content'] = ''
    accumulator['prompt_tokens'] = None
    accumulator['completion_tokens'] = None

    try:
        stream = _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=700,
            stream=True,
            stream_options={"include_usage": True},
            timeout=50,
        )
        for chunk in stream:
            if chunk.usage:
                accumulator['prompt_tokens'] = chunk.usage.prompt_tokens
                accumulator['completion_tokens'] = chunk.usage.completion_tokens
            if chunk.choices and chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                accumulator['full_content'] += token
                yield f"data: {json.dumps(token)}\n\n"
    except RateLimitError:
        msg = {"error": "I'm handling too many requests right now. Please try again in a moment."}
        yield f"data: {json.dumps(msg)}\n\n"
    except APITimeoutError:
        msg = {"error": "That took too long. Please try a shorter question or try again."}
        yield f"data: {json.dumps(msg)}\n\n"
    except InternalServerError:
        msg = {"error": "There's a temporary issue with the AI service. Please try again."}
        yield f"data: {json.dumps(msg)}\n\n"
    except APIError:
        msg = {"error": "Something went wrong with the AI service. Please try again."}
        yield f"data: {json.dumps(msg)}\n\n"


def _get_system_prompt():
    """Get the chat system prompt, with fallback for Plan 03 not yet implemented."""
    try:
        from services.openai_coach import CHAT_SYSTEM_PROMPT
        return CHAT_SYSTEM_PROMPT
    except (ImportError, AttributeError):
        return "You are a cycling and randonneuring coaching assistant for Team Asha."


def assemble_rider_context(user_id, rider_id):
    """Build personalized rider context for the system prompt.

    Returns XML-delimited rider data block, or empty string if:
    - rider_id is None (no rider profile)
    - strava_data_private is True (SEC-11)
    - No Strava connection AND no brevet history
    """
    if rider_id is None:
        return ''

    # Privacy check (SEC-11)
    if models.get_rider_privacy_flag(rider_id):
        return ''

    sections = []

    # Try Strava data first (PERS-01)
    strava_conn = models.get_strava_connection(rider_id)
    if strava_conn:
        activities = models.get_strava_activities(rider_id, days=28)
        fitness_score = calculate_fitness_score(activities) if activities else None
        training_summary = _build_training_summary(activities, fitness_score)
        if training_summary:
            sections.append(training_summary)
    else:
        # Brevet history fallback (PERS-02)
        current_season = models.get_current_season()
        if current_season:
            participation = models.get_rider_participation(rider_id, current_season['id'])
            if participation:
                season_data = [{'season': current_season, 'participation': participation}]
                brevet_summary = _build_brevet_history_summary(season_data)
                if brevet_summary:
                    sections.append(brevet_summary)

    # Upcoming brevets (KNOW-06) — cap at 3
    upcoming = models.get_rider_upcoming_signups(rider_id)
    if upcoming:
        brevet_lines = ["UPCOMING BREVETS:"]
        for signup in upcoming[:3]:
            date_str = str(signup.get('date', ''))[:10]
            name = signup.get('name', 'Unknown')
            dist = signup.get('distance_km') or 0
            status = signup.get('signup_status', '')
            brevet_lines.append(f"  {date_str}: {name} — {dist:.0f}km ({status})")
        sections.append("\n".join(brevet_lines))

    if not sections:
        return ''

    return f"\n<rider_data>\n{chr(10).join(sections)}\n</rider_data>\n"


def assemble_team_context():
    """Build team-wide context (upcoming rides) for the system prompt.

    Available to all users regardless of Strava status (PERS-03).
    Returns XML-delimited team context block.
    """
    upcoming_rides = models.get_upcoming_rides()

    if not upcoming_rides:
        return "\n<team_context>\nNo upcoming Team Asha rides scheduled.\n</team_context>\n"

    lines = ["UPCOMING TEAM ASHA RIDES:"]
    for ride in upcoming_rides[:5]:
        date_str = str(ride.get('date', ''))[:10]
        name = ride.get('name', 'Unknown')
        dist = ride.get('distance_km') or 0
        lines.append(f"  {date_str}: {name} — {dist:.0f}km")

    return f"\n<team_context>\n{chr(10).join(lines)}\n</team_context>\n"


def process_message(user_id, message, conversation_id=None, rider_id=None):
    """Main SSE generator. Moderates, persists, streams, and records the exchange."""
    # Step 1: Moderation
    if not moderate_input(message):
        msg = {"error": "I can't process that message. Please rephrase your question about cycling or randonneuring."}
        yield f"data: {json.dumps(msg)}\n\n"
        return

    # Step 2: Resolve conversation
    if conversation_id is None:
        conv = models.create_conversation(user_id, title=message[:50])
        conversation_id = conv['id']
    else:
        conv = models.get_conversation(conversation_id, user_id)
        if conv is None:
            yield f'data: {json.dumps({"error": "Conversation not found."})}\n\n'
            return

    # Step 3: Persist user message
    models.insert_chat_message(conversation_id, 'user', message)

    # Step 4: Get history (8 turns = 16 messages, CHAT-03)
    history = models.get_recent_messages(conversation_id, limit=16)

    # Step 4.5: Build personalized context (Phase 2)
    context_block = assemble_rider_context(user_id, rider_id)
    team_block = assemble_team_context()
    system_prompt = _get_system_prompt() + context_block + team_block

    # Step 5: Build messages
    messages = build_messages(message, history, system_prompt)

    # Step 6: Send conversation_id to client
    yield f'data: {json.dumps({"conversation_id": str(conversation_id)})}\n\n'

    # Steps 7-8 wrapped in Braintrust span for observability
    accumulator = {}
    span_metadata = None

    if _bt_logger:
        span = _bt_logger.start_span(
            name="chat_message",
            span_attributes={"type": "task"},
            input={"message": message, "conversation_id": str(conversation_id)},
        )
        span.__enter__()
        try:
            span_id = span.id
            trace_id = span.root_span_id
            span_metadata = {"span_id": span_id, "trace_id": trace_id}

            # Step 7: Agent loop
            for chunk in run_agent_loop(_get_client(), message, messages, rider_id, user_id, accumulator=accumulator):
                yield chunk

            # Step 8: Persist assistant response
            full_content = accumulator.get('full_content', '')
            if full_content:
                models.insert_chat_message(
                    conversation_id, 'assistant', full_content,
                    prompt_tokens=accumulator.get('prompt_tokens'),
                    completion_tokens=accumulator.get('completion_tokens'),
                    metadata=span_metadata,
                )
                models.touch_conversation(conversation_id)
        finally:
            full_content = accumulator.get('full_content', '')
            span.log(
                output={"response_length": len(full_content)},
                metadata={
                    "conversation_id": str(conversation_id),
                    "prompt_tokens": accumulator.get('prompt_tokens'),
                    "completion_tokens": accumulator.get('completion_tokens'),
                },
            )
            span.__exit__(None, None, None)
    else:
        # No Braintrust — run without span wrapping
        for chunk in run_agent_loop(_get_client(), message, messages, rider_id, user_id, accumulator=accumulator):
            yield chunk

        full_content = accumulator.get('full_content', '')
        if full_content:
            models.insert_chat_message(
                conversation_id, 'assistant', full_content,
                prompt_tokens=accumulator.get('prompt_tokens'),
                completion_tokens=accumulator.get('completion_tokens'),
            )
            models.touch_conversation(conversation_id)
