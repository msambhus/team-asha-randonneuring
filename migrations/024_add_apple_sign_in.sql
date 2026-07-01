-- 024 — Sign in with Apple support for the mobile app (App Store Guideline 4.8).
--
-- app_user was Google-only: google_id was NOT NULL. To also support Apple, add a
-- nullable apple_sub (the stable Apple user id from the identity token's `sub`
-- claim) and relax google_id so an Apple-only account doesn't need a Google id.
-- Both id columns are unique (Postgres treats NULLs as distinct, so many
-- Google-only rows can share a NULL apple_sub and vice-versa).

ALTER TABLE app_user ADD COLUMN IF NOT EXISTS apple_sub VARCHAR(255);
ALTER TABLE app_user ALTER COLUMN google_id DROP NOT NULL;

-- Unique index (not a table constraint) so re-running is idempotent.
CREATE UNIQUE INDEX IF NOT EXISTS app_user_apple_sub_key ON app_user (apple_sub);
