-- P7 restart-safe workflow metadata. Applied to production on 2026-09-10.
-- Stores only short-lived setup/session metadata; never resume text or file bytes.

CREATE TABLE IF NOT EXISTS public.growth_runtime_state (
    user_id BIGINT NOT NULL,
    state_key TEXT NOT NULL,
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, state_key),
    CHECK (char_length(state_key) BETWEEN 1 AND 80)
);

CREATE INDEX IF NOT EXISTS idx_growth_runtime_state_expires
    ON public.growth_runtime_state(expires_at)
    WHERE expires_at IS NOT NULL;

ALTER TABLE public.growth_runtime_state ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.growth_runtime_state FROM anon, authenticated;
