-- P7 production verification for junior_middle_it.
-- Safe to run against the production Supabase project.
-- Synthetic writes are contained in a transaction and always rolled back.

-- 1) Expected backend-only tables exist and RLS is enabled.
SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'r'
  AND c.relname LIKE 'growth_%'
ORDER BY c.relname;

-- 2) anon/authenticated must have no table privileges on growth data.
SELECT table_name, grantee,
       string_agg(privilege_type, ',' ORDER BY privilege_type) AS privileges
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND table_name LIKE 'growth_%'
  AND grantee IN ('anon', 'authenticated')
GROUP BY table_name, grantee
ORDER BY table_name, grantee;

-- 3) Transactional CRUD smoke. Every synthetic row is rolled back.
BEGIN;

INSERT INTO public.growth_user_settings(
    user_id, enabled_categories, skills, onboarding_done
) VALUES (
    -900000001, 'development,qa', 'python,sql', TRUE
);

INSERT INTO public.growth_referrals(user_id, referrer_id)
VALUES (-900000002, -900000001);

INSERT INTO public.growth_events(user_id, name, props, migration_key)
VALUES (
    -900000001,
    'p7_infra_smoke',
    '{"source":"verify_p7.sql"}'::jsonb,
    'p7_infra_smoke_verify_sql'
);

INSERT INTO public.growth_job_payloads(hash, payload)
VALUES (
    'p7-infra-smoke-verify-sql',
    '{"title":"smoke","category":"development"}'::jsonb
);

INSERT INTO public.growth_saved_searches(
    user_id, name, categories, skills, fingerprint
) VALUES (
    -900000001,
    'smoke',
    'development',
    'python',
    'p7-infra-smoke-verify-sql'
);

SELECT
    (SELECT count(*) FROM public.growth_user_settings
      WHERE user_id = -900000001) AS user_settings_rows,
    (SELECT count(*) FROM public.growth_referrals
      WHERE user_id = -900000002) AS referral_rows,
    (SELECT count(*) FROM public.growth_events
      WHERE migration_key = 'p7_infra_smoke_verify_sql') AS event_rows,
    (SELECT count(*) FROM public.growth_job_payloads
      WHERE hash = 'p7-infra-smoke-verify-sql') AS payload_rows,
    (SELECT count(*) FROM public.growth_saved_searches
      WHERE fingerprint = 'p7-infra-smoke-verify-sql') AS saved_search_rows;

ROLLBACK;

-- 4) Constraint smoke. Expected violations are caught; cleanup is explicit.
DO $$
DECLARE
    ok_self_ref BOOLEAN := FALSE;
    ok_salary BOOLEAN := FALSE;
    ok_migration BOOLEAN := FALSE;
BEGIN
    BEGIN
        INSERT INTO public.growth_referrals(user_id, referrer_id)
        VALUES (-910000001, -910000001);
    EXCEPTION WHEN check_violation THEN
        ok_self_ref := TRUE;
    END;

    BEGIN
        INSERT INTO public.growth_user_settings(user_id, min_salary_filter)
        VALUES (-910000002, -1);
    EXCEPTION WHEN check_violation THEN
        ok_salary := TRUE;
    END;

    BEGIN
        INSERT INTO public.growth_events(user_id, name, migration_key)
        VALUES (-910000003, 'smoke', 'p7-unique-smoke-verify-sql');
        INSERT INTO public.growth_events(user_id, name, migration_key)
        VALUES (-910000004, 'smoke', 'p7-unique-smoke-verify-sql');
    EXCEPTION WHEN unique_violation THEN
        ok_migration := TRUE;
    END;

    IF NOT (ok_self_ref AND ok_salary AND ok_migration) THEN
        RAISE EXCEPTION
            'P7 constraint smoke failed: self_ref=%, salary=%, migration=%',
            ok_self_ref, ok_salary, ok_migration;
    END IF;

    DELETE FROM public.growth_events
    WHERE migration_key = 'p7-unique-smoke-verify-sql';
END $$;

SELECT 'ok' AS constraint_smoke;

-- 5) Production row counts after verification. Synthetic rows must be absent.
SELECT 'growth_user_settings' AS table_name, count(*)::bigint AS rows
FROM public.growth_user_settings
UNION ALL
SELECT 'growth_referrals', count(*) FROM public.growth_referrals
UNION ALL
SELECT 'growth_events', count(*) FROM public.growth_events
UNION ALL
SELECT 'growth_migration_meta', count(*) FROM public.growth_migration_meta
UNION ALL
SELECT 'growth_job_payloads', count(*) FROM public.growth_job_payloads
UNION ALL
SELECT 'growth_saved_searches', count(*) FROM public.growth_saved_searches
ORDER BY table_name;
