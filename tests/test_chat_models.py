"""Tests for chat persistence schema and CRUD functions (INFRA-01, INFRA-05, INFRA-06, SEC-07)."""
import psycopg2.extras


def test_schema_exists(db_conn):
    """Verify conversation and chat_message tables exist with correct columns."""
    cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Check tables exist
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name IN ('conversation', 'chat_message')
        ORDER BY table_name
    """)
    tables = [row['table_name'] for row in cur.fetchall()]
    assert 'chat_message' in tables, "chat_message table does not exist"
    assert 'conversation' in tables, "conversation table does not exist"

    # Check conversation columns
    cur.execute("""
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'conversation'
        ORDER BY column_name
    """)
    conv_cols = {row['column_name']: row['data_type'] for row in cur.fetchall()}
    assert 'id' in conv_cols
    assert 'user_id' in conv_cols
    assert 'title' in conv_cols
    assert 'created_at' in conv_cols
    assert 'last_active_at' in conv_cols

    # Check chat_message columns
    cur.execute("""
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'chat_message'
        ORDER BY column_name
    """)
    msg_cols = {row['column_name']: row['data_type'] for row in cur.fetchall()}
    assert 'id' in msg_cols
    assert 'conversation_id' in msg_cols
    assert 'role' in msg_cols
    assert 'content' in msg_cols
    assert 'prompt_tokens' in msg_cols
    assert 'completion_tokens' in msg_cols
    assert 'metadata' in msg_cols
    assert 'created_at' in msg_cols


def test_conversation_crud(app, db_conn):
    """Test create, get, and list conversations (INFRA-05)."""
    from models import create_conversation, get_conversation, get_conversations_for_user

    test_user_id = 1  # Assumes app_user with id=1 exists in test DB

    with app.app_context():
        # Create
        conv = create_conversation(user_id=test_user_id, title="Test Conversation")
        assert conv is not None
        assert 'id' in conv
        assert conv['user_id'] == test_user_id
        assert conv['title'] == "Test Conversation"
        assert 'created_at' in conv
        assert 'last_active_at' in conv

        # Get by ID + user_id
        fetched = get_conversation(conv['id'], user_id=test_user_id)
        assert fetched is not None
        assert fetched['id'] == conv['id']

        # List for user
        convs = get_conversations_for_user(user_id=test_user_id)
        assert isinstance(convs, list)
        assert any(c['id'] == conv['id'] for c in convs)


def test_message_crud(app, db_conn):
    """Test insert and retrieve chat messages (INFRA-06)."""
    from models import create_conversation, insert_chat_message, get_recent_messages

    with app.app_context():
        conv = create_conversation(user_id=1, title="Msg Test")
        msg = insert_chat_message(
            conversation_id=conv['id'],
            role='user',
            content='Hello',
            prompt_tokens=10,
            completion_tokens=0
        )
        assert msg is not None
        assert msg['role'] == 'user'
        assert msg['content'] == 'Hello'
        assert msg['prompt_tokens'] == 10
        assert msg['completion_tokens'] == 0
        assert 'metadata' in msg
        assert 'created_at' in msg

        messages = get_recent_messages(conv['id'])
        assert len(messages) >= 1
        assert messages[-1]['content'] == 'Hello'


def test_history_limit(app, db_conn):
    """Verify get_recent_messages enforces limit (SEC-07)."""
    from models import create_conversation, insert_chat_message, get_recent_messages

    with app.app_context():
        conv = create_conversation(user_id=1, title="Limit Test")
        for i in range(25):
            insert_chat_message(
                conversation_id=conv['id'],
                role='user' if i % 2 == 0 else 'assistant',
                content=f'Message {i}'
            )

        messages = get_recent_messages(conv['id'], limit=20)
        assert len(messages) == 20


def test_conversation_ownership(app, db_conn):
    """Verify cross-user isolation — user cannot access another user's conversation."""
    from models import create_conversation, get_conversation

    with app.app_context():
        conv = create_conversation(user_id=1, title="Private Conv")
        # User 999 should NOT be able to access user 1's conversation
        result = get_conversation(conv['id'], user_id=999)
        assert result is None, "Cross-user conversation access should return None"
