-- Ordered RWGPS legs for multi-day / multi-route ride plans.
CREATE TABLE IF NOT EXISTS ride_plan_leg (
    id           SERIAL PRIMARY KEY,
    ride_plan_id INTEGER NOT NULL REFERENCES ride_plan(id) ON DELETE CASCADE,
    leg_order    INTEGER NOT NULL,
    day_number   INTEGER NOT NULL DEFAULT 1,
    label        TEXT,
    rwgps_url    TEXT NOT NULL,
    created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (ride_plan_id, leg_order)
);

CREATE INDEX IF NOT EXISTS idx_ride_plan_leg_plan
    ON ride_plan_leg (ride_plan_id, leg_order);

-- Existing four-leg Coulee plan. The slug makes this safe across environments.
INSERT INTO ride_plan_leg (ride_plan_id, leg_order, day_number, label, rwgps_url)
SELECT rp.id, v.leg_order, v.day_number, v.label, v.rwgps_url
FROM ride_plan rp
CROSS JOIN (VALUES
    (1, 1, 'Day 1', 'https://ridewithgps.com/routes/56315049'),
    (2, 2, 'Day 2', 'https://ridewithgps.com/routes/55704679?privacy_code=jgevAt3gnSeSCoV7o9lerNfCOAQjPGI8'),
    (3, 3, 'Day 3', 'https://ridewithgps.com/routes/55706355?privacy_code=xP4JcB4Gvb3dM9mwzukot6tJOYFWYbs9'),
    (4, 4, 'Day 4', 'https://ridewithgps.com/routes/55691344?privacy_code=e7YP5SDwZOr5prw5VpsUJcn84r4NzgGM')
) AS v(leg_order, day_number, label, rwgps_url)
WHERE rp.slug = 'coulee-challenge'
ON CONFLICT (ride_plan_id, leg_order) DO UPDATE SET
    day_number = EXCLUDED.day_number,
    label = EXCLUDED.label,
    rwgps_url = EXCLUDED.rwgps_url;
