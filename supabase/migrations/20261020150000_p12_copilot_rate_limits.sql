-- P9 Copilot (Stage E): rate limiter of the Mini App API.
--
-- The Mini App authenticates every request with the Telegram signature, but a
-- signed client can still be used too often. The limiter counts the requests
-- of one user inside a fixed window and never stores anything but the counter.

CREATE TABLE IF NOT EXISTS public.growth_copilot_rate_limits (
    user_id BIGINT PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hits INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.growth_copilot_rate_limits ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.growth_copilot_rate_limits FROM anon, authenticated;
