-- Bounded, normalized Garmin training/recovery metrics for private rider use.
ALTER TABLE garmin_performance_snapshot
    ADD COLUMN IF NOT EXISTS readiness_level TEXT,
    ADD COLUMN IF NOT EXISTS readiness_feedback TEXT,
    ADD COLUMN IF NOT EXISTS recovery_time_minutes NUMERIC,
    ADD COLUMN IF NOT EXISTS sleep_factor_percent NUMERIC,
    ADD COLUMN IF NOT EXISTS acwr_factor_percent NUMERIC,
    ADD COLUMN IF NOT EXISTS hrv_factor_percent NUMERIC,
    ADD COLUMN IF NOT EXISTS endurance_score NUMERIC,
    ADD COLUMN IF NOT EXISTS acute_training_load NUMERIC,
    ADD COLUMN IF NOT EXISTS load_level_trend TEXT;

ALTER TABLE garmin_performance_snapshot ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE garmin_performance_snapshot FROM anon, authenticated;
