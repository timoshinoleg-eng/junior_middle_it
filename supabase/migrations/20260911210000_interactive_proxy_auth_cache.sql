-- Cache successful Telegram bot identity checks across Edge isolates.
-- Stores only SHA-256(token), never the raw bot token.

CREATE TABLE IF NOT EXISTS public.growth_proxy_auth_cache (
    token_hash TEXT PRIMARY KEY CHECK (length(token_hash) = 64),
    bot_username TEXT NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS growth_proxy_auth_cache_expires_idx
    ON public.growth_proxy_auth_cache(expires_at);

ALTER TABLE public.growth_proxy_auth_cache ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.growth_proxy_auth_cache FROM anon, authenticated;
