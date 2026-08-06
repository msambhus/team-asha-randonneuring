-- Coulee Challenge starts at 6:00 AM in Minnesota (America/Chicago).
-- It was initially stored as 04:00 after an incorrect Pacific-to-Central
-- conversion even though ride start_time values are event-local wall times.
UPDATE ride
SET start_time = '06:00'
WHERE id = 194
  AND name = 'Coulee Challenge'
  AND start_time = '04:00';

UPDATE ride_plan
SET start_time = '06:00'
WHERE slug = 'coulee-challenge'
  AND start_time = '04:00';
