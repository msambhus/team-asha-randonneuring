-- Migration 012: Personality extraction fields and evidence table
-- Phase 8: Personality Extraction
--
-- Adds 3 new columns to personality_profile, relaxes extraction_source CHECK to include 'merged',
-- changes UNIQUE constraint to (rider_id, profile_type, extraction_source) to allow per-source rows,
-- and creates personality_trait_evidence table for storing supporting quotes.
--
-- Idempotent: uses ADD COLUMN IF NOT EXISTS and DROP CONSTRAINT IF EXISTS patterns

-- ==========================================================================
-- 1. Add missing extraction-model columns to personality_profile
-- ==========================================================================

ALTER TABLE personality_profile
    ADD COLUMN IF NOT EXISTS response_length_tendency VARCHAR(10)
        CHECK (response_length_tendency IN ('brief', 'moderate', 'verbose'));

ALTER TABLE personality_profile
    ADD COLUMN IF NOT EXISTS question_asking_behavior VARCHAR(15)
        CHECK (question_asking_behavior IN ('rarely', 'sometimes', 'frequently'));

ALTER TABLE personality_profile
    ADD COLUMN IF NOT EXISTS domain_bias VARCHAR(100);

-- ==========================================================================
-- 2. Fix extraction_source CHECK to add 'merged'
--    The original CHECK only allowed ('whatsapp', 'blog', 'manual').
--    Extraction scripts need 'merged' for the post-merge combined profile.
-- ==========================================================================

ALTER TABLE personality_profile
    DROP CONSTRAINT IF EXISTS personality_profile_extraction_source_check;

ALTER TABLE personality_profile
    ADD CONSTRAINT personality_profile_extraction_source_check
        CHECK (extraction_source IN ('whatsapp', 'blog', 'manual', 'merged'));

-- ==========================================================================
-- 3. Relax UNIQUE constraint from (rider_id, profile_type) to
--    (rider_id, profile_type, extraction_source).
--    Extraction needs separate rows per source (whatsapp + blog) so the merge
--    script can read both independently before writing the 'merged' row.
-- ==========================================================================

ALTER TABLE personality_profile
    DROP CONSTRAINT IF EXISTS personality_profile_rider_id_profile_type_key;

ALTER TABLE personality_profile
    ADD CONSTRAINT personality_profile_rider_id_profile_type_source_key
        UNIQUE (rider_id, profile_type, extraction_source);

-- ==========================================================================
-- 4. Create personality_trait_evidence table (EXTR-04)
--    Stores 3-5 supporting source quotes per trait per rider per extraction run.
-- ==========================================================================

CREATE TABLE IF NOT EXISTS personality_trait_evidence (
    id SERIAL PRIMARY KEY,
    rider_id INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    trait_name VARCHAR(50) NOT NULL,
    source_quote TEXT NOT NULL,
    extraction_source VARCHAR(10) NOT NULL
        CHECK (extraction_source IN ('whatsapp', 'blog', 'merged')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trait_evidence_rider
    ON personality_trait_evidence(rider_id);

CREATE INDEX IF NOT EXISTS idx_trait_evidence_rider_trait
    ON personality_trait_evidence(rider_id, trait_name);
