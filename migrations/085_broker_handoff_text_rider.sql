-- Widen rp_strava_broker_handoff.ta_rider_id to TEXT so a separate-database
-- consumer with non-integer account ids (e.g. Runnernet's UUID accounts) can
-- broker a Strava connect through BrevetHub. Team Asha's integer ids serialize to
-- text unchanged, and the row is transient (<= BROKER_HANDOFF_TTL, 5 min), so no
-- historical data is affected. Idempotent: re-running on an already-TEXT column is
-- a no-op ALTER.

ALTER TABLE rp_strava_broker_handoff
    ALTER COLUMN ta_rider_id TYPE TEXT USING ta_rider_id::text;
