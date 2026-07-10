-- 031_add_email_normalized.sql
-- Canonical email for identity matching, so Gmail dot/+tag variants
-- (mihir.sambhus@ / mihirsambhus+x@ / mihirsambhus@gmail.com) resolve to ONE
-- account instead of creating duplicates. Populated on every account-create path
-- (models.create_user*) and read by models.get_user_by_normalized_email.
--
-- The backfill below MUST mirror services/email_normalize.normalize_email:
-- only gmail.com / googlemail.com get dot + '+tag' stripping (and collapse to
-- gmail.com); every other domain is just lowercased, so we never merge two
-- genuinely different addresses.
--
-- No UNIQUE constraint: pre-existing dot-variant duplicates may already exist,
-- and the lookup deliberately prefers a profile-completed row over an empty one.

ALTER TABLE app_user
    ADD COLUMN IF NOT EXISTS email_normalized VARCHAR(255);

UPDATE app_user
SET email_normalized = CASE
    WHEN position('@' in email) = 0 THEN lower(email)
    WHEN lower(split_part(email, '@', 2)) IN ('gmail.com', 'googlemail.com')
        THEN replace(regexp_replace(split_part(lower(email), '@', 1), '[+].*$', ''), '.', '')
             || '@gmail.com'
    ELSE lower(email)
END
WHERE email_normalized IS NULL;

CREATE INDEX IF NOT EXISTS app_user_email_normalized_idx
    ON app_user (email_normalized);
