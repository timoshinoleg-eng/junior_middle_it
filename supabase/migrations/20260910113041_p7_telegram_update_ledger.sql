-- P7 durable Telegram webhook delivery ledger.
-- Prevents concurrent duplicate update processing while allowing a crashed
-- request to be retried after a short lease expires.

CREATE TABLE IF NOT EXISTS public.growth_telegram_updates (
    update_id BIGINT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'processing',
    lease_expires_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    CHECK (state IN ('processing','processed'))
);

CREATE INDEX IF NOT EXISTS idx_growth_telegram_updates_lease
    ON public.growth_telegram_updates(lease_expires_at)
    WHERE state = 'processing' AND lease_expires_at IS NOT NULL;

ALTER TABLE public.growth_telegram_updates ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.growth_telegram_updates FROM anon, authenticated;
