"""Chat API routes — SSE streaming endpoint, conversation management, image preview."""
from flask import Blueprint, Response, jsonify, request, session, stream_with_context
from auth import api_login_required
from cache import cache
from services.image_preview import fetch_og_image, _is_safe_url
import models
import services.chat_service as chat_service

chat_bp = Blueprint('chat', __name__)


@chat_bp.route('/api/chat/stream', methods=['POST'])
@api_login_required
def chat_stream():
    data = request.get_json(silent=True)
    if not data:
        return {'error': 'Invalid JSON'}, 400

    message = (data.get('message') or '').strip()
    conversation_id = data.get('conversation_id')
    user_id = session.get('user_id')
    rider_id = session.get('rider_id')

    if not message:
        return {'error': 'Message required'}, 400
    if len(message) > 2000:
        return {'error': 'Message too long (max 2000 characters)'}, 400

    def generate():
        try:
            for chunk in chat_service.process_message(
                user_id=user_id,
                message=message,
                conversation_id=conversation_id,
                rider_id=rider_id,
            ):
                yield chunk
        except Exception:
            yield f'data: {{"error": "Something went wrong. Please try again."}}\n\n'
        yield "data: [DONE]\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@chat_bp.route('/api/chat/conversations', methods=['GET'])
@api_login_required
def list_conversations():
    user_id = session.get('user_id')
    convs = models.get_conversations_for_user(user_id, limit=20)
    return {
        'conversations': [
            {
                'id': str(c['id']),
                'title': c.get('title') or 'Untitled conversation',
                'last_active_at': str(c['last_active_at'])[:16].replace('T', ' '),
            }
            for c in convs
        ]
    }


@chat_bp.route('/api/chat/conversations/<conversation_id>/messages', methods=['GET'])
@api_login_required
def get_conversation_messages(conversation_id):
    user_id = session.get('user_id')
    conv = models.get_conversation(conversation_id, user_id)
    if conv is None:
        return {'error': 'Conversation not found'}, 404
    messages = models.get_recent_messages(conversation_id, limit=16)
    return {
        'messages': [
            {'role': m['role'], 'content': m['content']}
            for m in messages
        ]
    }


@chat_bp.route('/api/image-preview')
@api_login_required
def image_preview():
    """Fetch OpenGraph image metadata for a URL on an allowlisted domain.

    Returns JSON {image_url, title, domain} or error.
    Caches successful results for 1 hour.
    """
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify({'error': 'url parameter required'}), 400
    if not _is_safe_url(url):
        return jsonify({'error': 'domain not allowed'}), 403

    # Check cache first
    cache_key = f'og_preview:{url}'
    cached = cache.get(cache_key)
    if cached is not None:
        return jsonify(cached)

    result = fetch_og_image(url, timeout=2.0)
    if result is None:
        return jsonify({'error': 'no preview available'}), 404

    cache.set(cache_key, result, timeout=3600)
    return jsonify(result)
