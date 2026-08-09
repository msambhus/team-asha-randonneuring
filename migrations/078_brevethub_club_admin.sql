-- 078_brevethub_club_admin.sql
-- Per-club admin credential table for BrevetHub.
--
-- Replaces the single global ADMIN_PASSWORD env-var approach with named,
-- club-scoped admin accounts. Each row is one admin user tied to exactly one
-- club (username UNIQUE, club_id NOT NULL). Multiple rows per club are allowed
-- so a club can have several admins; one admin cannot span multiple clubs.
--
-- Passwords are stored as werkzeug.security bcrypt hashes (never plaintext).
-- The global ADMIN_PASSWORD env var is kept as a super-admin backdoor for
-- bootstrapping and platform-level operations (sets club_id = NULL in session).
--
-- Strictly additive + idempotent + rp_*-only. Safe to apply ahead of code.

CREATE TABLE IF NOT EXISTS rp_club_admin (
    id            SERIAL PRIMARY KEY,
    club_id       INTEGER NOT NULL REFERENCES rp_club(id),
    username      TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ,
    UNIQUE (username)
);

CREATE INDEX IF NOT EXISTS rp_club_admin_club_idx     ON rp_club_admin (club_id);
CREATE INDEX IF NOT EXISTS rp_club_admin_username_idx ON rp_club_admin (username);
