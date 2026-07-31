-- Normalized electronic-shifting telemetry decoded from owner-scoped Garmin
-- Activity FIT downloads. Original FIT binaries are not retained.
ALTER TABLE garmin_activity_detail
  ADD COLUMN IF NOT EXISTS gear_events JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS gear_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS gear_synced_at TIMESTAMPTZ;

COMMENT ON COLUMN garmin_activity_detail.gear_events IS
  'Private timestamped electronic shift events decoded from the original Garmin FIT activity';
COMMENT ON COLUMN garmin_activity_detail.gear_summary IS
  'Private derived time-in-gear and power-in-gear aggregates; original FIT is discarded';

ALTER TABLE garmin_activity_detail ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE garmin_activity_detail FROM anon, authenticated;
