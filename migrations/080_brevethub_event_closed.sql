-- Add closed_at to rp_brevet_event.
-- An event is OPEN until an admin explicitly closes it.
-- closed_at = timestamp when closed; NULL means OPEN.
ALTER TABLE rp_brevet_event ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ DEFAULT NULL;
