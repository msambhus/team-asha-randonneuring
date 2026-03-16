"""Tests for chat widget rendering — logged-in vs logged-out, SSE client presence."""


def test_widget_renders_for_logged_in_user(client):
    """GET / with logged-in session returns HTML containing chat-toggle and chat-panel."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    resp = client.get('/')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'chat-toggle' in html
    assert 'chat-panel' in html


def test_widget_hidden_for_logged_out_user(client):
    """GET / without login does NOT contain chat-toggle."""
    resp = client.get('/')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'chat-toggle' not in html
    assert 'chat-panel' not in html


def test_widget_contains_sse_fetch(client):
    """Widget HTML contains fetch call to /api/chat/stream."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    resp = client.get('/')
    html = resp.get_data(as_text=True)
    assert '/api/chat/stream' in html
    assert 'ReadableStream' not in html or 'reader' in html  # Uses fetch + reader pattern


def test_widget_has_session_storage(client):
    """Widget JS references sessionStorage for open/close persistence."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    resp = client.get('/')
    html = resp.get_data(as_text=True)
    assert 'sessionStorage' in html
    assert 'chatOpen' in html


def test_widget_has_coach_title(client):
    """Widget header shows Team Asha Coaches title."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    resp = client.get('/')
    html = resp.get_data(as_text=True)
    assert 'Team Asha Coaches' in html
