-- Durable at-most-once receipts for request-driven scheduled Telegram delivery.
-- Applied to production Supabase before code rollout.

CREATE TABLE IF NOT EXISTS public.growth_delivery_receipts (
    delivery_kind TEXT NOT NULL,
    period_key TEXT NOT NULL,
    target_key TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'sending',
    item_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    PRIMARY KEY (delivery_kind, period_key, target_key),
    CHECK (delivery_kind IN ('daily_channel','daily_personal','weekly_salary')),
    CHECK (state IN ('sending','sent','no_content')),
    CHECK (char_length(period_key) BETWEEN 1 AND 32),
    CHECK (char_length(target_key) BETWEEN 1 AND 160),
    CHECK (item_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_growth_delivery_receipts_created
    ON public.growth_delivery_receipts(created_at);

ALTER TABLE public.growth_delivery_receipts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.growth_delivery_receipts FROM anon, authenticated;
