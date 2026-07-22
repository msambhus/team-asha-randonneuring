-- 056_brevethub_event_live_link.sql
-- Per-event live ride link (Closes #538): give a live ride (rp_ride) an OPTIONAL
-- linkage to a calendar event (rp_brevet_event), so the calendar can surface a
-- per-event "Live" button pointing at the shared Radial view (/live/<ride_id>).
--
-- Today rp_ride and rp_brevet_event are fully disjoint: a live ride carries no
-- event/route linkage (only rwgps_url + start_at from migrations 033/044), so a
-- per-event Live link has nothing to point at. This adds that missing FK.
--
-- Strictly additive + idempotent + rp_*-only (exactly like migrations 042/044): a
-- single nullable column plus a guarded index. NULL means "unlinked" — every
-- existing rp_ride row stays valid and untouched, and code that predates this
-- column simply ignores it. Applying (or re-applying) it cannot alter any Team
-- Asha table. There is NO backfill: a pre-existing public ride resolves to its
-- event via the name+date fallback in brevethub.models until an owner sets the
-- explicit link, which is authoritative.

-- --------------------------------------------------------------------------- --
-- rp_ride.event_id — the calendar event a live ride is following. Nullable FK to
-- rp_brevet_event(id); an unlinked ride (the default) keeps a NULL here. The
-- owner-scoped setter (models.set_ride_event) is the only writer, so a ride can
-- only ever be linked/unlinked by its own rider.
-- --------------------------------------------------------------------------- --
ALTER TABLE rp_ride ADD COLUMN IF NOT EXISTS event_id INTEGER REFERENCES rp_brevet_event(id);

-- The calendar resolver looks rides up by event_id (FK tier); index it so the
-- per-page lookup stays cheap.
CREATE INDEX IF NOT EXISTS rp_ride_event_id_idx ON rp_ride (event_id);
