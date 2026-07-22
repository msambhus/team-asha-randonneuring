-- 048_brevethub_brevet_strategy.sql
-- BrevetHub Strategies-tab persistence: the rider's chosen pace card + its optional
-- community share flag, stored on the SAME rp_brevet_plan row that already holds the
-- rider's (rider_id, event_id) pacing target. No new table — the saved strategy is a
-- property of the rider/event pair, alongside target_speed_kmh and plan_data.
--
-- rp_brevet_plan gains two additive columns:
--   * strategy_pace TEXT — the picked pace card id (comfort | standard | push), or NULL
--     when the rider has never chosen one (the read-only default). Nullable, no default,
--     so every pre-existing row keeps NULL (no chosen strategy) without a backfill.
--   * is_public BOOLEAN NOT NULL DEFAULT FALSE — the community opt-in. FALSE by default
--     backfills every existing row to private, so a migration alone never publishes a
--     rider's plan; publishing is an explicit rider action through the save/share route.
--
-- Additive + idempotent + rp_*-only, in the spirit of migrations 041/045/047: two
-- ADD COLUMN IF NOT EXISTS statements plus a guarded partial index for the club-scoped
-- community read. It never drops a table, a column, or a constraint, so it is
-- non-destructive and safe to re-apply. It references only the rp_brevet_plan table, so
-- applying it can never alter a parent-app table. There is no migration runner here —
-- apply it out-of-band (the PR body carries the exact SQL).

-- --------------------------------------------------------------------------- --
-- strategy_pace — the rider's chosen pace card id. NULL = none chosen yet (the
-- read-only default every legacy row keeps). Values: comfort | standard | push,
-- validated by the save route against shared/strategies.py._PACE_VARIANTS.
-- Idempotent: ADD COLUMN IF NOT EXISTS.
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_brevet_plan
    ADD COLUMN IF NOT EXISTS strategy_pace TEXT;

-- --------------------------------------------------------------------------- --
-- is_public — the community share opt-in. NOT NULL DEFAULT FALSE backfills every
-- existing row to private, so the community list only ever shows plans a rider
-- explicitly shared. Idempotent: ADD COLUMN IF NOT EXISTS.
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_brevet_plan
    ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT FALSE;

-- --------------------------------------------------------------------------- --
-- Partial index for the club-scoped community read (get_public_strategies): only
-- the shared rows are indexed, keeping it small. Guarded IF NOT EXISTS so a re-apply
-- no-ops. Touches only rp_brevet_plan.
-- --------------------------------------------------------------------------- --
CREATE INDEX IF NOT EXISTS rp_brevet_plan_public_event_idx
    ON rp_brevet_plan (event_id)
    WHERE is_public;
