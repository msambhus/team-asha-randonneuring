-- 047_brevethub_brevet_route_plan_variant.sql
-- BrevetHub conservative/aggressive ride-plan variants: the `variant` column and the
-- re-keyed uniqueness that lets TWO stored plans (a realistic "conservative" pace and
-- a faster "aggressive" pace) coexist per brevet.
--
-- rp_brevet_route_plan gains a `variant` TEXT column defaulting to 'conservative'
-- (so every pre-existing single-plan row becomes the conservative variant, the legacy
-- default the /plan page and live-tracking grading resolve to). The one-plan-per-event
-- UNIQUE(event_id) is swapped for UNIQUE(event_id, variant) so the two variants upsert
-- independently; the slug uniqueness is re-keyed per (event_id, variant) to match (the
-- model suffixes the slug with the variant).
--
-- Additive + idempotent + rp_*-only, in the spirit of migrations 041/045: an
-- ADD COLUMN IF NOT EXISTS, plus guarded DROP CONSTRAINT IF EXISTS / re-runnable
-- pg_constraint-checked ADD CONSTRAINT swaps (Postgres has no ADD CONSTRAINT IF NOT
-- EXISTS). It never drops a table or a column — the only DROPs are guarded constraint
-- swaps — so it is non-destructive and safe to re-apply. It references only rp_* tables,
-- so applying it can never alter a parent-app table. There is no migration runner here —
-- apply it out-of-band (the PR body carries the exact SQL).
--
-- NOTE ON CONSTRAINT NAMES: migration 041 created UNIQUE(event_id) and UNIQUE(slug) as
-- INLINE column constraints, which Postgres auto-names `rp_brevet_route_plan_event_id_key`
-- and `rp_brevet_route_plan_slug_key`. Verify the real names with `\d rp_brevet_route_plan`
-- before applying; the DROP ... IF EXISTS guards below no-op cleanly if a name differs.

-- --------------------------------------------------------------------------- --
-- variant — 'conservative' (legacy default, the graded/display default) or
-- 'aggressive' (+1.5 mph, display-only). NOT NULL DEFAULT 'conservative' backfills
-- every existing row to the conservative variant, so no event changes which plan it
-- resolves to today. Idempotent: ADD COLUMN IF NOT EXISTS.
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_brevet_route_plan
    ADD COLUMN IF NOT EXISTS variant TEXT NOT NULL DEFAULT 'conservative';

-- --------------------------------------------------------------------------- --
-- Re-key uniqueness from one-plan-per-event to one-plan-per-(event, variant).
-- Remove the old single-column UNIQUE(event_id) (guarded), then add the composite
-- inside a re-runnable DO block that checks pg_constraint first.
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_brevet_route_plan
    DROP CONSTRAINT IF EXISTS rp_brevet_route_plan_event_id_key;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'rp_brevet_route_plan_event_variant_key'
    ) THEN
        ALTER TABLE rp_brevet_route_plan
            ADD CONSTRAINT rp_brevet_route_plan_event_variant_key
            UNIQUE (event_id, variant);
    END IF;
END $$;

-- --------------------------------------------------------------------------- --
-- Slug uniqueness re-keyed per (event_id, variant) to match the model's
-- variant-suffixed slug. Remove the old global UNIQUE(slug) (guarded), then add the
-- composite inside a re-runnable DO block.
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_brevet_route_plan
    DROP CONSTRAINT IF EXISTS rp_brevet_route_plan_slug_key;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'rp_brevet_route_plan_event_variant_slug_key'
    ) THEN
        ALTER TABLE rp_brevet_route_plan
            ADD CONSTRAINT rp_brevet_route_plan_event_variant_slug_key
            UNIQUE (event_id, variant, slug);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS rp_brevet_route_plan_event_variant_idx
    ON rp_brevet_route_plan (event_id, variant);
