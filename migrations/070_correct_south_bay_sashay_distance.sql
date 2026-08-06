-- South Bay Sashay is RUSA permanent #00491, homologated as a 200 km brevet.
-- The route is about 133.6 miles, but its classification was accidentally stored
-- as 300 km. Linked season views prefer this value, so both 2026 editions appeared
-- in the 300K tier even though the scheduled ride rows correctly say 200 km.
UPDATE ride_plan
SET distance_km = 200
WHERE slug = '00491-south-bay-sashay'
  AND distance_km = 300;
