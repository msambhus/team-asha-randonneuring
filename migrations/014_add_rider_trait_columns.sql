-- Migration 014: Add rider-specific trait columns
-- Adds speed, power, group category, mind games, and social style fields

ALTER TABLE personality_profile ADD COLUMN IF NOT EXISTS riding_speed VARCHAR(20);
ALTER TABLE personality_profile ADD CONSTRAINT personality_profile_riding_speed_check
    CHECK (riding_speed IN ('slow', 'moderate', 'fast', 'very-fast'));

ALTER TABLE personality_profile ADD COLUMN IF NOT EXISTS power_level VARCHAR(20);
ALTER TABLE personality_profile ADD CONSTRAINT personality_profile_power_level_check
    CHECK (power_level IN ('low', 'moderate', 'strong', 'elite'));

ALTER TABLE personality_profile ADD COLUMN IF NOT EXISTS group_category VARCHAR(5);
ALTER TABLE personality_profile ADD CONSTRAINT personality_profile_group_category_check
    CHECK (group_category IN ('A', 'B', 'C'));

ALTER TABLE personality_profile ADD COLUMN IF NOT EXISTS mind_games VARCHAR(20);
ALTER TABLE personality_profile ADD CONSTRAINT personality_profile_mind_games_check
    CHECK (mind_games IN ('none', 'subtle', 'moderate', 'expert'));

ALTER TABLE personality_profile ADD COLUMN IF NOT EXISTS social_style VARCHAR(20);
ALTER TABLE personality_profile ADD CONSTRAINT personality_profile_social_style_check
    CHECK (social_style IN ('quiet', 'social', 'leader', 'entertainer'));
