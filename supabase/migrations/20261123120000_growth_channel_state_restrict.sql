-- Review backend database role and active clients before applying; see PUBLISHING_CADENCE.md.
ALTER TABLE public.growth_channel_state ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.growth_channel_state FROM anon, authenticated;
