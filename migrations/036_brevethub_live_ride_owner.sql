-- 036_brevethub_live_ride_owner.sql
-- BrevetHub M3 (public/guest live-ride browse): add the ride-owner column the
-- rider-posts-own-position path and the create/flag-public path gate on.
--
-- rp_ride was created (migration 033) without a rider owner because rides were a
-- read-only shell then. M3 lets a logged-in rider create a ride and post live
-- position breadcrumbs for it, so a ride now needs an owner to enforce
-- "you may only post positions for YOUR OWN ride" (rp_ride.rider_id = session
-- rider). rp_live_position already carries ride_id/rider_id/lat/lng/recorded_at
-- plus the (ride_id, recorded_at) index, so it needs no change.
--
-- Strictly additive + idempotent + rp_*-only, exactly like migrations 033/035:
-- a single nullable column on rp_ride plus a supporting index, both guarded by
-- IF NOT EXISTS. Applying (or re-applying) it cannot alter any Team Asha table.
-- Old code that predates this column simply ignores it; a rollback leaves an
-- unused nullable column, so it is safe to apply ahead of the code deploy.

-- --------------------------------------------------------------------------- --
-- rp_ride.rider_id — the rider who owns (created) the ride. Nullable so the
-- existing read-only rides created before M3 keep working; the position-POST and
-- create paths set/require it.
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_ride ADD COLUMN IF NOT EXISTS rider_id INTEGER REFERENCES rp_rider(id);

-- Owner lookups (list a rider's own rides on the create/flag page) filter by
-- rider_id, so index it.
CREATE INDEX IF NOT EXISTS rp_ride_rider_idx ON rp_ride (rider_id);
