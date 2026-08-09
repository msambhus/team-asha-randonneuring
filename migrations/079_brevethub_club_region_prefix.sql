-- 079_brevethub_club_region_prefix.sql
-- Add region_prefix to rp_club for matching RUSA feed events to clubs.
--
-- RUSA feed events have club_id = NULL but carry a region string like
-- "CA: San Francisco". Storing the matching prefix on rp_club lets the
-- admin layer filter events to the right club without populating club_id
-- on every event row (which would require ongoing maintenance as the feed
-- changes). A club's region_prefix is the exact string that the RUSA feed
-- uses as the region for that club's events.
--
-- Strictly additive + idempotent + rp_*-only.

ALTER TABLE rp_club ADD COLUMN IF NOT EXISTS region_prefix TEXT;

CREATE INDEX IF NOT EXISTS rp_club_region_prefix_idx ON rp_club (region_prefix);

-- Seed known region prefixes for the clubs that BrevetHub actively serves.
UPDATE rp_club SET region_prefix = 'AK: Anchorage'            WHERE rusa_club_id = 'AKR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'AL: Birmingham'           WHERE rusa_club_id = 'ALR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'AZ: Phoenix'              WHERE rusa_club_id = 'AZB'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'CA: Davis'                WHERE rusa_club_id = 'DBC'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'CA: Humboldt'             WHERE rusa_club_id = 'SCR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'CA: Los Angeles'          WHERE rusa_club_id = 'LAR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'CA: San Diego'            WHERE rusa_club_id = 'SDR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'CA: San Francisco'        WHERE rusa_club_id = 'SFR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'CA: San Luis Obispo'      WHERE rusa_club_id = 'CCR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'CA: Santa Cruz'           WHERE rusa_club_id = 'SCR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'CA: Santa Rosa'           WHERE rusa_club_id = 'SRCC' AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'CO: Boulder'              WHERE rusa_club_id = 'ROCK' AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'GA: Atlanta'              WHERE rusa_club_id = 'GAR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'HI: Maui'                 WHERE rusa_club_id = 'HIR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'IA: Central'              WHERE rusa_club_id = 'IAR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'IL: Chicagoland'          WHERE rusa_club_id = 'CIR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'KY: Louisville'           WHERE rusa_club_id = 'KYR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'MA: Boston'               WHERE rusa_club_id = 'BOS'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'MD: Capital Region'       WHERE rusa_club_id = 'DCR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'MI: Detroit'              WHERE rusa_club_id = 'DTR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'MN: Twin Cities / Rochester' WHERE rusa_club_id = 'MNR' AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'MO: Kansas City'          WHERE rusa_club_id = 'KSR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'MO: St. Louis'            WHERE rusa_club_id = 'MOR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'NJ: Princeton'            WHERE rusa_club_id = 'NJR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'NM: Albuquerque'          WHERE rusa_club_id = 'NMR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'NV: Las Vegas'            WHERE rusa_club_id = 'NVR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'NY: New York City'        WHERE rusa_club_id = 'NYCR' AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'OH: Columbus'             WHERE rusa_club_id = 'OHR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'OK: Oklahoma City'        WHERE rusa_club_id = 'OKR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'OR: Portland'             WHERE rusa_club_id = 'ORR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'TX: Austin'               WHERE rusa_club_id = 'ATX'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'TX: Dallas'               WHERE rusa_club_id = 'TXR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'TX: Houston'              WHERE rusa_club_id = 'HCR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'UT: Salt Lake City'       WHERE rusa_club_id = 'UTR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'WA: Seattle'              WHERE rusa_club_id = 'SIR'  AND region_prefix IS NULL;
UPDATE rp_club SET region_prefix = 'WA: Eastern'              WHERE rusa_club_id = 'WAE'  AND region_prefix IS NULL;
