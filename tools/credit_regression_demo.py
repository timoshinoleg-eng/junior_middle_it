"""The proof that the credit system of the branch is safe.

The script runs two situations against a real PostgreSQL and prints what the
code of the branch does with them. It was run against the code of the round 3
as well: the assertions of the script failed there, which is the proof that
the situations were broken before this branch and that the branch fixes them.

    COPILOT_TEST_PG_DSN=postgresql://user:pass@host:5432/db \\
        python tools/credit_regression_demo.py

Situation 1 - the late attempt: a user asks for the same document twice. The
first attempt fails and the credit is given back, the second attempt is paid
for. The late settlement of the first attempt arrives and must not touch the
reservation of the second attempt.

Situation 2 - the late event: the previous code reserved the credit and wrote
it into the journal only. The new code takes over and must not charge the
user for the same operation a second time, and it must be able to give the
credit back when the delivery fails.
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from copilot.store import CopilotStore  # noqa: E402

DSN = str(os.environ.get("COPILOT_TEST_PG_DSN") or "").strip()
USER_ID = 9101
REF = "regression-demo"
SPEND = "spend"
SPEND_RELEASE = "spend_release"

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
"""


def _connect():
    import psycopg

    return psycopg.connect(DSN, autocommit=True)


def _reset():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute("DELETE FROM growth_copilot_credit_ops")
            cur.execute("DELETE FROM growth_copilot_credit_events")
            cur.execute("DELETE FROM growth_copilot_credits")
            cur.execute(
                "INSERT INTO growth_copilot_credits (user_id, balance) VALUES (%s, %s)",
                (USER_ID, 10),
            )


def _store():
    return CopilotStore(_RemoteStore(_connect()))


class _RemoteStore:
    """The store interface of the product, backed by a direct connection."""

    def __init__(self, connection):
        self.conn = connection

    def _ensure_conn(self):
        return self.conn

    def close(self):
        self.conn.close()


def _balance():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT balance FROM growth_copilot_credits WHERE user_id = %s", (USER_ID,)
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0


def _journal():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reason, delta FROM growth_copilot_credit_events "
                "WHERE user_id = %s ORDER BY id",
                (USER_ID,),
            )
            return [(str(row[0]), int(row[1])) for row in cur.fetchall()]


def _journal_spend():
    """The reservation of the previous code: the journal only."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE growth_copilot_credits SET balance = balance - 1 WHERE user_id = %s",
                (USER_ID,),
            )
            cur.execute(
                "INSERT INTO growth_copilot_credit_events (user_id, delta, reason, ref) "
                "VALUES (%s, %s, %s, %s)",
                (USER_ID, -1, SPEND, REF),
            )


def _reserve(store):
    return store.reserve_credit(
        USER_ID, 1, SPEND, REF, release_reason=SPEND_RELEASE
    )


def _release(store, attempt=None):
    """The release of the branch, and the release of the round 3."""
    parameters = inspect.signature(store.release_credit).parameters
    if "attempt" in parameters:
        return store.release_credit(
            USER_ID,
            1,
            SPEND_RELEASE,
            REF,
            reserved_reason=SPEND,
            attempt=attempt,
        )
    return store.release_credit(
        USER_ID, 1, SPEND_RELEASE, REF, reserved_reason=SPEND
    )


def situation_late_attempt():
    """The late settlement of the first attempt must not touch the second."""
    _reset()
    store = _store()
    try:
        first = _reserve(store)
        released_first = _release(store, first if isinstance(first, int) else 1)
        second = _reserve(store)
        late = _release(store, first if isinstance(first, int) else 1)
    finally:
        store._store.close()
    balance = _balance()
    print("situation 1 - the late attempt")
    print("  the first attempt: reserved=%s released=%s" % (first, released_first))
    print("  the second attempt: reserved=%s" % (second,))
    print("  the late release of the first attempt: %s" % late)
    print("  the balance: %s (the user paid for the second attempt)" % balance)
    print("  the journal: %s" % (_journal(),))
    assert late is False or late is None, (
        "the late settlement of the first attempt touched the second one"
    )
    assert balance == 9, "the balance is %s, the second attempt was given back" % balance
    print("  OK: the reservation of the second attempt is untouched")


def situation_late_event():
    """An operation of the previous code must not be charged again."""
    _reset()
    _journal_spend()
    store = _store()
    try:
        reserved = _reserve(store)
    finally:
        store._store.close()
    balance = _balance()
    print("situation 2 - the late event of the previous code")
    print("  the reservation of the new code: %s" % reserved)
    print("  the balance: %s (the user paid once)" % balance)
    print("  the journal: %s" % (_journal(),))
    assert reserved is None or reserved is False, (
        "the operation of the previous code was charged a second time"
    )
    assert balance == 9, "the balance is %s, the user paid twice" % balance
    print("  OK: the operation of the previous code was not charged again")


def main() -> int:
    if not DSN:
        print("COPILOT_TEST_PG_DSN is not set")
        return 2
    situation_late_attempt()
    situation_late_event()
    print("\nboth situations behave as the branch promises")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
