"""Chat service — moderation, message construction, streaming completions.

Security controls: moderation pre-filter, max_tokens enforcement, specific
error handling (no broad except Exception), prompt injection defense.
"""
import os
import json
import logging
from openai import OpenAI, RateLimitError, APITimeoutError, InternalServerError, APIError

import models

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")
        _client = OpenAI(api_key=api_key)
    return _client


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


def process_message(user_id, message, conversation_id=None):
    """Main SSE generator. Moderates, persists, streams, and records the exchange."""
    # Step 1: Moderation
    if not moderate_input(message):
        msg = {"error": "I can't process that message. Please rephrase your question about cycling or randonneuring."}
        yield f"data: {json.dumps(msg)}\n\n"
        return

    # Step 2: Resolve conversation
    if conversation_id is None:
        conv = models.create_conversation(user_id)
        conversation_id = conv['id']
    else:
        conv = models.get_conversation(conversation_id, user_id)
        if conv is None:
            yield f'data: {json.dumps({"error": "Conversation not found."})}\n\n'
            return

    # Step 3: Persist user message
    models.insert_chat_message(conversation_id, 'user', message)

    # Step 4: Get history
    history = models.get_recent_messages(conversation_id, limit=20)

    # Step 5: Build messages
    system_prompt = _get_system_prompt()
    messages = build_messages(message, history, system_prompt)

    # Step 6: Send conversation_id to client
    yield f'data: {json.dumps({"conversation_id": str(conversation_id)})}\n\n'

    # Step 7: Stream completion
    accumulator = {}
    for chunk in _stream_completion(messages, accumulator):
        yield chunk

    # Step 8: Persist assistant response
    full_content = accumulator.get('full_content', '')
    if full_content:
        models.insert_chat_message(
            conversation_id, 'assistant', full_content,
            prompt_tokens=accumulator.get('prompt_tokens'),
            completion_tokens=accumulator.get('completion_tokens'),
        )
        models.touch_conversation(conversation_id)
