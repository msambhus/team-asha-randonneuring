-- Migration 015: Support multiple bikes per rider + new accessory columns
-- Drops UNIQUE on rider_id, adds label column, new UNIQUE on (rider_id, label)
-- Adds tail_lights, cold_weather_gear, indoor_trainer, accessories columns

ALTER TABLE gear_preference DROP CONSTRAINT IF EXISTS gear_preference_rider_id_key;

ALTER TABLE gear_preference ADD COLUMN IF NOT EXISTS label VARCHAR(50) DEFAULT 'Primary';

ALTER TABLE gear_preference ADD CONSTRAINT gear_preference_rider_label_key
    UNIQUE (rider_id, label);

ALTER TABLE gear_preference ADD COLUMN IF NOT EXISTS tail_lights TEXT;
ALTER TABLE gear_preference ADD COLUMN IF NOT EXISTS cold_weather_gear TEXT;
ALTER TABLE gear_preference ADD COLUMN IF NOT EXISTS indoor_trainer TEXT;
ALTER TABLE gear_preference ADD COLUMN IF NOT EXISTS accessories TEXT;
