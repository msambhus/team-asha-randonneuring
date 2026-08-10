-- 083_brevethub_rusa_membership_cache.sql
-- Cache RUSA.org membership expiry on rp_rider (scraped via member search).

ALTER TABLE rp_rider ADD COLUMN IF NOT EXISTS rusa_membership_expires DATE;
ALTER TABLE rp_rider ADD COLUMN IF NOT EXISTS rusa_membership_checked_at TIMESTAMPTZ;
