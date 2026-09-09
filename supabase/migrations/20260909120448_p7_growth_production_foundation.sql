-- P7 production foundation for durable growth state.
-- Applied to Supabase project junior-middle-it-growth on 2026-09-09.

CREATE TABLE IF NOT EXISTS public.growth_user_settings (
    user_id BIGINT PRIMARY KEY,
    enabled_categories TEXT NOT NULL DEFAULT '',
    hide_senior BOOLEAN NOT NULL DEFAULT TRUE,
    min_salary_filter INTEGER NOT NULL DEFAULT 0 CHECK (min_salary_filter >= 0),
    skills TEXT NOT NULL DEFAULT '',
    digest_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    onboarding_done BOOLEAN NOT NULL DEFAULT FALSE,
    alerts_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    premium_unlocked BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.growth_referrals (
    user_id BIGINT PRIMARY KEY,
    referrer_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (user_id <> referrer_id)
);

CREATE TABLE IF NOT EXISTS public.growth_events (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id BIGINT,
    name TEXT NOT NULL,
    props JSONB NOT NULL DEFAULT '{}'::jsonb,
    migration_key TEXT
);

ALTER TABLE public.growth_events
    ADD COLUMN IF NOT EXISTS migration_key TEXT;

CREATE TABLE IF NOT EXISTS public.growth_migration_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_growth_events_name_ts
    ON public.growth_events(name, ts);
CREATE INDEX IF NOT EXISTS idx_growth_events_user_ts
    ON public.growth_events(user_id, ts);
CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_events_migration_key
    ON public.growth_events(migration_key);
CREATE INDEX IF NOT EXISTS idx_growth_referrals_referrer
    ON public.growth_referrals(referrer_id);

-- Growth data is backend-only. No Data API policy is intentionally created.
ALTER TABLE public.growth_user_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_referrals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_migration_meta ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.growth_user_settings FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_referrals FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_events FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_migration_meta FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.growth_events_id_seq FROM anon, authenticated;
