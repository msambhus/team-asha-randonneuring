"""Chat API routes — SSE streaming endpoint, conversation management."""
from flask import Blueprint, Response, request, session, stream_with_context
from auth import api_login_required
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
