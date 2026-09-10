-- Cover the growth_favorites.job_hash foreign key for efficient payload cleanup.
-- Applied to production Supabase before code rollout.

CREATE INDEX IF NOT EXISTS idx_growth_favorites_job_hash
    ON public.growth_favorites(job_hash);
