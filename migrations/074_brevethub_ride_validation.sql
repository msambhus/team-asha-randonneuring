-- BrevetHub-only brevet evidence validation and organizer review.
-- Evidence is intentionally private: the Flask server connects with the database
-- owner, while PostgREST's anon/authenticated roles receive no table privileges.

CREATE TABLE IF NOT EXISTS rp_validation_submission (
    id                    BIGSERIAL PRIMARY KEY,
    event_id              INTEGER NOT NULL REFERENCES rp_brevet_event(id),
    rider_id              INTEGER NOT NULL REFERENCES rp_rider(id),
    submitted_by          TEXT NOT NULL DEFAULT 'operator',
    source_type           TEXT NOT NULL,
    strava_activity_id    BIGINT,
    source_metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
    normalized_track      JSONB NOT NULL DEFAULT '[]'::jsonb,
    rider_explanation     TEXT,
    machine_decision      TEXT NOT NULL DEFAULT 'incomplete'
        CHECK (machine_decision IN ('clear', 'needs_review', 'incomplete')),
    organizer_decision    TEXT
        CHECK (organizer_decision IS NULL OR organizer_decision IN
               ('approved', 'needs_more_evidence', 'not_approved')),
    organizer_notes       TEXT,
    reviewed_at           TIMESTAMPTZ,
    reviewed_by           TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS rp_validation_submission_queue_idx
    ON rp_validation_submission (organizer_decision, created_at DESC);
CREATE INDEX IF NOT EXISTS rp_validation_submission_event_rider_idx
    ON rp_validation_submission (event_id, rider_id);

CREATE TABLE IF NOT EXISTS rp_validation_evidence (
    id                    BIGSERIAL PRIMARY KEY,
    submission_id         BIGINT NOT NULL REFERENCES rp_validation_submission(id)
                              ON DELETE CASCADE,
    evidence_kind         TEXT NOT NULL,
    control_order         INTEGER,
    control_orders        INTEGER[] NOT NULL DEFAULT '{}',
    original_filename     TEXT,
    content_type          TEXT,
    byte_size             INTEGER,
    sha256                TEXT,
    captured_at           TIMESTAMPTZ,
    description           TEXT,
    private_content       BYTEA,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS rp_validation_evidence_submission_idx
    ON rp_validation_evidence (submission_id, control_order, id);
CREATE INDEX IF NOT EXISTS rp_validation_evidence_hash_idx
    ON rp_validation_evidence (sha256) WHERE sha256 IS NOT NULL;

CREATE TABLE IF NOT EXISTS rp_validation_check (
    id                    BIGSERIAL PRIMARY KEY,
    submission_id         BIGINT NOT NULL REFERENCES rp_validation_submission(id)
                              ON DELETE CASCADE,
    check_code            TEXT NOT NULL,
    result                TEXT NOT NULL
        CHECK (result IN ('clear', 'needs_review', 'incomplete')),
    title                 TEXT NOT NULL,
    summary               TEXT NOT NULL,
    metrics               JSONB NOT NULL DEFAULT '{}'::jsonb,
    map_segments          JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (submission_id, check_code)
);

CREATE INDEX IF NOT EXISTS rp_validation_check_submission_idx
    ON rp_validation_check (submission_id, id);

ALTER TABLE rp_validation_submission ENABLE ROW LEVEL SECURITY;
ALTER TABLE rp_validation_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE rp_validation_check ENABLE ROW LEVEL SECURITY;
ALTER TABLE rp_validation_evidence
    ADD COLUMN IF NOT EXISTS control_orders INTEGER[] NOT NULL DEFAULT '{}';
REVOKE ALL ON rp_validation_submission FROM anon, authenticated;
REVOKE ALL ON rp_validation_evidence FROM anon, authenticated;
REVOKE ALL ON rp_validation_check FROM anon, authenticated;
