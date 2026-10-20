-- P9 Copilot (Stage C): cache of the optional AI analyses.
--
-- The AI layer is only reached through an explicit button, and its answer is
-- cached here so a repeated tap does not pay for a second provider call.
--
-- Privacy invariant: the table never stores resume text, resume file names or
-- any other personal document content. An answer that was produced with the
-- resume is stored without its text, because such an answer may echo personal
-- data back to the user.

CREATE TABLE IF NOT EXISTS public.growth_copilot_ai_reports (
    user_id BIGINT NOT NULL,
    job_id TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    answer TEXT NOT NULL DEFAULT '',
    used_resume BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_growth_copilot_ai_reports_created
    ON public.growth_copilot_ai_reports(created_at);

ALTER TABLE public.growth_copilot_ai_reports ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.growth_copilot_ai_reports FROM anon, authenticated;
