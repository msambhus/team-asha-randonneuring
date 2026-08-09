-- Official RUSA certificate / homologation number for a rider's completed event.
ALTER TABLE rp_event_signup
    ADD COLUMN IF NOT EXISTS homologation_number TEXT;
