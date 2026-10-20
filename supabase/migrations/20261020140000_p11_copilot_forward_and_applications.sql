-- P9 Copilot (Stage D): forwarded vacancies and the application tracker.
--
-- Forward-to-Analyze lets a user forward a vacancy into the private chat and
-- get the same deterministic analysis as for a channel post. The forwarded
-- payload is stored under the hash of its text, so the same post always maps
-- to one row and the buttons of the analysis card keep working after a
-- redeploy.
--
-- The application tracker remembers that the user sent a reply and which
-- answer came back, so the scheduler can ask about the result exactly once.
--
-- Privacy invariant: the forwarded text is a public vacancy posting - the
-- forward handler refuses resume-like texts before they reach this table, and
-- no column holds resume content, file names or other personal documents.

CREATE TABLE IF NOT EXISTS public.growth_copilot_forwarded_jobs (
    job_hash TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.growth_copilot_applications (
    user_id BIGINT NOT NULL,
    job_id TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT 'sent',
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reminded_at TIMESTAMPTZ,
    answered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_growth_copilot_forwarded_jobs_created
    ON public.growth_copilot_forwarded_jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_growth_copilot_applications_pending
    ON public.growth_copilot_applications(stage, sent_at);

ALTER TABLE public.growth_copilot_forwarded_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_copilot_applications ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.growth_copilot_forwarded_jobs FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_copilot_applications FROM anon, authenticated;
