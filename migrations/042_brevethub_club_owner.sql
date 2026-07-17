-- 042_brevethub_club_owner.sql
-- BrevetHub club ownership: who may generate real RWGPS ride plans for a club.
--
-- Adds rp_club.owner_rider_id — a nullable FK to rp_rider — so the club-admin route
-- (POST /admin/plan/generate) can gate real-plan generation on ownership: only the
-- rider who owns a club can paste an RWGPS URL and persist a plan. Nullable because
-- most seeded clubs have no owner yet (ownership is assigned out-of-band for v1),
-- and a NULL owner simply means "no one can admin this club" — a safe closed default.
--
-- Strictly additive + idempotent + rp_*-only, exactly like migrations
-- 033/035/036/037/038/039/040/041: a single guarded column addition plus a guarded index,
-- referencing only rp_* tables. Applying (or re-applying) it cannot alter any Team
-- Asha table; code that predates the column simply ignores it, so it is safe to
-- apply ahead of the code deploy.

-- --------------------------------------------------------------------------- --
-- rp_club.owner_rider_id — the rider who owns (administers) this club.
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_club
    ADD COLUMN IF NOT EXISTS owner_rider_id INTEGER REFERENCES rp_rider(id);

CREATE INDEX IF NOT EXISTS rp_club_owner_idx ON rp_club (owner_rider_id);
