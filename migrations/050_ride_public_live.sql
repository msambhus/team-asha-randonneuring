-- 050_ride_public_live.sql
-- Team Asha Radial live view: a per-ride PUBLIC-LIVE flag for the guest roster.
--
-- Team Asha's live map is gated by a per-ride INVITE CODE today; it has no notion
-- of a world-viewable ride. The new public roster (GET /ride/<id>/live/roster.json)
-- needs an explicit owner opt-in before it is served to anonymous guests without a
-- code. This adds that flag. When FALSE (the default), the roster falls back to the
-- existing invite-code / member gate, so nothing regresses for invite-only rides;
-- when an owner sets it TRUE, that ride's roster becomes guest-public (opted-in
-- riders only — the location-sharing consent filter is unchanged).
--
-- Strictly additive + idempotent: a single boolean defaulted FALSE. get_ride_by_id
-- selects ri.*, so the column is picked up automatically once applied and reads as
-- absent/False beforehand — no query change and no forced backfill.

ALTER TABLE ride ADD COLUMN IF NOT EXISTS is_public_live BOOLEAN NOT NULL DEFAULT FALSE;
