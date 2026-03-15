"""Chat API routes — SSE streaming endpoint."""
from flask import Blueprint, Response, request, session, stream_with_context
from auth import api_login_required
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
