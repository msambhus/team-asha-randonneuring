-- Migration 019: Add is_admin column to app_user
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;

-- Set existing admins based on current hardcoded list
UPDATE app_user SET is_admin = TRUE
WHERE rider_id IN (
    SELECT id FROM rider WHERE lower(first_name) IN ('sriharsha', 'venkatesh', 'mihir')
);
