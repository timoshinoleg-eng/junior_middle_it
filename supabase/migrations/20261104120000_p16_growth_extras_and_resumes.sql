-- p16: generic per-user extra settings + saved resumes.
--
-- growth_user_settings gains an ``extra`` JSONB bag so product flags that are
-- not first-class columns (magnet_claimed, stars_charge_ids, onboarding
-- marks) survive on the Postgres backend. Previously PostgresGrowthStore
-- silently dropped unknown keys, making the magnet re-claimable and Stars
-- top-ups double-grantable in production.
--
-- growth_copilot_resumes stores the user's own resume text (opt-in, one per
-- user) so vacancy analyses can reuse it without re-uploading the document.
ALTER TABLE public.growth_user_settings
    ADD COLUMN IF NOT EXISTS extra JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS public.growth_copilot_resumes (
    user_id BIGINT PRIMARY KEY,
    resume_text TEXT NOT NULL DEFAULT '',
    skills TEXT NOT NULL DEFAULT '',
    grade TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.growth_copilot_resumes ENABLE ROW LEVEL SECURITY;
