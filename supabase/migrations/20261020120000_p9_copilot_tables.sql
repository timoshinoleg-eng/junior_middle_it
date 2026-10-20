-- P9 Copilot tables: deterministic fit analysis, grade audit, enrichment cache,
-- generated document cache and credit accounting.
--
-- Every table is a growth_* relation so the Edge growth bridge can serve it,
-- and every table stays backend-only: RLS is enabled and no Data API policy is
-- created, so anon/authenticated keep no DML privileges.
--
-- Privacy invariant: no table stores resume text, resume file names or any
-- other personal document content. Only derived numbers, skill names and
-- vacancy level verdicts are persisted.

CREATE TABLE IF NOT EXISTS public.growth_copilot_profiles (
    user_id BIGINT PRIMARY KEY,
    grade TEXT NOT NULL DEFAULT '',
    skills TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'settings',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.growth_copilot_evaluations (
    user_id BIGINT NOT NULL,
    job_id TEXT NOT NULL,
    fit_score INTEGER NOT NULL DEFAULT 0 CHECK (fit_score >= 0 AND fit_score <= 100),
    stack_match_score INTEGER NOT NULL DEFAULT 0 CHECK (stack_match_score >= 0 AND stack_match_score <= 100),
    keyword_overlap_pct INTEGER NOT NULL DEFAULT 0 CHECK (keyword_overlap_pct >= 0 AND keyword_overlap_pct <= 100),
    matched_keywords TEXT NOT NULL DEFAULT '[]',
    missing_skills TEXT NOT NULL DEFAULT '[]',
    llm_used BOOLEAN NOT NULL DEFAULT FALSE,
    model TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, job_id)
);

CREATE TABLE IF NOT EXISTS public.growth_copilot_documents (
    user_id BIGINT NOT NULL,
    job_id TEXT NOT NULL,
    telegram_file_id TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL DEFAULT '',
    fit_score INTEGER NOT NULL DEFAULT 0 CHECK (fit_score >= 0 AND fit_score <= 100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, job_id)
);

CREATE TABLE IF NOT EXISTS public.growth_copilot_credits (
    user_id BIGINT PRIMARY KEY,
    balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
    free_granted_on DATE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.growth_copilot_credit_events (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    ref TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.growth_copilot_grade_audits (
    job_hash TEXT PRIMARY KEY,
    declared_grade TEXT NOT NULL DEFAULT '',
    real_grade TEXT NOT NULL DEFAULT '',
    realism_score INTEGER NOT NULL DEFAULT 0 CHECK (realism_score >= 0 AND realism_score <= 100),
    is_fake_grade BOOLEAN NOT NULL DEFAULT FALSE,
    badge_text TEXT NOT NULL DEFAULT '',
    signals TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.growth_copilot_enrichments (
    job_hash TEXT PRIMARY KEY,
    source_url TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_growth_copilot_evaluations_created
    ON public.growth_copilot_evaluations(created_at);
CREATE INDEX IF NOT EXISTS idx_growth_copilot_documents_created
    ON public.growth_copilot_documents(created_at);
CREATE INDEX IF NOT EXISTS idx_growth_copilot_credit_events_user
    ON public.growth_copilot_credit_events(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_growth_copilot_credit_events_reason
    ON public.growth_copilot_credit_events(reason, created_at);
CREATE INDEX IF NOT EXISTS idx_growth_copilot_grade_audits_created
    ON public.growth_copilot_grade_audits(created_at);
CREATE INDEX IF NOT EXISTS idx_growth_copilot_enrichments_fetched
    ON public.growth_copilot_enrichments(fetched_at);

ALTER TABLE public.growth_copilot_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_copilot_evaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_copilot_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_copilot_credits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_copilot_credit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_copilot_grade_audits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_copilot_enrichments ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.growth_copilot_profiles FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_copilot_evaluations FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_copilot_documents FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_copilot_credits FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_copilot_credit_events FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_copilot_grade_audits FROM anon, authenticated;
REVOKE ALL ON TABLE public.growth_copilot_enrichments FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.growth_copilot_credit_events_id_seq FROM anon, authenticated;
