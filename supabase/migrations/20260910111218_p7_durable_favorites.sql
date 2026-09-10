-- P7 durable favorites for restart-safe/serverless interactive runtimes.
-- Applied to production Supabase before code rollout.

CREATE TABLE IF NOT EXISTS public.growth_favorites (
    user_id BIGINT NOT NULL,
    job_hash TEXT NOT NULL,
    saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, job_hash),
    CONSTRAINT growth_favorites_job_fk
        FOREIGN KEY (job_hash) REFERENCES public.growth_job_payloads(hash) ON DELETE CASCADE,
    CHECK (char_length(job_hash) BETWEEN 1 AND 100)
);

CREATE INDEX IF NOT EXISTS idx_growth_favorites_user_saved
    ON public.growth_favorites(user_id, saved_at DESC);

ALTER TABLE public.growth_favorites ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.growth_favorites FROM anon, authenticated;
