-- 025 — Persist LLM ride-coaching narrative on the ride analysis cache.
--
-- services/ride_coach.generate_ride_coaching() produces a per-segment +
-- overall coaching narrative for a completed ride. The in-memory
-- content-fingerprint cache in that module is the primary mechanism, but this
-- nullable JSONB column lets us optionally persist the generated narrative
-- alongside the existing stream analysis so it survives process restarts and
-- can be shown without re-calling OpenAI.
--
-- The intended lookup key is match_id (the strava_ride_match row that the
-- narrative was generated for) — strava_ride_analysis already keys 1:1 on
-- match_id (see migration 009), so no new key is needed. Idempotent so it can
-- be re-run safely.

ALTER TABLE strava_ride_analysis
  ADD COLUMN IF NOT EXISTS llm_narrative JSONB;

COMMENT ON COLUMN strava_ride_analysis.llm_narrative
  IS 'Optional cached LLM ride-coaching narrative {per_segment, overall} from services/ride_coach.py, keyed 1:1 on match_id';
