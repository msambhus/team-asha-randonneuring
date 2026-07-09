-- 028_add_password_auth.sql
-- Email + password as a THIRD login option on the mobile app (alongside Google
-- and Sign in with Apple). Adds a nullable password hash to app_user; existing
-- Google/Apple accounts keep password_hash NULL and are unaffected. The hash is
-- a werkzeug PBKDF2/scrypt string (models.create_user_password); we never store
-- the plaintext password.
ALTER TABLE app_user
    ADD COLUMN IF NOT EXISTS password_hash VARCHAR;
