-- 029_unique_lower_email.sql
-- Enforce one account per email, case-insensitively. Closes a TOCTOU gap in the
-- email+password signup dup-check (028) — two concurrent signups (or a signup
-- racing a Google first-login) could otherwise create duplicate accounts for the
-- same email. Prod verified to have no case-insensitive duplicate emails before
-- this index is created. create_user_password catches the unique violation → 409.
CREATE UNIQUE INDEX IF NOT EXISTS app_user_lower_email_uidx
    ON app_user (lower(email));
