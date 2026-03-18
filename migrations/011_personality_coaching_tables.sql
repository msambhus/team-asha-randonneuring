-- Migration 011: Personality profiles and coaching configuration tables
-- Phase 7: Data Foundation for Personality-Driven Coaching
--
-- Creates 4 tables: personality_profile, gear_preference, coach_assignment, coaching_guardrail
-- Plus rule_version auto-increment trigger on coaching_guardrail
--
-- Idempotent: uses CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS

-- ==========================================================================
-- Table 1: personality_profile
-- Stores structured personality traits for coaches and riders.
-- Uses VARCHAR with CHECK constraints (not TEXT blobs) for prompt injection defense (OWASP LLM01).
-- ==========================================================================

CREATE TABLE IF NOT EXISTS personality_profile (
    id SERIAL PRIMARY KEY,
    rider_id INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    profile_type VARCHAR(10) NOT NULL CHECK (profile_type IN ('coach', 'rider')),

    -- Coach-specific typed fields (PROF-02)
    tone VARCHAR(20) CHECK (tone IN ('direct', 'warm', 'playful', 'serious', 'sarcastic')),
    humor_type VARCHAR(20) CHECK (humor_type IN ('none', 'dry', 'sarcastic', 'gentle', 'self-deprecating')),
    directness VARCHAR(10) CHECK (directness IN ('low', 'medium', 'high')),
    signature_phrases TEXT[],
    topic_biases TEXT[],
    topics_allowed TEXT[],

    -- Rider-specific typed fields (PROF-03)
    preferred_formality VARCHAR(10) CHECK (preferred_formality IN ('casual', 'mixed', 'formal')),
    humor_sensitivity VARCHAR(10) CHECK (humor_sensitivity IN ('low', 'medium', 'high')),
    encouragement_style VARCHAR(20) CHECK (encouragement_style IN ('data-driven', 'emotional', 'balanced', 'tough-love')),
    technical_depth VARCHAR(10) CHECK (technical_depth IN ('beginner', 'intermediate', 'expert')),

    -- Extraction metadata (PROF-04)
    extraction_source VARCHAR(10) CHECK (extraction_source IN ('whatsapp', 'blog', 'manual')),
    extraction_date DATE,
    source_message_count INTEGER,
    extraction_confidence VARCHAR(10) CHECK (extraction_confidence IN ('high', 'medium', 'low')),

    -- Audit (PROF-05)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by TEXT,
    deleted_at TIMESTAMPTZ,

    UNIQUE (rider_id, profile_type)
);

CREATE INDEX IF NOT EXISTS idx_personality_profile_rider ON personality_profile(rider_id);
CREATE INDEX IF NOT EXISTS idx_personality_profile_type ON personality_profile(profile_type);

-- ==========================================================================
-- Table 2: gear_preference
-- Stores bike and gear details per rider.
-- ==========================================================================

CREATE TABLE IF NOT EXISTS gear_preference (
    id SERIAL PRIMARY KEY,
    rider_id INTEGER NOT NULL UNIQUE REFERENCES rider(id) ON DELETE CASCADE,

    -- Bike details
    bike_make VARCHAR(100),
    bike_model VARCHAR(100),
    bike_year INTEGER,
    bike_material VARCHAR(20) CHECK (bike_material IN ('aluminum', 'steel', 'titanium', 'carbon', 'other')),

    -- Categories
    wheels_tires TEXT,
    lighting TEXT,
    bags TEXT,
    navigation TEXT,
    kit TEXT,

    -- Value orientation
    value_orientation VARCHAR(20) CHECK (value_orientation IN ('budget', 'mid-range', 'premium', 'buy-once-buy-right')),

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by TEXT,
    deleted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_gear_preference_rider ON gear_preference(rider_id);

-- ==========================================================================
-- Table 3: coach_assignment
-- Maps coaches to topic domains for routing.
-- ==========================================================================

CREATE TABLE IF NOT EXISTS coach_assignment (
    id SERIAL PRIMARY KEY,
    coach_rider_id INTEGER NOT NULL REFERENCES rider(id) ON DELETE CASCADE,
    topic_domain VARCHAR(50) NOT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by TEXT,
    deleted_at TIMESTAMPTZ,

    UNIQUE (coach_rider_id, topic_domain)
);

CREATE INDEX IF NOT EXISTS idx_coach_assignment_coach ON coach_assignment(coach_rider_id);
CREATE INDEX IF NOT EXISTS idx_coach_assignment_domain ON coach_assignment(topic_domain);
CREATE INDEX IF NOT EXISTS idx_coach_assignment_active ON coach_assignment(is_active) WHERE is_active = TRUE;

-- ==========================================================================
-- Table 4: coaching_guardrail
-- Stores guardrail rules for coaching behavior constraints.
-- ==========================================================================

CREATE TABLE IF NOT EXISTS coaching_guardrail (
    id SERIAL PRIMARY KEY,
    rule_type VARCHAR(30) NOT NULL CHECK (rule_type IN ('topic_block', 'tone_limit', 'escalation', 'scope')),
    rule_value TEXT NOT NULL,
    applies_to VARCHAR(10) DEFAULT 'all' CHECK (applies_to IN ('all', 'shriram', 'venki')),
    is_active BOOLEAN DEFAULT TRUE,
    rule_version INTEGER DEFAULT 1,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by TEXT,
    deleted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_coaching_guardrail_type ON coaching_guardrail(rule_type);
CREATE INDEX IF NOT EXISTS idx_coaching_guardrail_active ON coaching_guardrail(is_active) WHERE is_active = TRUE;

-- ==========================================================================
-- Trigger: Auto-increment rule_version on coaching_guardrail UPDATE (GUARD-06)
-- ==========================================================================

CREATE OR REPLACE FUNCTION increment_guardrail_rule_version()
RETURNS TRIGGER AS $$
BEGIN
    NEW.rule_version = OLD.rule_version + 1;
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_increment_guardrail_rule_version ON coaching_guardrail;
CREATE TRIGGER trigger_increment_guardrail_rule_version
    BEFORE UPDATE ON coaching_guardrail
    FOR EACH ROW
    EXECUTE FUNCTION increment_guardrail_rule_version();
