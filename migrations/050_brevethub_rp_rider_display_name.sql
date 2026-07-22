-- 050_brevethub_rp_rider_display_name.sql
-- BrevetHub Radial live view: a real per-rider DISPLAY NAME for the public roster.
--
-- The public, guest-reachable roster (GET /live/<id>/roster.json) shows every
-- opted-in rider BY NAME. Until now BrevetHub had no name column and the member
-- poll fell back to split_part(email,'@',1) — the EMAIL LOCAL-PART — which is PII
-- and must never appear in a world-viewable payload. This adds a dedicated
-- display_name so the public output uses a real, chosen name and NEVER the email.
--
-- Strictly additive + idempotent + rp_*-only (like migrations 033/044): a single
-- nullable column. Old code ignores it; the model layer COALESCEs a non-email
-- fallback ('Rider') when it is null, so nothing depends on a backfill. Populate it
-- from signup when a name field is added to the profile flow; it must NEVER be set
-- to the email local-part.

ALTER TABLE rp_rider ADD COLUMN IF NOT EXISTS display_name TEXT;
