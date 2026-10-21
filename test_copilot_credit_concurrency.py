"""Concurrency check of the credit reservation against a real PostgreSQL.

The in-memory backend of the test suite mirrors the rules of the SQL, but it
cannot show what PostgreSQL does when two connections work on the same
operation at the same time. This module runs the production statements of
``CopilotStore`` against a real server.

The server is not started by the test: it reads the DSN from the environment
variable ``COPILOT_TEST_PG_DSN`` and skips itself when there is none, so the
normal CI run stays green and the check stays reproducible. To run it locally:

    python tools/pg_concurrency_check.py

That script starts an isolated PostgreSQL, creates the copilot tables, points
the environment variable at it and runs this module.

What the module proves:

* two parallel builds of one vacancy hold one claim and charge one credit;
* two parallel analyses of one vacancy do the same;
* two parallel reservations of different vacancies are both charged;
* a parallel reservation and a refund keep the journal consistent;
* the slot of the global budget is atomic: parallel calls cannot pass it;
* a claim that was taken over cannot be released or written by its old owner;
* the balance of a referral and its journal entry are written together.
"""
import os
import threading
import unittest

from copilot.credit_operations import (
    REASON_LLM_SPEND,
    REASON_RELEASE,
    REASON_SPEND,
)
from copilot.store import LLM_BUDGET_SCOPE, CopilotStore

DSN_ENV = "COPILOT_TEST_PG_DSN"
USER_ID = 4242
OTHER_USER_ID = 4243
REF = "job-concurrency"
BALANCE = 10


class PsycopgGrowthStore:
    """The durable growth store of the production code, on one connection.

    It connects exactly like ``growth_store.PostgresGrowthStore`` does:
    psycopg with autocommit, so every statement of the store runs on its own
    and the Edge bridge can forward one query per request.
    """

    def __init__(self, dsn: str):
        import psycopg

        self.conn = psycopg.connect(dsn, autocommit=True, prepare_threshold=None)

    def _ensure_conn(self):
        if self.conn is None or getattr(self.conn, "closed", True):
            import psycopg

            self.conn = psycopg.connect(
                os.environ[DSN_ENV], autocommit=True, prepare_threshold=None
            )
        return self.conn

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass


SCHEMA = """
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
CREATE TABLE IF NOT EXISTS public.growth_copilot_documents (
    user_id BIGINT NOT NULL,
    job_id TEXT NOT NULL,
    telegram_file_id TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL DEFAULT '',
    fit_score INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, job_id)
);
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
CREATE TABLE IF NOT EXISTS public.growth_copilot_rate_limits (
    user_id BIGINT PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hits INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _reset(dsn: str) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute("DELETE FROM growth_copilot_credit_events")
            cur.execute("DELETE FROM growth_copilot_credits")
            cur.execute("DELETE FROM growth_copilot_documents")
            cur.execute("DELETE FROM growth_copilot_ai_reports")
            cur.execute("DELETE FROM growth_copilot_rate_limits")
            cur.execute(
                "INSERT INTO growth_copilot_credits (user_id, balance) VALUES (%s, %s)",
                (USER_ID, BALANCE),
            )


def _balance(dsn: str, user_id: int = USER_ID) -> int:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT balance FROM growth_copilot_credits WHERE user_id = %s", (user_id,)
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0


def _journal(dsn: str, user_id: int = USER_ID):
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reason, delta, ref FROM growth_copilot_credit_events "
                "WHERE user_id = %s ORDER BY id",
                (user_id,),
            )
            return [(row[0], int(row[1]), row[2]) for row in cur.fetchall()]


def _store(dsn: str) -> CopilotStore:
    return CopilotStore(PsycopgGrowthStore(dsn))


def _parallel(dsn: str, jobs):
    """Run the given jobs at the same moment on independent connections."""
    barrier = threading.Barrier(len(jobs))
    results = [None] * len(jobs)
    stores = [_store(dsn) for _ in jobs]

    def worker(index, job, store):
        barrier.wait()
        try:
            results[index] = job(store)
        except Exception as exc:  # pragma: no cover - reported in the result
            results[index] = "error: %s: %s" % (type(exc).__name__, exc)

    threads = [
        threading.Thread(target=worker, args=(index, job, store))
        for index, (job, store) in enumerate(zip(jobs, stores))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for store in stores:
        store._store.close()
    return results


def scenario_sequential(dsn: str) -> dict:
    """The control: the second reservation of one operation is refused."""
    _reset(dsn)
    store = _store(dsn)
    try:
        first = store.reserve_credit(USER_ID, 1, REASON_SPEND, REF, release_reason=REASON_RELEASE)
        second = store.reserve_credit(USER_ID, 1, REASON_SPEND, REF, release_reason=REASON_RELEASE)
    finally:
        store._store.close()
    return {
        "first": first,
        "second": second,
        "balance": _balance(dsn),
        "spends": sum(1 for reason, _d, _r in _journal(dsn) if reason == REASON_SPEND),
    }


def scenario_parallel_same_operation(dsn: str) -> dict:
    """Two builds of one vacancy at the same moment.

    A build holds the claim of the vacancy before it reserves the credit, so
    the second build is refused and the user is charged once.
    """
    _reset(dsn)

    def build(store):
        claim = store.claim_document(USER_ID, REF, "cv.pdf")
        if not claim:
            return {"claim": None, "reserved": False}
        reserved = store.reserve_credit(
            USER_ID, 1, REASON_SPEND, REF, release_reason=REASON_RELEASE
        )
        return {"claim": claim, "reserved": bool(reserved)}

    results = _parallel(dsn, [build, build])
    journal = _journal(dsn)
    return {
        "results": results,
        "claims": sum(1 for item in results if item.get("claim")),
        "reserved": sum(1 for item in results if item.get("reserved")),
        "balance": _balance(dsn),
        "spends": sum(1 for r, _d, _r2 in journal if r == REASON_SPEND),
        "journal": journal,
    }


def scenario_parallel_llm_path(dsn: str) -> dict:
    """Two analyses of one vacancy at the same moment.

    The analysis slot of the vacancy is claimed before the credit is
    reserved, so the second tap is refused and the user is charged once.
    """
    _reset(dsn)

    def analyze(store):
        claim = store.claim_ai_report(USER_ID, REF)
        if not claim:
            return {"claim": None, "reserved": False}
        reserved = store.reserve_credit(
            USER_ID, 1, REASON_LLM_SPEND, REF, release_reason="llm_call_release"
        )
        return {"claim": claim, "reserved": bool(reserved)}

    results = _parallel(dsn, [analyze, analyze])
    journal = _journal(dsn)
    return {
        "results": results,
        "claims": sum(1 for item in results if item.get("claim")),
        "reserved": sum(1 for item in results if item.get("reserved")),
        "balance": _balance(dsn),
        "spends": sum(1 for r, _d, _r2 in journal if r == REASON_LLM_SPEND),
        "journal": journal,
    }


def scenario_parallel_different_operations(dsn: str) -> dict:
    """The control: two different operations are both charged."""
    _reset(dsn)

    def reserve(store, ref):
        return store.reserve_credit(
            USER_ID, 1, REASON_SPEND, ref, release_reason=REASON_RELEASE
        )

    results = _parallel(
        dsn,
        [
            lambda store: reserve(store, "job-a"),
            lambda store: reserve(store, "job-b"),
        ],
    )
    return {
        "results": results,
        "granted": sum(1 for item in results if item is True),
        "balance": _balance(dsn),
    }


def scenario_parallel_reserve_and_release(dsn: str) -> dict:
    """One connection reserves while another gives a credit back."""
    _reset(dsn)

    def reserve(store):
        return store.reserve_credit(
            USER_ID, 1, REASON_SPEND, REF, release_reason=REASON_RELEASE
        )

    def release(store):
        return store.release_credit(
            USER_ID, 1, REASON_RELEASE, REF, reserved_reason=REASON_SPEND
        )

    results = _parallel(dsn, [reserve, release])
    journal = _journal(dsn)
    return {
        "results": results,
        "balance": _balance(dsn),
        "journal": journal,
        "spends": sum(1 for r, _d, _r2 in journal if r == REASON_SPEND),
        "releases": sum(1 for r, _d, _r2 in journal if r == REASON_RELEASE),
    }


def scenario_parallel_budget_slots(dsn: str, *, budget=1, calls=4) -> dict:
    """The slot of the global budget is atomic.

    The budget is a counter in a row of its own, so the lock of that row
    serializes the callers: parallel calls cannot take more slots than the
    budget allows.
    """
    _reset(dsn)

    def take_slot(store):
        return store.reserve_llm_attempt(USER_ID, budget, "llm_attempt", REF)

    results = _parallel(dsn, [take_slot] * calls)
    journal = _journal(dsn)
    return {
        "results": results,
        "granted": sum(1 for item in results if item is True),
        "budget": budget,
        "attempts": sum(1 for r, _d, _r2 in journal if r == "llm_attempt"),
        "balance": _balance(dsn),
    }


def scenario_claim_ownership(dsn: str) -> dict:
    """A claim that was taken over belongs to the new owner."""
    _reset(dsn)
    store = _store(dsn)
    try:
        first = store.claim_document(USER_ID, REF, "cv.pdf")
        # The claim goes stale and another build takes it over.
        second = _take_over(dsn)
        late_release = store.release_document_claim(USER_ID, REF, first)
        late_write = store.save_document(
            USER_ID,
            REF,
            telegram_file_id="FILE-OLD",
            filename="cv.pdf",
            fit_score=10,
            claim=first,
        )
        row = _document(dsn)
    finally:
        store._store.close()
    return {
        "first": first,
        "second": second,
        "late_release": late_release,
        "late_write": late_write,
        "row": row,
    }


def _take_over(dsn: str) -> str:
    """Wait for the claim to go stale and take it over."""
    import time

    store = _store(dsn)
    try:
        with store._store._ensure_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE growth_copilot_documents SET created_at = NOW() - INTERVAL '1 hour'"
                )
        time.sleep(0.05)
        return store.claim_document(USER_ID, REF, "cv.pdf", stale_after_minutes=15)
    finally:
        store._store.close()


def _document(dsn: str):
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT telegram_file_id, filename FROM growth_copilot_documents "
                "WHERE user_id = %s AND job_id = %s",
                (USER_ID, REF),
            )
            row = cur.fetchone()
            return tuple(row) if row else None


def scenario_referral_grant(dsn: str) -> dict:
    """The bonus of a referral and its journal entry are one statement."""
    _reset(dsn)
    store = _store(dsn)
    try:
        store.grant_credits_with_event(USER_ID, 5, "referral", "")
    finally:
        store._store.close()
    journal = _journal(dsn)
    return {
        "balance": _balance(dsn),
        "journal": journal,
        "events": len(journal),
    }


def scenario_balance_matches_journal(dsn: str) -> dict:
    """After a mixed sequence the balance equals the journal."""
    _reset(dsn)
    store = _store(dsn)
    try:
        store.reserve_credit(USER_ID, 1, REASON_SPEND, REF, release_reason=REASON_RELEASE)
        store.release_credit(
            USER_ID, 1, REASON_RELEASE, REF, reserved_reason=REASON_SPEND
        )
        store.reserve_credit(USER_ID, 1, REASON_SPEND, REF, release_reason=REASON_RELEASE)
    finally:
        store._store.close()
    journal = _journal(dsn)
    total = sum(delta for _reason, delta, _ref in journal)
    return {"balance": _balance(dsn), "journal_sum": total, "journal": journal}


def scenario_reservation_without_claim(dsn: str) -> dict:
    """The measurement of the reservation that runs without a claim.

    The reservation counts the journal of the operation, and a parallel
    transaction cannot see the event of the other one yet. So the
    reservation alone is not safe: two parallel reservations of one operation
    can charge the user twice.

    The flows of the product are safe, because a costed action holds the
    claim of its operation before it reserves the credit, and the claim is
    the unique key of the operation. This scenario is a measurement, not a
    test: it shows the size of the remaining window of the primitive.
    """
    _reset(dsn)

    def reserve(store):
        return store.reserve_credit(
            USER_ID, 1, REASON_SPEND, REF, release_reason=REASON_RELEASE
        )

    results = _parallel(dsn, [reserve, reserve])
    journal = _journal(dsn)
    return {
        "results": results,
        "granted": sum(1 for item in results if item is True),
        "spends": sum(1 for r, _d, _r2 in journal if r == REASON_SPEND),
        "balance": _balance(dsn),
    }


SCENARIOS = {
    "sequential": scenario_sequential,
    "parallel_same_operation": scenario_parallel_same_operation,
    "parallel_llm_path": scenario_parallel_llm_path,
    "parallel_different_operations": scenario_parallel_different_operations,
    "parallel_reserve_and_release": scenario_parallel_reserve_and_release,
    "parallel_budget_slots": scenario_parallel_budget_slots,
    "claim_ownership": scenario_claim_ownership,
    "referral_grant": scenario_referral_grant,
    "balance_matches_journal": scenario_balance_matches_journal,
    "reservation_without_claim": scenario_reservation_without_claim,
}


def run_scenarios(dsn: str) -> dict:
    return {name: func(dsn) for name, func in SCENARIOS.items()}


class CreditConcurrencyTests(unittest.TestCase):
    """The credit reservation on a real PostgreSQL server."""

    def setUp(self):
        dsn = os.environ.get(DSN_ENV)
        if not dsn:
            self.skipTest("%s is not set: no PostgreSQL for the concurrency check" % DSN_ENV)
        self.dsn = dsn

    def test_a_repeated_reservation_is_refused(self):
        result = scenario_sequential(self.dsn)
        self.assertTrue(result["first"])
        self.assertFalse(result["second"])
        self.assertEqual(result["spends"], 1)
        self.assertEqual(result["balance"], BALANCE - 1)

    def test_two_parallel_builds_of_one_vacancy_charge_once(self):
        result = scenario_parallel_same_operation(self.dsn)
        self.assertEqual(result["claims"], 1, result)
        self.assertEqual(result["reserved"], 1, result)
        self.assertEqual(result["spends"], 1, result)
        self.assertEqual(result["balance"], BALANCE - 1, result)

    def test_two_parallel_analyses_of_one_vacancy_charge_once(self):
        result = scenario_parallel_llm_path(self.dsn)
        self.assertEqual(result["claims"], 1, result)
        self.assertEqual(result["reserved"], 1, result)
        self.assertEqual(result["spends"], 1, result)
        self.assertEqual(result["balance"], BALANCE - 1, result)

    def test_two_parallel_reservations_of_different_operations_both_charge(self):
        result = scenario_parallel_different_operations(self.dsn)
        self.assertEqual(result["granted"], 2, result)
        self.assertEqual(result["balance"], BALANCE - 2, result)

    def test_a_parallel_reserve_and_release_keep_the_journal_consistent(self):
        result = scenario_parallel_reserve_and_release(self.dsn)
        self.assertEqual(result["spends"] - result["releases"], 1, result)
        self.assertEqual(result["balance"], BALANCE - 1, result)

    def test_the_budget_slot_is_atomic(self):
        """Parallel calls cannot take more slots than the budget allows."""
        result = scenario_parallel_budget_slots(self.dsn, budget=1, calls=4)
        self.assertEqual(result["granted"], 1, result)
        self.assertEqual(result["attempts"], 1, result)

    def test_a_claim_that_was_taken_over_belongs_to_the_new_owner(self):
        result = scenario_claim_ownership(self.dsn)
        self.assertTrue(result["first"])
        self.assertTrue(result["second"])
        self.assertNotEqual(result["first"], result["second"])
        # The late release and the late write of the old owner do nothing.
        self.assertFalse(result["late_release"])
        self.assertFalse(result["late_write"])
        self.assertEqual(result["row"][0], "")
        self.assertEqual(result["row"][1], result["second"])

    def test_the_referral_writes_the_balance_and_the_journal_together(self):
        result = scenario_referral_grant(self.dsn)
        self.assertEqual(result["balance"], BALANCE + 5, result)
        self.assertEqual(result["events"], 1, result)
        self.assertEqual(result["journal"][0][0], "referral")
        self.assertEqual(result["journal"][0][1], 5)

    def test_the_balance_equals_the_journal(self):
        result = scenario_balance_matches_journal(self.dsn)
        self.assertEqual(result["balance"], BALANCE + result["journal_sum"], result)
        self.assertEqual(result["balance"], BALANCE - 1, result)

    def test_the_budget_counter_lives_in_its_own_row(self):
        """The counter of the budget is not the counter of the requests."""
        _reset(self.dsn)
        store = _store(self.dsn)
        try:
            store.bump_rate_limit(USER_ID, limit=10, window_minutes=10)
            store.reserve_llm_attempt(USER_ID, 5, "llm_attempt", REF)
        finally:
            store._store.close()
        import psycopg

        with psycopg.connect(self.dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT hits FROM growth_copilot_rate_limits WHERE user_id = %s",
                    (LLM_BUDGET_SCOPE,),
                )
                row = cur.fetchone()
        self.assertEqual(int(row[0]), 1)


if __name__ == "__main__":
    unittest.main()
