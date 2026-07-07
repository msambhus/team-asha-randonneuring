-- 026_add_rider_notes.sql
-- Rider's own free-text notes on a ride analysis, decoupled from the map/stops.
-- Shape: {"overall": "text", "segments": {"<segment location>": "text"}}
-- Fed to the AI ride coach (services/ride_coach.py) so coaching adapts to what
-- the rider wrote. Replaces the short-lived per-stop `commentary` key that lived
-- on detected_stops (PR #434) — notes are no longer map-pin annotations.
ALTER TABLE strava_ride_analysis
    ADD COLUMN IF NOT EXISTS rider_notes JSONB;
