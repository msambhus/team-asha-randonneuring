-- BrevetHub rider follows for calendar live views.  This is event-scoped so a
-- rider can follow before the organizer creates a live ride, and the follow
-- automatically resolves when a public live ride is linked later.
CREATE TABLE IF NOT EXISTS rp_followed_live_event (
    rider_id INTEGER NOT NULL REFERENCES rp_rider(id) ON DELETE CASCADE,
    event_id INTEGER NOT NULL REFERENCES rp_brevet_event(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (rider_id, event_id)
);
CREATE INDEX IF NOT EXISTS rp_followed_live_event_event_idx ON rp_followed_live_event(event_id);
ALTER TABLE rp_followed_live_event ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON rp_followed_live_event FROM anon, authenticated;
