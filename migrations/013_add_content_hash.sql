-- migrations/013_add_content_hash.sql
-- Add nullable content_hash column for web chunk deduplication.
-- Existing WhatsApp chunks are unaffected (NULL hash value).

ALTER TABLE whatsapp_chunk
    ADD COLUMN IF NOT EXISTS content_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_whatsapp_chunk_content_hash
    ON whatsapp_chunk(content_hash)
    WHERE content_hash IS NOT NULL;
