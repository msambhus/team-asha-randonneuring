-- Chat persistence tables for Phase 1
-- Run in Supabase SQL editor

CREATE TABLE IF NOT EXISTS conversation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    title TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_active_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_user ON conversation(user_id, last_active_at DESC);

CREATE TABLE IF NOT EXISTS chat_message (
    id SERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_message_conversation ON chat_message(conversation_id, created_at);

-- Read-only role for chat queries (SEC-01)
-- May fail on Supabase free tier — application-level enforcement (SEC-02, SEC-03) is the primary defense
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'chat_readonly') THEN
        CREATE ROLE chat_readonly NOLOGIN;
    END IF;
END
$$;
GRANT SELECT ON conversation, chat_message TO chat_readonly;
