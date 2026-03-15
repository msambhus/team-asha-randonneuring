"""Tests for chat API routes — auth gating, input validation, SSE streaming."""
import pytest


def test_stream_endpoint(client):
    """POST /api/chat/stream with valid session returns SSE stream."""
    from unittest.mock import patch

    mock_chunks = [
        'data: {"conversation_id": "abc-123"}\n\n',
        'data: "Hello"\n\n',
    ]

    with patch('services.chat_service.process_message', return_value=iter(mock_chunks)):
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.post('/api/chat/stream', json={'message': 'Hello'})
        assert resp.status_code == 200
        assert 'text/event-stream' in resp.content_type
        data = resp.get_data(as_text=True)
        assert 'data:' in data


def test_auth_required(client):
    """POST /api/chat/stream without session returns 401 JSON (not 302 redirect)."""
    resp = client.post('/api/chat/stream', json={'message': 'Hello'})
    assert resp.status_code == 401
    json_data = resp.get_json()
    assert json_data['error'] == 'Authentication required'


def test_input_length_limit(client):
    """Message > 2000 characters returns 400."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    resp = client.post('/api/chat/stream', json={'message': 'x' * 2001})
    assert resp.status_code == 400
    json_data = resp.get_json()
    assert 'too long' in json_data['error'].lower() or 'length' in json_data['error'].lower()


def test_empty_message(client):
    """Empty or missing message returns 400."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    resp = client.post('/api/chat/stream', json={'message': ''})
    assert resp.status_code == 400

    with client.session_transaction() as sess:
        sess['user_id'] = 1
    resp = client.post('/api/chat/stream', json={})
    assert resp.status_code == 400


def test_invalid_json(client):
    """Non-JSON body returns 400."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    resp = client.post('/api/chat/stream', data='not json', content_type='text/plain')
    assert resp.status_code == 400


# --- Conversation list endpoint tests ---

def test_list_conversations(client):
    """GET /api/chat/conversations returns conversations for authenticated user."""
    from unittest.mock import patch
    from datetime import datetime

    mock_convs = [
        {'id': 'conv-1', 'title': 'Training question', 'last_active_at': datetime(2026, 3, 15, 10, 30)},
        {'id': 'conv-2', 'title': None, 'last_active_at': datetime(2026, 3, 14, 9, 0)},
    ]

    with patch('models.get_conversations_for_user', return_value=mock_convs) as mock_fn:
        with client.session_transaction() as sess:
            sess['user_id'] = 42
        resp = client.get('/api/chat/conversations')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'conversations' in data
        assert len(data['conversations']) == 2
        assert data['conversations'][0]['id'] == 'conv-1'
        assert data['conversations'][0]['title'] == 'Training question'
        # Null title should become 'Untitled conversation'
        assert data['conversations'][1]['title'] == 'Untitled conversation'
        mock_fn.assert_called_once_with(42, limit=20)


def test_list_conversations_unauthenticated(client):
    """GET /api/chat/conversations without auth returns 401."""
    resp = client.get('/api/chat/conversations')
    assert resp.status_code == 401


def test_list_conversations_empty(client):
    """New user with no conversations returns empty array."""
    from unittest.mock import patch

    with patch('models.get_conversations_for_user', return_value=[]):
        with client.session_transaction() as sess:
            sess['user_id'] = 99
        resp = client.get('/api/chat/conversations')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['conversations'] == []


def test_get_conversation_messages(client):
    """GET /api/chat/conversations/<id>/messages returns messages for owned conversation."""
    from unittest.mock import patch

    mock_conv = {'id': 'conv-1', 'user_id': 42, 'title': 'Test'}
    mock_msgs = [
        {'role': 'user', 'content': 'Hello'},
        {'role': 'assistant', 'content': 'Hi there!'},
    ]

    with patch('models.get_conversation', return_value=mock_conv) as mock_get_conv, \
         patch('models.get_recent_messages', return_value=mock_msgs) as mock_get_msgs:
        with client.session_transaction() as sess:
            sess['user_id'] = 42
        resp = client.get('/api/chat/conversations/conv-1/messages')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['messages']) == 2
        assert data['messages'][0]['role'] == 'user'
        assert data['messages'][0]['content'] == 'Hello'
        assert data['messages'][1]['role'] == 'assistant'
        mock_get_conv.assert_called_once_with('conv-1', 42)
        mock_get_msgs.assert_called_once_with('conv-1', limit=16)


def test_get_conversation_messages_wrong_user(client):
    """GET messages for another user's conversation returns 404."""
    from unittest.mock import patch

    with patch('models.get_conversation', return_value=None):
        with client.session_transaction() as sess:
            sess['user_id'] = 42
        resp = client.get('/api/chat/conversations/someone-elses-conv/messages')
        assert resp.status_code == 404
        data = resp.get_json()
        assert 'error' in data


def test_get_conversation_messages_unauthenticated(client):
    """GET /api/chat/conversations/<id>/messages without auth returns 401."""
    resp = client.get('/api/chat/conversations/conv-1/messages')
    assert resp.status_code == 401
