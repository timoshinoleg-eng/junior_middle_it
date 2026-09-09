-- Product-runtime durable tables required by Saved Searches and the P7 public site.
-- Applied to Supabase project junior-middle-it-growth on 2026-09-09.

CREATE TABLE IF NOT EXISTS public.growth_job_payloads (
    hash TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_growth_job_payloads_updated
    ON public.growth_job_payloads(updated_at);

CREATE TABLE IF NOT EXISTS public.growth_saved_searches (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    categories TEXT NOT NULL DEFAULT '',
    skills TEXT NOT NULL DEFAULT '',
    min_salary_filter INTEGER NOT NULL DEFAULT 0 CHECK (min_salary_filter >= 0),
    hide_senior BOOLEAN NOT NULL DEFAULT TRUE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_growth_saved_searches_user
    ON public.growth_saved_searches(user_id, enabled);

ALTER TABLE public.growth_job_payloads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_saved_searches ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.growth_job_payloads FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_saved_searches FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.growth_saved_searches_id_seq FROM anon, authenticated;
