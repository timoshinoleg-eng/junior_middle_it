-- P13 Copilot: the registry of the credit operations.
--
-- A costed operation such as the CV export of one vacancy spans three steps:
-- the credit is reserved, the document is produced and the document is
-- delivered. The state of the operation used to be derived from the journal:
-- a reservation was open when the journal held more spends than releases of
-- the reference.
--
-- That rule cannot be made safe. Under READ COMMITTED a parallel transaction
-- does not see the event of the other one yet, so two parallel requests of
-- the same vacancy both count the same number of reservations and both of
-- them debit the balance. The journal stays the history of the credits, but
-- the state of an operation lives in one row of this table now, and the
-- database itself refuses a second reservation of the same attempt.
--
-- The table is additive: the code of a production without it keeps the old
-- statements, so the migration can be applied before the deploy and the
-- deploy can be rolled back without losing anything.
--
-- Privacy invariant: the table holds the reference of a vacancy, the state of
-- the operation and the amount of the credit. It never holds a resume, a
-- file name or any other personal document.

CREATE TABLE IF NOT EXISTS public.growth_copilot_credit_ops (
    user_id BIGINT NOT NULL,
    ref TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'reserved'
        CHECK (state IN ('reserved', 'released', 'delivered', 'unknown')),
    amount INTEGER NOT NULL DEFAULT 1,
    attempt INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, ref, kind)
);

-- The rows of the operations that were paid for are dropped by the retention
-- of the store. The index keeps that cleanup cheap.
CREATE INDEX IF NOT EXISTS growth_copilot_credit_ops_updated_at_idx
    ON public.growth_copilot_credit_ops (updated_at);

ALTER TABLE public.growth_copilot_credit_ops ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.growth_copilot_credit_ops FROM anon, authenticated;

-- The operations that are open when the migration is applied keep their state.
-- A user who paid for a vacancy before the migration must not pay for it
-- again, so the open reservations and the paid operations of the journal
-- become rows of the registry.
--
-- The state of a reference is the state of its last event: a delivery wins
-- over an open outcome, an open outcome wins over an open reservation, and
-- a reservation that was given back is not open any more.
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
