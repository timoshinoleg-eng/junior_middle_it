"""Concurrency check of the credit reservation against a real PostgreSQL.

The in-memory backend of the test suite mirrors the rules of the SQL, but it
cannot show what PostgreSQL does when two connections work on the same
operation at the same time. This module runs the production statements of
``CopilotStore`` against a real server.

The server is not started by the test: it reads the DSN from the environment
variable ``COPILOT_TEST_PG_DSN`` and skips itself when there is none, so the
local run can omit PostgreSQL. Only database copilot_test on localhost, 127.0.0.1 or ::1 is allowed. CI supplies an isolated PostgreSQL service. To run it locally:

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
* the balance of a referral and its journal entry are written together;
* two parallel reservations of one operation charge the user once, even
  without a claim in front of them, and the same holds for the releases;
* a reservation without balance changes nothing, so an operation is never
  paid for without a debit;
* a build whose slot was released cannot write its file or its answer.
"""
import os
from tools.copilot_pg_safety import validate_test_dsn
import threading
import unittest

from copilot.credit_operations import (
    CostedOperation, CreditStoreUnavailable,
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
CREATE TABLE IF NOT EXISTS public.growth_copilot_credit_ops (
    user_id BIGINT NOT NULL,
    ref TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'reserved',
    amount INTEGER NOT NULL DEFAULT 1,
    attempt INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, ref, kind)
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
    claim TEXT NOT NULL DEFAULT '',
    answer TEXT NOT NULL DEFAULT '',
    used_resume BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, job_id)
);
ALTER TABLE public.growth_copilot_ai_reports ADD COLUMN IF NOT EXISTS claim TEXT NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS public.growth_copilot_rate_limits (
    user_id BIGINT PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hits INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _reset(dsn: str) -> None:
    validate_test_dsn(dsn)
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute("DELETE FROM growth_copilot_credit_events")
            cur.execute("DELETE FROM growth_copilot_credit_ops")
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


def _balance_set(dsn: str, balance: int, user_id: int = USER_ID) -> int:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE growth_copilot_credits SET balance = %s WHERE user_id = %s",
                (int(balance), int(user_id)),
            )
    return int(balance)


def _operations(dsn: str, user_id: int = USER_ID):
    """The rows of the registry of the credit operations."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ref, kind, state, attempt FROM growth_copilot_credit_ops "
                "WHERE user_id = %s ORDER BY ref, kind",
                (int(user_id),),
            )
            return [(row[0], row[1], row[2], int(row[3])) for row in cur.fetchall()]


def _make_claim_stale(dsn: str) -> None:
    """Make the claim of the document slot older than the stale window."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE growth_copilot_documents SET created_at = NOW() - INTERVAL '1 hour' "
                "WHERE user_id = %s",
                (USER_ID,),
            )


def _make_ai_claim_stale(dsn: str) -> None:
    """Make the claim of the analysis slot older than the stale window."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE growth_copilot_ai_reports SET updated_at = NOW() - INTERVAL '1 hour' "
                "WHERE user_id = %s",
                (USER_ID,),
            )


def _ai_report(dsn: str):
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT model, answer FROM growth_copilot_ai_reports "
                "WHERE user_id = %s AND job_id = %s",
                (USER_ID, REF),
            )
            row = cur.fetchone()
            return tuple(row) if row else None


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
        "granted": sum(1 for item in results if isinstance(item, int) and item > 0),
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
            USER_ID, 1, REASON_RELEASE, REF, reserved_reason=REASON_SPEND, attempt=1
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
            USER_ID, 1, REASON_RELEASE, REF, reserved_reason=REASON_SPEND, attempt=1
        )
        store.reserve_credit(USER_ID, 1, REASON_SPEND, REF, release_reason=REASON_RELEASE)
    finally:
        store._store.close()
    journal = _journal(dsn)
    total = sum(delta for _reason, delta, _ref in journal)
    return {"balance": _balance(dsn), "journal_sum": total, "journal": journal}


def scenario_reservation_without_claim(dsn: str) -> dict:
    """The reservation of one operation without a claim.

    The reservation used to count the journal of the operation, and a
    parallel transaction cannot see the event of the other one yet, so two
    parallel reservations of one operation charged the user twice. The state
    of the operation is a row of the registry now, and the database refuses a
    second reservation of the same attempt, so the user is charged once.
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
        "granted": sum(1 for item in results if isinstance(item, int) and item > 0),
        "spends": sum(1 for r, _d, _r2 in journal if r == REASON_SPEND),
        "balance": _balance(dsn),
    }


def scenario_many_parallel_reservations(dsn: str, calls: int = 8) -> dict:
    """Many parallel reservations of one operation charge the user once."""
    _reset(dsn)

    def reserve(store):
        return store.reserve_credit(
            USER_ID, 1, REASON_SPEND, REF, release_reason=REASON_RELEASE
        )

    results = _parallel(dsn, [reserve] * calls)
    journal = _journal(dsn)
    return {
        "granted": sum(1 for item in results if isinstance(item, int) and item > 0),
        "spends": sum(1 for r, _d, _r2 in journal if r == REASON_SPEND),
        "balance": _balance(dsn),
    }


def scenario_parallel_releases(dsn: str, calls: int = 4) -> dict:
    """Many parallel releases of one reservation give the credit back once.

    The release is the mirror image of the reservation: a user who gets the
    credit back twice would be able to spend it again for free.
    """
    _reset(dsn)
    store = _store(dsn)
    try:
        store.reserve_credit(USER_ID, 1, REASON_SPEND, REF, release_reason=REASON_RELEASE)
    finally:
        store._store.close()

    def release(store):
        return store.release_credit(
            USER_ID, 1, REASON_RELEASE, REF, reserved_reason=REASON_SPEND, attempt=1
        )

    results = _parallel(dsn, [release] * calls)
    journal = _journal(dsn)
    return {
        "granted": sum(1 for item in results if item is True),
        "releases": sum(1 for r, _d, _r2 in journal if r == REASON_RELEASE),
        "balance": _balance(dsn),
    }


def scenario_reservation_without_balance(dsn: str) -> dict:
    """A reservation without balance changes nothing.

    The reservation must not leave the operation of the user in a paid state
    when the balance is empty, or the user would get the document for free.
    """
    _reset(dsn)
    _balance_set(dsn, 0)
    store = _store(dsn)
    try:
        reserved = store.reserve_credit(
            USER_ID, 1, REASON_SPEND, REF, release_reason=REASON_RELEASE
        )
    finally:
        store._store.close()
    journal = _journal(dsn)
    return {
        "reserved": bool(reserved),
        "spends": len(journal),
        "balance": _balance(dsn),
        "operations": _operations(dsn),
    }


def scenario_expired_claim_two_reservations(dsn: str) -> dict:
    """A claim that expired in the middle of the build cannot charge twice.

    The first build holds the claim of the vacancy, the claim goes stale while
    the build waits, the second build takes the claim over and both builds
    reserve the credit of the same vacancy. The user is charged once.
    """
    _reset(dsn)

    def first_build(store):
        claim = store.claim_document(USER_ID, REF, "cv.pdf")
        if not claim:
            return {"claim": None, "reserved": False}
        # The claim goes stale while the build waits for the credit check.
        _make_claim_stale(dsn)
        second = store.claim_document(USER_ID, REF, "cv.pdf")
        reserved = store.reserve_credit(
            USER_ID, 1, REASON_SPEND, REF, release_reason=REASON_RELEASE
        )
        return {"claim": claim, "second": second, "reserved": bool(reserved)}

    results = _parallel(dsn, [first_build, first_build])
    journal = _journal(dsn)
    return {
        "results": results,
        "granted": sum(1 for item in results if item.get("reserved")),
        "spends": sum(1 for r, _d, _r2 in journal if r == REASON_SPEND),
        "balance": _balance(dsn),
    }


def scenario_late_write_after_release(dsn: str) -> dict:
    """A build whose slot was released cannot write its file.

    The build of the first owner loses the slot of the vacancy, the second
    owner takes it over and gives it back, and the first owner writes its file
    afterwards. The write is refused and the slot stays free: the old upsert
    created the row again and delivered a file that nobody owns.
    """
    _reset(dsn)
    store = _store(dsn)
    try:
        first = store.claim_document(USER_ID, REF, "cv.pdf")
        _make_claim_stale(dsn)
        second = store.claim_document(USER_ID, REF, "cv.pdf")
        released = store.release_document_claim(USER_ID, REF, second)
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
        "released": released,
        "late_write": late_write,
        "row": row,
    }


def scenario_late_report_after_release(dsn: str) -> dict:
    """An analysis whose slot was released cannot write its answer."""
    _reset(dsn)
    store = _store(dsn)
    try:
        first = store.claim_ai_report(USER_ID, REF)
        _make_ai_claim_stale(dsn)
        second = store.claim_ai_report(USER_ID, REF)
        released = store.release_ai_report_claim(USER_ID, REF, second)
        late_write = store.save_ai_report(
            USER_ID, REF, claim=first, model="m", answer="late answer"
        )
        row = _ai_report(dsn)
    finally:
        store._store.close()
    return {
        "first": first,
        "second": second,
        "released": released,
        "late_write": late_write,
        "row": row,
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
    "many_parallel_reservations": scenario_many_parallel_reservations,
    "parallel_releases": scenario_parallel_releases,
    "reservation_without_balance": scenario_reservation_without_balance,
    "expired_claim_two_reservations": scenario_expired_claim_two_reservations,
    "late_write_after_release": scenario_late_write_after_release,
    "late_report_after_release": scenario_late_report_after_release,
}


def run_scenarios(dsn: str) -> dict:
    return {name: func(dsn) for name, func in SCENARIOS.items()}


class CreditConcurrencyTests(unittest.TestCase):
    """The credit reservation on a real PostgreSQL server."""

    @classmethod
    def setUpClass(cls):
        dsn = os.environ.get(DSN_ENV)
        if not dsn and os.environ.get("CI"):
            raise RuntimeError("CI must provide COPILOT_TEST_PG_DSN")
        if not dsn:
            raise unittest.SkipTest("COPILOT_TEST_PG_DSN is not set: no PostgreSQL for the concurrency check")
        validate_test_dsn(dsn)
        cls.dsn = dsn

    def test_p13_backfill_ignores_unmapped_refs_and_audit_finds_them(self):
        import psycopg
        from pathlib import Path
        from tools.verify_release_v7 import HISTORICAL_CREDIT_REFS_QUERY, EMPTY_CREDIT_REFS_QUERY
        _reset(self.dsn)
        migration = (Path(__file__).parent / "supabase/migrations/20261021060000_p13_copilot_credit_operations.sql").read_text(encoding="utf-8")
        backfill = "WITH spends AS (" + migration.split("WITH spends AS (", 1)[1]
        valid_ref = "a" * 40
        refs = ("cv", "pdf", "", "legacy-ref", "A" * 40, valid_ref)
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            for ref in refs:
                conn.execute("INSERT INTO growth_copilot_credit_events (user_id, delta, reason, ref) VALUES (%s, -1, 'spend', %s)", (USER_ID, ref))
            legacy = conn.execute(HISTORICAL_CREDIT_REFS_QUERY).fetchall()
            empty = conn.execute(EMPTY_CREDIT_REFS_QUERY).fetchall()
            self.assertEqual({row[0] for row in legacy}, {"cv", "pdf", "legacy-ref", "A" * 40})
            self.assertEqual([(row[0], row[2]) for row in empty], [("", 1)])
            conn.execute(backfill)
            conn.execute(backfill)
            rows = conn.execute("SELECT ref, kind, state, attempt FROM growth_copilot_credit_ops").fetchall()
            self.assertEqual(rows, [(valid_ref, "spend", "reserved", 1)])
            conn.execute("DELETE FROM growth_copilot_credit_events WHERE ref <> %s", (valid_ref,))
            self.assertEqual(conn.execute(HISTORICAL_CREDIT_REFS_QUERY).fetchall(), [])
            self.assertEqual(conn.execute(EMPTY_CREDIT_REFS_QUERY).fetchall(), [])

    def _abandoned(self, kind, *, stale=True):
        import psycopg
        _reset(self.dsn)
        store = _store(self.dsn)
        self.addCleanup(store._store.close)
        claim_fn = (lambda: store.claim_document(USER_ID, REF, "cv.pdf")) if kind == "spend" else (lambda: store.claim_ai_report(USER_ID, REF))
        first = claim_fn()
        a = CostedOperation(store, USER_ID, REF, spend_reason=kind).bind_claim(first)
        a.reserve()
        (_make_claim_stale if kind == "spend" else _make_ai_claim_stale)(self.dsn)
        if stale:
            with psycopg.connect(self.dsn, autocommit=True) as conn:
                conn.execute("UPDATE growth_copilot_credit_ops SET updated_at = NOW() - INTERVAL '1 hour'")
        second = claim_fn()
        self.assertTrue(second)
        self.assertNotEqual(first, second)
        b = CostedOperation(store, USER_ID, REF, spend_reason=kind).bind_claim(second)
        return store, a, b, second

    def test_abandoned_reservation_is_adopted_without_second_spend(self):
        for kind in ("spend", "llm_call"):
            with self.subTest(kind=kind):
                store, a, b, claim = self._abandoned(kind)
                self.assertEqual(b.reserve(), "reserved")
                self.assertEqual(b.attempt, a.attempt)
                self.assertTrue(b.can_deliver())
                self.assertEqual(b.confirm(), "delivered")
                self.assertEqual(_operations(self.dsn), [(REF, kind, "delivered", 1)])
                self.assertEqual(_balance(self.dsn), BALANCE - 1)
                self.assertEqual(sum(reason == kind for reason, _, _ in _journal(self.dsn)), 1)

    def test_adopted_ambiguous_delivery_keeps_credit_spent(self):
        for kind in ("spend", "llm_call"):
            with self.subTest(kind=kind):
                store, a, b, claim = self._abandoned(kind)
                b.reserve()
                self.assertEqual(b.mark_unknown(), "unknown")
                self.assertEqual(_operations(self.dsn), [(REF, kind, "unknown", 1)])
                self.assertEqual(_balance(self.dsn), BALANCE - 1)
                self.assertEqual(sum(delta for _, delta, _ in _journal(self.dsn)), -1)

    def test_fresh_reservation_cannot_be_adopted(self):
        for kind in ("spend", "llm_call"):
            with self.subTest(kind=kind):
                store, a, b, claim = self._abandoned(kind, stale=False)
                with self.assertRaises(CreditStoreUnavailable):
                    b.reserve()
                self.assertEqual(_balance(self.dsn), BALANCE - 1)
                self.assertEqual(len(_journal(self.dsn)), 1)

    def test_adoption_blocks_old_owners_settlement(self):
        for kind in ("spend", "llm_call"):
            with self.subTest(kind=kind):
                store, a, b, claim = self._abandoned(kind)
                b.reserve()
                journal = _journal(self.dsn)
                for settle in (a.confirm, a.release, a.mark_unknown):
                    with self.assertRaises(CreditStoreUnavailable):
                        settle()
                self.assertFalse(a.can_deliver())
                self.assertEqual(_journal(self.dsn), journal)
                self.assertEqual(_operations(self.dsn), [(REF, kind, "reserved", 1)])
                self.assertEqual(_balance(self.dsn), BALANCE - 1)

    def test_parallel_adoption_is_exclusive(self):
        for kind in ("spend", "llm_call"):
            with self.subTest(kind=kind):
                store, a, b, claim = self._abandoned(kind)
                self.assertFalse(store.reserve_credit(USER_ID, 1, kind, REF, claim="claim:wrong"))
                def adopt(other):
                    return other.reserve_credit(USER_ID, 1, kind, REF, claim=claim)
                results = _parallel(self.dsn, [adopt, adopt])
                self.assertEqual(sum(value is not False for value in results), 1)
                self.assertEqual(_balance(self.dsn), BALANCE - 1)
                self.assertEqual(len(_journal(self.dsn)), 1)
                self.assertFalse(store.reserve_credit(USER_ID, 1, kind, REF, claim="claim:wrong"))

    def test_p14_migration_is_idempotent_and_cleans_legacy_model_tokens(self):
        import psycopg
        from pathlib import Path
        _reset(self.dsn)
        migration = (Path(__file__).parent / "supabase/migrations/20261021070000_p14_copilot_ai_report_claim.sql").read_text(encoding="utf-8")
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute("INSERT INTO growth_copilot_ai_reports (user_id, job_id, model) VALUES (%s, %s, %s)",
                         (USER_ID, REF, "claim:legacy"))
            conn.execute(migration)
            conn.execute(migration)
            row = conn.execute("SELECT model, claim FROM growth_copilot_ai_reports WHERE user_id = %s AND job_id = %s",
                               (USER_ID, REF)).fetchone()
        self.assertEqual(row, ("", ""))

    def test_ai_claim_survives_save_and_resume_slot_reopens(self):
        _reset(self.dsn)
        store = _store(self.dsn)
        self.addCleanup(store._store.close)
        for used_resume in (False, True):
            with self.subTest(used_resume=used_resume):
                claim = store.claim_ai_report(USER_ID, REF)
                self.assertTrue(claim)
                self.assertTrue(store.save_ai_report(USER_ID, REF, claim=claim,
                    model="test-model", answer="private" if used_resume else "answer", used_resume=used_resume))
                self.assertTrue(store.owns_claim(USER_ID, REF, claim, "llm_call"))
                self.assertEqual(store.get_ai_report(USER_ID, REF)["model"], "test-model")
                if used_resume:
                    self.assertEqual(store.get_ai_report(USER_ID, REF)["answer"], "")
                self.assertTrue(store.release_ai_report_claim(USER_ID, REF, claim))
                self.assertFalse(store.owns_claim(USER_ID, REF, claim, "llm_call"))
        self.assertTrue(store.claim_ai_report(USER_ID, REF))

    def test_reject_b_then_timeout_a_keeps_credit_spent(self):
        _reset(self.dsn)
        store = _store(self.dsn)
        self.addCleanup(store._store.close)
        a = CostedOperation(store, USER_ID, REF)
        b = CostedOperation(store, USER_ID, REF)
        a.reserve()
        self.assertEqual(a.attempt, 1)
        with self.assertRaises(CreditStoreUnavailable):
            b.reserve()
        with self.assertRaises(CreditStoreUnavailable):
            b.release()
        self.assertEqual(_balance(self.dsn), BALANCE - 1)
        self.assertEqual(a.mark_unknown(), "unknown")
        self.assertEqual(_operations(self.dsn), [(REF, REASON_SPEND, "unknown", 1)])
        self.assertEqual(_balance(self.dsn), BALANCE - 1)

    def test_timeout_a_then_late_release_b_keeps_credit_spent(self):
        _reset(self.dsn)
        store = _store(self.dsn)
        self.addCleanup(store._store.close)
        a = CostedOperation(store, USER_ID, REF)
        b = CostedOperation(store, USER_ID, REF)
        a.reserve()
        with self.assertRaises(CreditStoreUnavailable):
            b.reserve()
        self.assertEqual(a.mark_unknown(), "unknown")
        with self.assertRaises(CreditStoreUnavailable):
            b.release()
        self.assertFalse(store.release_credit(
            USER_ID, 1, REASON_RELEASE, REF, reserved_reason=REASON_SPEND, attempt=2
        ))
        self.assertEqual(_operations(self.dsn), [(REF, REASON_SPEND, "unknown", 1)])
        self.assertEqual(_balance(self.dsn), BALANCE - 1)

    def test_stale_attempt_cannot_settle_new_reservation(self):
        _reset(self.dsn)
        store = _store(self.dsn)
        self.addCleanup(store._store.close)
        first = store.reserve_credit(USER_ID, 1, REASON_SPEND, REF)
        self.assertTrue(store.release_credit(
            USER_ID, 1, REASON_RELEASE, REF, reserved_reason=REASON_SPEND, attempt=first
        ))
        second = store.reserve_credit(USER_ID, 1, REASON_SPEND, REF)
        self.assertEqual(second, first + 1)
        journal = _journal(self.dsn)
        self.assertFalse(store.release_credit(
            USER_ID, 1, REASON_RELEASE, REF, reserved_reason=REASON_SPEND, attempt=first
        ))
        self.assertFalse(store.confirm_credit_operation(
            USER_ID, REF, REASON_SPEND, "cv_delivered", attempt=first
        ))
        self.assertFalse(store.mark_credit_operation_unknown(
            USER_ID, REF, REASON_SPEND, "cv_delivery_unknown", attempt=first
        ))
        self.assertEqual(_journal(self.dsn), journal)
        self.assertEqual(_operations(self.dsn), [(REF, REASON_SPEND, "reserved", second)])
        self.assertEqual(_balance(self.dsn), BALANCE - 1)

    def test_takeover_blocks_delivery_and_all_settlement(self):
        for kind in ("spend", "llm_call"):
            with self.subTest(kind=kind):
                _reset(self.dsn)
                store = _store(self.dsn)
                try:
                    claim_fn = (lambda uid, ref: store.claim_document(uid, ref, "cv.pdf")) if kind == "spend" else store.claim_ai_report
                    first = claim_fn(USER_ID, REF)
                    a = CostedOperation(store, USER_ID, REF, spend_reason=kind).bind_claim(first)
                    a.reserve()
                    self.assertTrue(a.can_deliver())
                    stale_fn = _make_claim_stale if kind == "spend" else _make_ai_claim_stale
                    stale_fn(self.dsn)
                    second = claim_fn(USER_ID, REF)
                    self.assertNotEqual(first, second)
                    self.assertFalse(a.can_deliver())
                    for settle in (a.release, a.confirm, a.mark_unknown):
                        with self.assertRaises(CreditStoreUnavailable):
                            settle()
                    self.assertFalse(store.release_credit(
                        USER_ID, 1, REASON_RELEASE, REF, reserved_reason=kind,
                        attempt=a.attempt, claim=first
                    ))
                    self.assertEqual(_balance(self.dsn), BALANCE - 1)
                    self.assertEqual(_operations(self.dsn), [(REF, kind, "reserved", 1)])
                finally:
                    store._store.close()

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

    # ------------------------------------------------------------------
    # The reservation of one operation cannot charge the user twice.
    # ------------------------------------------------------------------
    def test_two_parallel_reservations_of_one_operation_charge_once(self):
        """The reservation alone is safe, without a claim in front of it.

        The reservation used to count the journal, and a parallel transaction
        does not see the event of the other one yet, so both of them charged
        the user. The state of the operation is a row of the registry now.
        """
        result = scenario_reservation_without_claim(self.dsn)
        self.assertEqual(result["granted"], 1, result)
        self.assertEqual(result["spends"], 1, result)
        self.assertEqual(result["balance"], BALANCE - 1, result)

    def test_many_parallel_reservations_of_one_operation_charge_once(self):
        result = scenario_many_parallel_reservations(self.dsn, calls=8)
        self.assertEqual(result["granted"], 1, result)
        self.assertEqual(result["spends"], 1, result)
        self.assertEqual(result["balance"], BALANCE - 1, result)

    def test_many_parallel_releases_give_the_credit_back_once(self):
        result = scenario_parallel_releases(self.dsn, calls=4)
        self.assertEqual(result["granted"], 1, result)
        self.assertEqual(result["releases"], 1, result)
        self.assertEqual(result["balance"], BALANCE, result)

    def test_a_reservation_without_balance_charges_nothing(self):
        """The operation must not stay paid for when the balance is empty."""
        result = scenario_reservation_without_balance(self.dsn)
        self.assertFalse(result["reserved"], result)
        self.assertEqual(result["spends"], 0, result)
        self.assertEqual(result["balance"], 0, result)
        self.assertEqual(result["operations"], [], result)

    def test_a_claim_that_expired_in_the_middle_cannot_charge_twice(self):
        """The build of a claim that expired does not charge the user again."""
        result = scenario_expired_claim_two_reservations(self.dsn)
        self.assertEqual(result["granted"], 1, result)
        self.assertEqual(result["spends"], 1, result)
        self.assertEqual(result["balance"], BALANCE - 1, result)

    # ------------------------------------------------------------------
    # The write of a result needs the claim of its owner.
    # ------------------------------------------------------------------
    def test_a_write_after_the_slot_was_released_is_refused(self):
        """The old upsert created the row again and delivered a lost file."""
        result = scenario_late_write_after_release(self.dsn)
        self.assertTrue(result["first"])
        self.assertTrue(result["second"])
        self.assertTrue(result["released"])
        self.assertFalse(result["late_write"], result)
        self.assertIsNone(result["row"], result)

    def test_a_report_after_the_slot_was_released_is_refused(self):
        """The same rule for the answer of the AI analysis."""
        result = scenario_late_report_after_release(self.dsn)
        self.assertTrue(result["first"])
        self.assertTrue(result["second"])
        self.assertTrue(result["released"])
        self.assertFalse(result["late_write"], result)
        self.assertIsNone(result["row"], result)


if __name__ == "__main__":
    unittest.main()
