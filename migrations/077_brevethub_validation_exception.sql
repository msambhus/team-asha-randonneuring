-- Explicit organizer/rider exception for supplemental evidence on an official finish.
ALTER TABLE rp_event_signup
    ADD COLUMN IF NOT EXISTS evidence_submission_allowed BOOLEAN NOT NULL DEFAULT FALSE;
