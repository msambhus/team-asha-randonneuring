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
