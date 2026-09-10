ALTER TABLE public.growth_job_payloads
  ADD COLUMN IF NOT EXISTS publication_state text,
  ADD COLUMN IF NOT EXISTS claim_token text,
  ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz,
  ADD COLUMN IF NOT EXISTS published_at timestamptz;

UPDATE public.growth_job_payloads
SET publication_state = COALESCE(publication_state, 'published'),
    published_at = COALESCE(published_at, updated_at, created_at, NOW())
WHERE publication_state IS NULL OR published_at IS NULL;

ALTER TABLE public.growth_job_payloads
  ALTER COLUMN publication_state SET DEFAULT 'published',
  ALTER COLUMN publication_state SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'growth_job_payloads_publication_state_check'
      AND conrelid = 'public.growth_job_payloads'::regclass
  ) THEN
    ALTER TABLE public.growth_job_payloads
      ADD CONSTRAINT growth_job_payloads_publication_state_check
      CHECK (publication_state IN ('pending', 'sending', 'published'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_growth_job_payloads_publication_state
  ON public.growth_job_payloads (publication_state, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_growth_job_payloads_pending_lease
  ON public.growth_job_payloads (lease_expires_at)
  WHERE publication_state = 'pending';
