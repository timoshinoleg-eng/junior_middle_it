-- Singleton per-channel state blobs (pinned-post marks, navigator state, etc.).
-- Written through the authenticated Edge growth transports and the admin runtime;
-- DDL is migration-owned because the bot-token proxy accepts data/query only.
CREATE TABLE IF NOT EXISTS public.growth_channel_state (
    channel_key TEXT PRIMARY KEY,
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
