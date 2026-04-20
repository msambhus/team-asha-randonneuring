-- Migration 018: Consolidate ride_plan duplicate fields into ride table
-- ride_plan becomes purely a route template (stops + computed stats)
-- Event-specific data (date, time, location, route URLs, limits) lives on ride

-- Step 1: Add new columns to ride table
ALTER TABLE ride ADD COLUMN IF NOT EXISTS start_time TEXT;
ALTER TABLE ride ADD COLUMN IF NOT EXISTS rwgps_url_team TEXT;

-- Step 2: Backfill ride rows from their linked ride_plan

-- start_time
UPDATE ride r
SET start_time = rp.start_time
FROM ride_plan rp
WHERE r.ride_plan_id = rp.id
  AND (r.start_time IS NULL OR r.start_time = '')
  AND rp.start_time IS NOT NULL;

-- rwgps_url_team
UPDATE ride r
SET rwgps_url_team = rp.rwgps_url_team
FROM ride_plan rp
WHERE r.ride_plan_id = rp.id
  AND rp.rwgps_url_team IS NOT NULL;

-- time_limit_hours: backfill from cutoff_hours where ride doesn't have one
UPDATE ride r
SET time_limit_hours = rp.cutoff_hours
FROM ride_plan rp
WHERE r.ride_plan_id = rp.id
  AND r.time_limit_hours IS NULL
  AND rp.cutoff_hours IS NOT NULL;

-- rwgps_url: backfill where ride doesn't have one but plan does
UPDATE ride r
SET rwgps_url = rp.rwgps_url
FROM ride_plan rp
WHERE r.ride_plan_id = rp.id
  AND r.rwgps_url IS NULL
  AND rp.rwgps_url IS NOT NULL;

-- Step 3: Mark ride_plan columns as deprecated (DO NOT drop - keep for rollback)
COMMENT ON COLUMN ride_plan.rwgps_url IS 'DEPRECATED: use ride.rwgps_url';
COMMENT ON COLUMN ride_plan.rwgps_url_team IS 'DEPRECATED: use ride.rwgps_url_team';
COMMENT ON COLUMN ride_plan.distance_km IS 'DEPRECATED: use ride.distance_km';
COMMENT ON COLUMN ride_plan.cutoff_hours IS 'DEPRECATED: use ride.time_limit_hours';
COMMENT ON COLUMN ride_plan.start_time IS 'DEPRECATED: use ride.start_time';
COMMENT ON COLUMN ride_plan.rwgps_route_id IS 'DEPRECATED: derive from ride.rwgps_url';
