-- Keep the ownership token separate from model metadata across cache writes.
-- Legacy model values containing a claim token are not model names.
ALTER TABLE public.growth_copilot_ai_reports
    ADD COLUMN IF NOT EXISTS claim TEXT NOT NULL DEFAULT '';

UPDATE public.growth_copilot_ai_reports
SET model = ''
WHERE model LIKE 'claim:%';
