-- 039_brevethub_brevet_plan.sql
-- BrevetHub M8 (ride planning / pacing schedule): a rider's saved pacing target
-- per brevet.
--
-- rp_brevet_plan stores ONE plan per (rider, brevet): the rider's chosen target
-- average speed and/or finish time, plus the server-computed pacing schedule
-- (rp_brevet_plan.plan_data JSONB — cumulative distance, arrival time, time bank vs
-- the ACP cutoff, and average speed per stop). The schedule is always recomputed
-- server-side from the reused shared/pacing.py engine on save, never trusted from
-- the client. Guests can compute a schedule on the fly (GET /plan/<event_id>) but
-- only a signed-in rider can persist one here.
--
-- This is a NEW, correctly-scoped table — deliberately NOT the rp_ride_plan shell
-- from migration 033, which is FK'd to rp_ride (a live ride), whereas a brevet plan
-- is keyed to a cached calendar brevet (rp_brevet_event). UNIQUE(rider_id, event_id)
-- makes the save an idempotent upsert (one plan per rider per brevet).
--
-- Strictly additive + idempotent + rp_*-only, exactly like migrations 033/035/036/
-- 037/038: one guarded table creation plus guarded indexes, referencing only rp_*
-- tables (every statement carries IF NOT EXISTS). Applying (or re-applying) it
-- cannot alter any Team Asha table; old code that predates the table simply ignores
-- it, so it is safe to apply ahead of the code deploy.

-- --------------------------------------------------------------------------- --
-- rp_brevet_plan — a rider's saved pacing target + computed schedule per brevet.
--
-- target_speed_kmh / target_finish_min are the rider's chosen inputs (either may
-- be NULL — the save records whichever the rider picked). plan_data holds the
-- server-computed per-stop schedule so the dashboard can re-render it without
-- recomputing. Both FKs reference rp_* tables only.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS rp_brevet_plan (
    id                 SERIAL PRIMARY KEY,
    rider_id           INTEGER NOT NULL REFERENCES rp_rider(id),
    event_id           INTEGER NOT NULL REFERENCES rp_brevet_event(id),
    target_speed_kmh   NUMERIC,                          -- rider's chosen avg speed (km/h), nullable
    target_finish_min  INTEGER,                          -- rider's chosen finish time (minutes), nullable
    plan_data          JSONB,                            -- server-computed per-stop schedule
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (rider_id, event_id)
);

CREATE INDEX IF NOT EXISTS rp_brevet_plan_rider_idx ON rp_brevet_plan (rider_id);
CREATE INDEX IF NOT EXISTS rp_brevet_plan_event_idx ON rp_brevet_plan (event_id);
