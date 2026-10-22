-- The reconciliation of the credit operations.
--
-- The previous code had no registry and wrote its reservations into the
-- journal only. A production that switches to the new code therefore holds
-- operations the registry does not know yet, and the new code would charge
-- the user for such an operation a second time.
--
-- This script adopts every operation of the journal into the registry. It is
-- the same rule the store uses at runtime: the state of a reference is the
-- state of its last event, and the attempt of the operation is the number of
-- the reservations the journal holds for it.
--
-- The script is idempotent: a row that the registry already holds is kept
-- as it is, so the state of the new code is never overwritten. It can be
-- run again after every change of the traffic.
--
-- Run it while the credit operations are paused, and check the result with
-- the check "supabase.credit_registry_ready" of tools/verify_release_v7.py.

WITH spends AS (
    SELECT
        user_id,
        ref,
        reason AS kind,
        COUNT(*) AS spends
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend', 'llm_call')
    GROUP BY user_id, ref, reason
),
releases AS (
    SELECT
        user_id,
        ref,
        CASE reason
            WHEN 'spend_release' THEN 'spend'
            ELSE 'llm_call'
        END AS kind,
        COUNT(*) AS releases
    FROM public.growth_copilot_credit_events
    WHERE reason IN ('spend_release', 'llm_call_release')
    GROUP BY user_id, ref, reason
),
states AS (
    SELECT
        s.user_id,
        s.ref,
        s.kind,
        s.spends,
        CASE
            WHEN EXISTS (
                SELECT 1
                FROM public.growth_copilot_credit_events d
                WHERE d.user_id = s.user_id
                  AND d.ref = s.ref
                  AND d.reason = CASE s.kind
                      WHEN 'spend' THEN 'cv_delivered'
                      ELSE 'llm_delivered'
                  END
            ) THEN 'delivered'
            WHEN EXISTS (
                SELECT 1
                FROM public.growth_copilot_credit_events u
                WHERE u.user_id = s.user_id
                  AND u.ref = s.ref
                  AND u.reason = CASE s.kind
                      WHEN 'spend' THEN 'cv_delivery_unknown'
                      ELSE 'llm_delivery_unknown'
                  END
            ) THEN 'unknown'
            WHEN s.spends > COALESCE(r.releases, 0) THEN 'reserved'
            ELSE 'released'
        END AS state
    FROM spends s
    LEFT JOIN releases r
        ON r.user_id = s.user_id AND r.ref = s.ref AND r.kind = s.kind
)
INSERT INTO public.growth_copilot_credit_ops
    (user_id, ref, kind, state, amount, attempt, created_at, updated_at)
SELECT
    user_id,
    ref,
    kind,
    state,
    1,
    spends,
    NOW(),
    NOW()
FROM states
WHERE state <> 'released'
ON CONFLICT (user_id, ref, kind) DO NOTHING;
