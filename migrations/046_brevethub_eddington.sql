-- 046_brevethub_eddington.sql
-- BrevetHub cycling Eddington number: the per-rider cache columns the profile and
-- same-club public rider profile read. E is the largest number E such that the
-- rider has ridden at least E units (km or miles) on at least E different days,
-- computed from the rider own Strava history by the shared engine in
-- shared/eddington.py (reused from the parent web app, never forked).
--
-- The value is precomputed OFF the request path (on Strava connect and by the daily
-- /cron/refresh-eddington cron) and cached here, so a PUBLIC profile viewer — who
-- holds no token for the viewed rider — reads only the cached scalar and issues
-- zero Strava calls. It lives on rp_rider (the career-stat owner row both profile
-- views already load) so both surfaces get the value without a second query.
--
-- Strictly additive + idempotent + rp_*-only, exactly like migrations 037/042/045:
-- three ADD COLUMN IF NOT EXISTS statements referencing only the rp_rider table.
-- Applying (or re-applying) it cannot alter any parent-app table; old code that
-- predates the columns simply ignores them, so it is safe to apply before or after
-- the code deploy. There is no migration runner here — apply it out-of-band (the PR
-- body carries the exact SQL).

-- --------------------------------------------------------------------------- --
-- eddington_miles / eddington_km — the cached cycling Eddington number in each
-- unit (both stored, cheap; BrevetHub displays km per the career = KMs
-- convention). NULL until the first compute (on-connect or the daily cron), which
-- the profile renders as a graceful "not computed yet" state, never a fabricated 0.
-- eddington_calculated_at — when the cache was last written, for staleness /
-- observability. NULL until the first compute.
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_rider ADD COLUMN IF NOT EXISTS eddington_miles INTEGER;
ALTER TABLE rp_rider ADD COLUMN IF NOT EXISTS eddington_km INTEGER;
ALTER TABLE rp_rider ADD COLUMN IF NOT EXISTS eddington_calculated_at TIMESTAMPTZ;
