-- WhatsApp knowledge base table for pgvector embeddings
-- Run in Supabase SQL editor. Requires pgvector extension enabled.
-- Enable via: Dashboard -> Database -> Extensions -> search 'vector' -> Enable
-- Or run: CREATE EXTENSION IF NOT EXISTS vector;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS whatsapp_chunk (
    id              SERIAL PRIMARY KEY,
    source          TEXT NOT NULL,               -- e.g. 'fresh_start' or 'brevets'
    chunk_start     TIMESTAMPTZ NOT NULL,        -- timestamp of first message in chunk
    chunk_end       TIMESTAMPTZ NOT NULL,        -- timestamp of last message in chunk
    senders         TEXT[],                      -- array of sender names in chunk
    message_count   INTEGER NOT NULL,
    content         TEXT NOT NULL,               -- formatted chunk text for display/embedding
    embedding       vector(1536),               -- text-embedding-3-small output
    created_at      TIMESTAMPTZ DEFAULT NOW(),

    -- Unique constraint for idempotent re-import (WA-06)
    UNIQUE (source, chunk_start, chunk_end)
);

-- HNSW index for fast cosine similarity search
-- HNSW preferred over IVFFlat: works on empty table, better recall at our scale (~22k rows)
CREATE INDEX IF NOT EXISTS idx_whatsapp_chunk_embedding
    ON whatsapp_chunk USING hnsw (embedding vector_cosine_ops)
    WITH (m=16, ef_construction=64);

-- Source index for incremental append queries (MAX(chunk_end) WHERE source = ...)
CREATE INDEX IF NOT EXISTS idx_whatsapp_chunk_source
    ON whatsapp_chunk(source);
