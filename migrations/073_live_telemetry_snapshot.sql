-- Shared, server-computed live telemetry for web and native clients.
-- The Flask backend connects directly as the database owner. No PostgREST role
-- receives access: live payloads contain authenticated-view rider identifiers.
CREATE TABLE IF NOT EXISTS live_telemetry_snapshot (
    ride_id             INTEGER NOT NULL REFERENCES ride(id) ON DELETE CASCADE,
    plan_key            TEXT NOT NULL DEFAULT 'base',
    payload             JSONB NOT NULL,
    source_recorded_at  TIMESTAMPTZ,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ride_id, plan_key)
);

ALTER TABLE live_telemetry_snapshot ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE live_telemetry_snapshot FROM anon, authenticated;
