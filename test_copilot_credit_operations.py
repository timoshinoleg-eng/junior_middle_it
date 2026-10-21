"""Regression tests for the credit correctness of the costed Copilot actions.

The twelve cases of release stage A live here: a successful build and
delivery, an insufficient balance, a database error, a failing PDF generator,
a failing Telegram call, an ambiguous delivery, two parallel requests, a
repeated operation, a repeated delivery from the cache, the agreement of the
balance and the journal, the daily allowance and one user in two channels.

The store is not mocked away: the real statements of ``CopilotStore`` run
against an in-memory growth backend, so the tests check the SQL that the Edge
bridge would receive.
"""
import base64
import hashlib
import hmac
import io
import json
import os
import re
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

from api import copilot as api_copilot
from copilot import credit_operations as credit_ops
from copilot import credits as copilot_credits
from copilot import webapp as copilot_webapp
from copilot import webapp_auth
from copilot.store import CopilotStore

import copilot_webhook_runtime as copilot_runtime


BOT_TOKEN = "123456:TEST-TOKEN"
AUTH_DATE = int(time.time())

JOB = {
    "hash": "a" * 40,
    "title": "Junior Python Developer",
    "company": "Acme",
    "description": "Python, FastAPI, PostgreSQL. Опыт 1-2 года.",
    "requirements": "",
    "tags": [],
    "url": "https://example.com/jobs/1",
    "source": "TG:@junior_dev",
}

RESUME_TEXT = (
    "Иван Петров, python разработчик, 3 года опыта. "
    "Работал с Python, FastAPI, PostgreSQL, Docker и pytest. "
    "Telegram: @ivan_dev, почта ivan.petrov@example.com, телефон +7 999 123-45-67."
)


def build_init_data(params=None, token=BOT_TOKEN, auth_date=None):
    """Build a signed initData string, like Telegram does."""
    payload = {
        "auth_date": str(auth_date if auth_date is not None else AUTH_DATE),
        "query_id": "AAH_test",
        "user": json.dumps({"id": 42, "first_name": "Иван"}),
    }
    payload.update(params or {})
    secret = webapp_auth.compute_secret_key(token)
    signature = hmac.new(
        secret,
        webapp_auth.data_check_string(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    payload["hash"] = signature
    return urlencode(payload)


class FakeCursor:
    """The cursor of the in-memory growth backend."""

    def __init__(self, backend):
        self.backend = backend
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=()):
        self.backend.execute(query, params)
        self._rows = self.backend._rows
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeGrowthStore:
    """In-memory growth backend that answers the Copilot statements.

    Every answer follows the same rule as the SQL of ``copilot/store.py``: a
    reservation is only granted when no reservation of the same operation is
    unmatched, and a refund is only granted when a reservation is unmatched.
    """

    def __init__(self):
        self.credits = {}
        self.initial_balance = 0
        self.granted_today = set()
        self.events = []
        self.documents = {}
        self.rate_limits = {}
        self.clock = 0.0
        self.failures = set()
        self.statements = []
        self._rows = []

    # -- transport -------------------------------------------------
    def _ensure_conn(self):
        return self

    def cursor(self):
        return FakeCursor(self)

    def advance(self, minutes=0):
        self.clock += float(minutes) * 60.0

    def seed_credits(self, user_id, balance):
        self.credits[int(user_id)] = int(balance)

    def _balance(self, user_id):
        # The fake starts every user with the given balance, so the tests do
        # not depend on the order of the statements.
        if int(user_id) in self.credits:
            return int(self.credits[int(user_id)])
        return int(self.initial_balance)

    def _count_events(self, user_id, reason, ref):
        return sum(
            1
            for uid, _delta, item_reason, item_ref in self.events
            if int(uid) == int(user_id)
            and str(item_reason) == str(reason)
            and str(item_ref) == str(ref)
        )

    def _guard(self, marker):
        if marker in self.failures or "any" in self.failures:
            raise RuntimeError("growth backend is down")

    def execute(self, query, params=()):
        self.statements.append((str(query), tuple(params or ())))
        sql = re.sub(r"\s+", " ", str(query)).strip()
        values = list(params or ())
        self._rows = []
        if "WITH debit AS" in sql:
            self._guard("debit")
            self._rows = self._reserve(values)
        elif "WITH refund AS" in sql:
            self._guard("refund")
            self._rows = self._refund(values)
        elif "WITH granted AS" in sql:
            self._guard("grant")
            self._rows = self._grant(values)
        elif "SELECT reason, COUNT(*)" in sql:
            self._rows = self._counts(values)
        elif "INSERT INTO growth_copilot_documents" in sql and "EXCLUDED.telegram_file_id" in sql:
            self._rows = self._save_document(values)
        elif "INSERT INTO growth_copilot_documents" in sql and "DO UPDATE" in sql:
            self._guard("claim")
            self._rows = self._claim(values)
        elif "DELETE FROM growth_copilot_documents" in sql:
            self._rows = self._release_claim(values)
        elif "INSERT INTO growth_copilot_documents" in sql:
            self._rows = self._save_document(values)
        elif "SELECT telegram_file_id" in sql:
            self._rows = self._get_document(values)
        elif "INSERT INTO growth_copilot_rate_limits" in sql:
            self._rows = self._bump_rate_limit(values)
        elif "FROM growth_copilot_forwarded_jobs" in sql:
            self._rows = []
        elif "INSERT INTO growth_copilot_credit_events" in sql:
            self._guard("journal")
            self._rows = self._log_event(values)
        elif "SELECT balance FROM growth_copilot_credits" in sql:
            self._rows = [(self._balance(values[0]),)]
        elif "INSERT INTO growth_copilot_credits" in sql:
            self._rows = self._ensure_credit_row(values)
        elif "SELECT COUNT(*) FROM growth_copilot_credit_events" in sql:
            self._rows = [(self._count_reason(values[0]),)]
        else:
            raise AssertionError("unexpected statement: %s" % sql[:140])

    # -- credit statements -----------------------------------------
    def _ensure_credit_row(self, values):
        user_id = int(values[0])
        self.credits.setdefault(user_id, int(self.initial_balance))
        return [(1,)]

    def _grant(self, values):
        amount = int(values[0])
        user_id = int(values[1])
        reason = str(values[4])
        ref = str(values[5])
        if user_id in self.granted_today:
            return []
        self.granted_today.add(user_id)
        self.credits[user_id] = self._balance(user_id) + amount
        self.events.append((user_id, amount, reason, ref))
        return [(1,)]

    def _reserve(self, values):
        amount = int(values[0])
        user_id = int(values[1])
        ref = str(values[4])
        reason = str(values[5])
        release_reason = str(values[8])
        spends = self._count_events(user_id, reason, ref)
        releases = self._count_events(user_id, release_reason, ref)
        if spends > releases:
            return []
        if self._balance(user_id) < amount:
            return []
        self.credits[user_id] = self._balance(user_id) - amount
        self.events.append((user_id, -amount, reason, ref))
        return [(1,)]

    def _refund(self, values):
        amount = int(values[0])
        user_id = int(values[1])
        ref = str(values[3])
        reserved_reason = str(values[4])
        reason = str(values[7])
        spends = self._count_events(user_id, reserved_reason, ref)
        releases = self._count_events(user_id, reason, ref)
        if spends <= releases:
            return []
        self.credits[user_id] = self._balance(user_id) + amount
        self.events.append((user_id, amount, reason, ref))
        return [(1,)]

    def _log_event(self, values):
        user_id = int(values[0])
        delta = int(values[1])
        reason = str(values[2])
        ref = str(values[3])
        self.events.append((user_id, delta, reason, ref))
        return [(len(self.events),)]

    def _counts(self, values):
        user_id = int(values[0])
        ref = str(values[1])
        reasons = [str(item) for item in values[2:]]
        found = {}
        for reason in reasons:
            count = self._count_events(user_id, reason, ref)
            if count:
                found[reason] = count
        return [(reason, count) for reason, count in found.items()]

    def _count_reason(self, reason):
        return sum(1 for _uid, _delta, item_reason, _ref in self.events if item_reason == str(reason))

    # -- document statements ---------------------------------------
    def _claim(self, values):
        user_id = int(values[0])
        job_id = str(values[1])
        filename = str(values[2])
        stale_minutes = int(values[3])
        key = (user_id, job_id)
        row = self.documents.get(key)
        if row is None:
            self.documents[key] = {
                "telegram_file_id": "",
                "filename": filename,
                "fit_score": 0,
                "created_at": self.clock,
            }
            return [(job_id,)]
        if row["telegram_file_id"]:
            return []
        if self.clock - row["created_at"] >= stale_minutes * 60.0:
            row["created_at"] = self.clock
            return [(job_id,)]
        return []

    def _release_claim(self, values):
        key = (int(values[0]), str(values[1]))
        row = self.documents.get(key)
        if row and not row["telegram_file_id"]:
            del self.documents[key]
            return [(key[1],)]
        return []

    def _save_document(self, values):
        key = (int(values[0]), str(values[1]))
        self.documents[key] = {
            "telegram_file_id": str(values[2] or ""),
            "filename": str(values[3] or ""),
            "fit_score": int(values[4] or 0),
            "created_at": self.clock,
        }
        return [(key[1],)]

    def _get_document(self, values):
        key = (int(values[0]), str(values[1]))
        row = self.documents.get(key)
        if not row:
            return []
        return [
            (
                row["telegram_file_id"],
                row["filename"],
                row["fit_score"],
                row["created_at"],
            )
        ]

    def _bump_rate_limit(self, values):
        user_id = int(values[0])
        hits = self.rate_limits.get(user_id, 0) + 1
        self.rate_limits[user_id] = hits
        return [(hits,)]


def make_store(*, balance=0, failures=()):
    """A real Copilot store on top of the in-memory growth backend."""
    backend = FakeGrowthStore()
    backend.initial_balance = int(balance)
    backend.failures = set(failures)
    return CopilotStore(backend), backend


class FakeDb:
    """The bot database of the Mini App and of the Telegram runtime."""

    def __init__(self, backend=None, job=None):
        self._growth_store = backend
        self.job = job
        self.events = []
        self.settings = {"skills": "python, fastapi", "enabled_categories": ["development"]}

    def get_job_payload(self, job_hash):
        if self.job and str(job_hash) == str(self.job.get("hash")):
            return dict(self.job)
        return None

    def get_user_settings(self, user_id):
        return dict(self.settings)

    def log_event(self, user_id, name, props=None):
        self.events.append((name, dict(props or {})))


class CapturingHandler(api_copilot.handler):
    def _send_json(self, status, payload):
        self.captured_status = status
        self.captured_payload = payload


def call_api(store, db, payload, *, path="/api/copilot/pdf", env=None):
    """Run one Mini App request through the handler."""
    body = json.dumps(payload).encode("utf-8")
    handler = object.__new__(CapturingHandler)
    handler.rfile = io.BytesIO(body)
    handler.headers = {"content-length": str(len(body))}
    handler.path = path
    handler.captured_status = None
    handler.captured_payload = None
    environment = {"TELEGRAM_BOT_TOKEN": BOT_TOKEN}
    environment.update(env or {})
    with patch.dict(os.environ, environment, clear=False), patch.object(
        api_copilot.handler, "_runtime", new=lambda self: (db, None, store)
    ):
        handler.do_POST()
    return handler.captured_status, handler.captured_payload


def _build_resume_pdf():
    """A real PDF resume with a readable text layer."""
    from copilot import pdf as copilot_pdf

    contact = copilot_pdf.extract_contacts(RESUME_TEXT)
    return copilot_pdf.build_cv_pdf(
        contact=contact,
        role="Junior Python Developer",
        company="Acme",
        skills=["python", "fastapi", "postgresql"],
    )


# The fixture is built before any test patches the PDF builder.
RESUME_PDF_BASE64 = base64.b64encode(_build_resume_pdf()).decode("ascii")


def pdf_request(job_hash=None):
    return {
        "init_data": build_init_data(),
        "job_hash": job_hash or JOB["hash"],
        "resume": {
            "filename": "cv.pdf",
            "content_base64": RESUME_PDF_BASE64,
        },
    }


class FakeTelegramBot:
    """The bot of the Telegram runtime."""

    def __init__(self, *, failure=None, file_id="FILE-1"):
        self.failure = failure
        self.file_id = file_id
        self.sent = []

    async def send_document(self, chat_id, document, filename=None, caption=None, **kwargs):
        if self.failure is not None:
            raise self.failure
        self.sent.append({"chat_id": chat_id, "filename": filename, "caption": caption})
        return SimpleNamespace(document=SimpleNamespace(file_id=self.file_id))


class FakeMessage:
    def __init__(self, text=RESUME_TEXT):
        self.replies = []
        self.text = text
        self.document = None

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


def run_telegram_build(store, backend, *, failure=None, user_id=42):
    """Run one CV build through the Telegram runtime."""
    import asyncio

    db = FakeDb(backend, JOB)
    job_bot = SimpleNamespace(db=db)
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=message,
    )
    context = SimpleNamespace(bot=FakeTelegramBot(failure=failure))
    pending = {"job_hash": JOB["hash"], "job": dict(JOB)}
    asyncio.run(
        copilot_runtime._build_cv_from_message(job_bot, update, context, pending)
    )
    return message, db, context


def sent_document(store, backend):
    """The cached document of one user and vacancy."""
    return backend.documents.get((42, JOB["hash"]))


def event_reasons(backend, user_id=None):
    return [
        reason
        for uid, _delta, reason, _ref in backend.events
        if user_id is None or int(uid) == int(user_id)
    ]


class CreditOperationTests(unittest.TestCase):
    """The state machine of one costed operation."""

    def test_a_new_operation_has_no_state_yet(self):
        store, _backend = make_store(balance=3)
        operation = credit_ops.cv_operation(store, 42, JOB["hash"])
        self.assertEqual(operation.state(), credit_ops.STATE_NEW)

    def test_a_reservation_holds_the_credit(self):
        store, backend = make_store(balance=3)
        operation = credit_ops.cv_operation(store, 42, JOB["hash"])
        self.assertEqual(operation.reserve(), credit_ops.STATE_RESERVED)
        self.assertEqual(backend._balance(42), 2)

    def test_a_reservation_without_balance_is_refused(self):
        store, backend = make_store(balance=0)
        operation = credit_ops.cv_operation(store, 42, JOB["hash"])
        with self.assertRaises(credit_ops.InsufficientCredits):
            operation.reserve()
        self.assertEqual(backend._balance(42), 0)
        self.assertEqual(event_reasons(backend), [])

    def test_a_broken_store_does_not_report_a_reservation(self):
        store, backend = make_store(balance=3, failures=("debit",))
        operation = credit_ops.cv_operation(store, 42, JOB["hash"])
        with self.assertRaises(credit_ops.CreditStoreUnavailable):
            operation.reserve()

    def test_a_repeated_reservation_does_not_charge_twice(self):
        store, backend = make_store(balance=3)
        first = credit_ops.cv_operation(store, 42, JOB["hash"])
        second = credit_ops.cv_operation(store, 42, JOB["hash"])
        self.assertEqual(first.reserve(), credit_ops.STATE_RESERVED)
        self.assertEqual(second.reserve(), credit_ops.STATE_RESERVED)
        self.assertEqual(backend._balance(42), 2)
        self.assertEqual(event_reasons(backend).count(credit_ops.REASON_SPEND), 1)

    def test_a_release_gives_the_credit_back_once(self):
        store, backend = make_store(balance=3)
        operation = credit_ops.cv_operation(store, 42, JOB["hash"])
        operation.reserve()
        self.assertEqual(operation.release(), credit_ops.STATE_RELEASED)
        self.assertEqual(backend._balance(42), 3)
        self.assertEqual(operation.release(), credit_ops.STATE_RELEASED)
        self.assertEqual(backend._balance(42), 3)
        self.assertEqual(event_reasons(backend).count(credit_ops.REASON_RELEASE), 1)

    def test_a_request_after_a_refund_is_charged_again(self):
        store, backend = make_store(balance=3)
        operation = credit_ops.cv_operation(store, 42, JOB["hash"])
        operation.reserve()
        operation.release()
        again = credit_ops.cv_operation(store, 42, JOB["hash"])
        self.assertEqual(again.reserve(), credit_ops.STATE_RESERVED)
        self.assertEqual(backend._balance(42), 2)

    def test_an_ambiguous_delivery_keeps_the_credit_spent(self):
        store, backend = make_store(balance=3)
        operation = credit_ops.cv_operation(store, 42, JOB["hash"])
        operation.reserve()
        self.assertEqual(operation.mark_unknown(), credit_ops.STATE_UNKNOWN)
        self.assertEqual(backend._balance(42), 2)
        retry = credit_ops.cv_operation(store, 42, JOB["hash"])
        self.assertEqual(retry.state(), credit_ops.STATE_UNKNOWN)
        self.assertEqual(retry.reserve(), credit_ops.STATE_UNKNOWN)
        self.assertEqual(backend._balance(42), 2)

    def test_a_delivered_operation_is_not_charged_again(self):
        store, backend = make_store(balance=3)
        operation = credit_ops.cv_operation(store, 42, JOB["hash"])
        operation.reserve()
        self.assertEqual(operation.confirm(), credit_ops.STATE_DELIVERED)
        retry = credit_ops.cv_operation(store, 42, JOB["hash"])
        self.assertEqual(retry.reserve(), credit_ops.STATE_DELIVERED)
        self.assertEqual(backend._balance(42), 2)
        self.assertEqual(event_reasons(backend).count(credit_ops.REASON_SPEND), 1)

    def test_the_settlement_follows_the_delivery_outcome(self):
        store, backend = make_store(balance=3)
        delivered = credit_ops.cv_operation(store, 42, "job-a")
        delivered.reserve()
        self.assertEqual(
            credit_ops.settle_delivery(delivered, credit_ops.DELIVERY_DELIVERED),
            credit_ops.STATE_DELIVERED,
        )
        rejected = credit_ops.cv_operation(store, 42, "job-b")
        rejected.reserve()
        self.assertEqual(
            credit_ops.settle_delivery(rejected, credit_ops.DELIVERY_REJECTED),
            credit_ops.STATE_RELEASED,
        )
        unknown = credit_ops.cv_operation(store, 42, "job-c")
        unknown.reserve()
        self.assertEqual(
            credit_ops.settle_delivery(unknown, credit_ops.DELIVERY_UNKNOWN),
            credit_ops.STATE_UNKNOWN,
        )
        # Three reservations, one refund.
        self.assertEqual(backend._balance(42), 1)


class MiniAppCreditTests(unittest.TestCase):
    """The Mini App path: the credit is taken for the delivered document."""

    def setUp(self):
        patcher = patch.dict(os.environ, {"COPILOT_FREE_PER_DAY": "0"}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store, self.backend = make_store(balance=3)
        self.db = FakeDb(self.backend, JOB)

    def _call(self, payload=None):
        return call_api(self.store, self.db, payload or pdf_request())

    def test_a_successful_build_and_delivery_costs_one_credit(self):
        with patch.object(
            copilot_webapp, "send_document_to_chat", return_value={"file_id": "FILE-1"}
        ):
            status, payload = self._call()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.backend._balance(42), 2)
        reasons = event_reasons(self.backend)
        self.assertEqual(reasons.count(credit_ops.REASON_SPEND), 1)
        self.assertEqual(reasons.count(credit_ops.REASON_DELIVERED), 1)
        self.assertEqual(sent_document(self.store, self.backend)["telegram_file_id"], "FILE-1")

    def test_an_insufficient_balance_answers_402_and_charges_nothing(self):
        store, backend = make_store(balance=0)
        status, payload = call_api(store, FakeDb(backend, JOB), pdf_request())
        self.assertEqual(status, 402)
        self.assertEqual(payload["error"], "no_credits")
        self.assertEqual(backend._balance(42), 0)
        self.assertEqual(event_reasons(backend), [])

    def test_a_database_error_answers_503_and_charges_nothing(self):
        store, backend = make_store(balance=3, failures=("debit",))
        with patch.object(
            copilot_webapp, "send_document_to_chat", return_value={"file_id": "FILE-1"}
        ) as send:
            status, payload = call_api(store, FakeDb(backend, JOB), pdf_request())
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "store_unavailable")
        self.assertFalse(send.called)
        self.assertEqual(backend._balance(42), 3)
        self.assertEqual(event_reasons(backend), [])

    def test_a_failing_pdf_generator_keeps_the_credit(self):
        with patch.object(
            api_copilot.copilot_pdf, "build_cv_pdf", side_effect=RuntimeError("no font")
        ):
            status, payload = self._call()
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"], "cv_failed")
        self.assertEqual(self.backend._balance(42), 3)
        self.assertEqual(event_reasons(self.backend), [])
        self.assertEqual(self.backend.documents, {})

    def test_a_rejected_document_is_paid_back(self):
        error = copilot_webapp.WebappError(
            502, "telegram_error", "Telegram не принял файл.", delivery=copilot_webapp.DELIVERY_REJECTED
        )
        with patch.object(
            copilot_webapp, "send_document_to_chat", side_effect=error
        ):
            status, payload = self._call()
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"], "telegram_error")
        self.assertEqual(self.backend._balance(42), 3)
        reasons = event_reasons(self.backend)
        self.assertEqual(reasons.count(credit_ops.REASON_SPEND), 1)
        self.assertEqual(reasons.count(credit_ops.REASON_RELEASE), 1)

    def test_an_ambiguous_delivery_keeps_the_credit_spent(self):
        error = copilot_webapp.WebappError(
            502, "telegram_error", "Telegram недоступен.", delivery=copilot_webapp.DELIVERY_UNKNOWN
        )
        with patch.object(
            copilot_webapp, "send_document_to_chat", side_effect=error
        ):
            status, _payload = self._call()
        self.assertEqual(status, 502)
        self.assertEqual(self.backend._balance(42), 2)
        reasons = event_reasons(self.backend)
        self.assertEqual(reasons.count(credit_ops.REASON_SPEND), 1)
        self.assertEqual(reasons.count(credit_ops.REASON_UNKNOWN), 1)
        self.assertEqual(reasons.count(credit_ops.REASON_RELEASE), 0)

    def test_a_second_request_of_a_running_build_is_refused(self):
        # The first request holds the slot of the vacancy.
        self.backend._claim((42, JOB["hash"], "cv.pdf", 15))
        status, payload = self._call()
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "cv_in_progress")
        self.assertEqual(self.backend._balance(42), 3)
        self.assertEqual(event_reasons(self.backend), [])

    def test_a_repeated_request_of_the_same_vacancy_is_not_charged_twice(self):
        with patch.object(
            copilot_webapp, "send_document_to_chat", return_value={"file_id": "FILE-1"}
        ):
            status, _payload = self._call()
        self.assertEqual(status, 200)
        # The document is cached now, so the second request is free.
        with patch.object(
            copilot_webapp, "resend_document", return_value={"file_id": "FILE-1"}
        ) as resend:
            status, payload = self._call()
        self.assertEqual(status, 200)
        self.assertTrue(payload["cached"])
        self.assertTrue(resend.called)
        self.assertEqual(self.backend._balance(42), 2)
        self.assertEqual(event_reasons(self.backend).count(credit_ops.REASON_SPEND), 1)

    def test_a_cached_document_is_sent_again_without_a_credit(self):
        self.backend._save_document((42, JOB["hash"], "FILE-9", "cv.pdf", 70))
        with patch.object(
            copilot_webapp, "resend_document", return_value={"file_id": "FILE-9"}
        ) as resend:
            status, payload = self._call()
        self.assertEqual(status, 200)
        self.assertTrue(payload["cached"])
        self.assertTrue(resend.called)
        self.assertEqual(self.backend._balance(42), 3)
        self.assertEqual(event_reasons(self.backend), [])

    def test_an_already_paid_vacancy_is_not_blocked_by_an_empty_balance(self):
        with patch.object(
            copilot_webapp, "send_document_to_chat", return_value={"file_id": "FILE-1"}
        ):
            status, _payload = self._call()
        self.assertEqual(status, 200)
        # The document cache is lost and the balance is empty: the operation
        # was paid for already, so the user is not charged again.
        self.backend.documents.clear()
        self.backend.credits[42] = 0
        with patch.object(
            copilot_webapp, "send_document_to_chat", return_value={"file_id": "FILE-2"}
        ):
            status, _payload = self._call()
        self.assertEqual(status, 200)
        self.assertEqual(self.backend._balance(42), 0)
        self.assertEqual(event_reasons(self.backend).count(credit_ops.REASON_SPEND), 1)

    def test_the_balance_and_the_journal_agree_after_every_operation(self):
        with patch.object(
            copilot_webapp, "send_document_to_chat", return_value={"file_id": "FILE-1"}
        ):
            self._call()
        error = copilot_webapp.WebappError(
            502, "telegram_error", "Telegram недоступен.", delivery=copilot_webapp.DELIVERY_UNKNOWN
        )
        with patch.object(copilot_webapp, "send_document_to_chat", side_effect=error):
            call_api(
                self.store,
                self.db,
                dict(pdf_request(), job_hash="b" * 40),
            )
        journal = sum(delta for _uid, delta, _reason, _ref in self.backend.events)
        self.assertEqual(self.backend._balance(42), 3 + journal)

    def test_the_daily_allowance_is_granted_exactly_once(self):
        store, backend = make_store(balance=0)
        manager = copilot_credits.CreditManager(store)
        with patch.dict(os.environ, {"COPILOT_FREE_PER_DAY": "3"}, clear=False):
            self.assertEqual(manager.ensure_daily_allowance(42), 3)
            self.assertEqual(manager.ensure_daily_allowance(42), 3)
        self.assertEqual(backend._balance(42), 3)
        self.assertEqual(event_reasons(backend).count(copilot_credits.REASON_DAILY_GRANT), 1)

    def test_the_bot_and_the_mini_app_charge_the_same_vacancy_once(self):
        with patch.object(
            copilot_webapp, "send_document_to_chat", return_value={"file_id": "FILE-1"}
        ):
            status, _payload = self._call()
        self.assertEqual(status, 200)
        # The same vacancy through the Telegram bot: the credit is not taken
        # a second time.
        message, _db, _context = run_telegram_build(self.store, self.backend)
        self.assertTrue(message.replies)
        self.assertEqual(self.backend._balance(42), 2)
        self.assertEqual(event_reasons(self.backend).count(credit_ops.REASON_SPEND), 1)


class TelegramCreditTests(unittest.TestCase):
    """The Telegram path: the credit is taken for the delivered document."""

    def setUp(self):
        patcher = patch.dict(os.environ, {"COPILOT_FREE_PER_DAY": "0"}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_successful_build_and_delivery_costs_one_credit(self):
        store, backend = make_store(balance=3)
        message, db, _context = run_telegram_build(store, backend)
        self.assertTrue(message.replies)
        self.assertIn("Готово", message.replies[-1][0])
        self.assertEqual(backend._balance(42), 2)
        reasons = event_reasons(backend)
        self.assertEqual(reasons.count(credit_ops.REASON_SPEND), 1)
        self.assertEqual(reasons.count(credit_ops.REASON_DELIVERED), 1)
        self.assertTrue(any(name == "copilot_cv_built" for name, _props in db.events))

    def test_a_rejected_document_is_paid_back(self):
        from telegram import error as telegram_error

        store, backend = make_store(balance=3)
        message, _db, _context = run_telegram_build(
            store, backend, failure=telegram_error.BadRequest("chat not found")
        )
        self.assertTrue(message.replies)
        self.assertEqual(backend._balance(42), 3)
        reasons = event_reasons(backend)
        self.assertEqual(reasons.count(credit_ops.REASON_SPEND), 1)
        self.assertEqual(reasons.count(credit_ops.REASON_RELEASE), 1)

    def test_an_ambiguous_delivery_keeps_the_credit_spent(self):
        from telegram import error as telegram_error

        store, backend = make_store(balance=3)
        message, _db, _context = run_telegram_build(
            store, backend, failure=telegram_error.TimedOut("timeout")
        )
        self.assertTrue(message.replies)
        self.assertEqual(backend._balance(42), 2)
        reasons = event_reasons(backend)
        self.assertEqual(reasons.count(credit_ops.REASON_SPEND), 1)
        self.assertEqual(reasons.count(credit_ops.REASON_UNKNOWN), 1)
        self.assertEqual(reasons.count(credit_ops.REASON_RELEASE), 0)

    def test_a_failing_pdf_generator_keeps_the_credit(self):
        store, backend = make_store(balance=3)
        with patch.object(
            copilot_runtime.copilot_pdf, "build_cv_pdf", side_effect=RuntimeError("no font")
        ):
            message, _db, _context = run_telegram_build(store, backend)
        self.assertTrue(message.replies)
        self.assertEqual(backend._balance(42), 3)
        self.assertEqual(event_reasons(backend), [])
        self.assertEqual(backend.documents, {})

    def test_a_database_error_keeps_the_credit(self):
        store, backend = make_store(balance=3, failures=("debit",))
        message, _db, context = run_telegram_build(store, backend)
        self.assertTrue(message.replies)
        self.assertEqual(backend._balance(42), 3)
        self.assertEqual(event_reasons(backend), [])
        # The paid document must not reach the user when the credit could not
        # be confirmed: this is the defect the test has to keep closed.
        self.assertEqual(context.bot.sent, [])

    def test_a_store_outage_releases_the_document_slot(self):
        # The daily allowance fails after the slot was taken.
        store, backend = make_store(balance=3, failures=("grant",))
        with patch.dict(os.environ, {"COPILOT_FREE_PER_DAY": "3"}, clear=False):
            message, _db, context = run_telegram_build(store, backend)
        self.assertTrue(message.replies)
        self.assertEqual(context.bot.sent, [])
        self.assertEqual(backend._balance(42), 3)
        # The slot must be free again, otherwise the vacancy is blocked for
        # the next fifteen minutes.
        self.assertEqual(backend.documents, {})

    def test_a_telegram_rate_limit_is_a_proven_rejection(self):
        from telegram import error as telegram_error

        store, backend = make_store(balance=3)
        message, _db, _context = run_telegram_build(
            store, backend, failure=telegram_error.RetryAfter(5)
        )
        self.assertTrue(message.replies)
        # A flood control answer means the file was not delivered, so the
        # credit comes back, like in the Mini App.
        self.assertEqual(backend._balance(42), 3)
        reasons = event_reasons(backend)
        self.assertEqual(reasons.count(credit_ops.REASON_SPEND), 1)
        self.assertEqual(reasons.count(credit_ops.REASON_RELEASE), 1)

    def test_a_stale_claim_is_taken_over(self):
        store, backend = make_store(balance=3)
        backend._claim((42, JOB["hash"], "cv.pdf", 15))
        backend.advance(minutes=20)
        message, _db, _context = run_telegram_build(store, backend)
        self.assertTrue(message.replies)
        self.assertIn("Готово", message.replies[-1][0])
        self.assertEqual(backend._balance(42), 2)


class EdgeStatementTests(unittest.TestCase):
    """The new statements must pass the validator of the Edge bridge."""

    RELATION_PATTERNS = (
        r"\bFROM\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)",
        r"\bJOIN\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)",
        r"\bINTO\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)",
        r"\bDELETE\s+FROM\s+((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)",
        r"\bUPDATE\s+(?!SET\b)((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*)",
    )
    FORBIDDEN = re.compile(
        r"\b(pg_catalog|information_schema|auth\.|storage\.|vault\.|extensions\.|realtime\.|"
        r"graphql\.|supabase_|alter\b|drop\b|truncate\b|grant\b|revoke\b|copy\b|call\b|"
        r"do\s+\$|prepare\b|execute\b|deallocate\b|listen\b|notify\b|vacuum\b|set_config\b|"
        r"pg_sleep\b|pg_read_file\b|pg_ls_dir\b|dblink\b|lo_import\b|lo_export\b)",
        re.IGNORECASE,
    )
    DISTINCT_FROM = re.compile(r"\bIS\s+(?:NOT\s+)?DISTINCT\s+FROM\b", re.IGNORECASE)

    def _statements(self):
        store, backend = make_store(balance=3)
        operation = credit_ops.cv_operation(store, 42, JOB["hash"])
        operation.reserve()
        operation.release()
        copilot_credits.CreditManager(store).ensure_daily_allowance(42)
        store.claim_document(42, JOB["hash"], "cv.pdf")
        store.release_document_claim(42, JOB["hash"])
        store.log_credit_event(42, 0, credit_ops.REASON_DELIVERED, JOB["hash"])
        store.credit_event_counts(42, (credit_ops.REASON_SPEND,), JOB["hash"])
        return backend.statements

    def test_every_new_statement_survives_the_edge_validator(self):
        statements = self._statements()
        self.assertTrue(statements)
        for query, params in statements:
            normalized = re.sub(r"\s+", " ", query.strip())
            self.assertNotIn(";", normalized, normalized[:80])
            self.assertNotIn("--", normalized, normalized[:80])
            self.assertIsNone(self.FORBIDDEN.search(normalized), normalized[:120])
            self.assertTrue(
                re.match(
                    r"^(select\b|with\b|insert\b|update\b|delete\b|create table if not exists\b|"
                    r"create index if not exists\b)",
                    normalized,
                    re.IGNORECASE,
                ),
                normalized[:80],
            )
            ctes = {
                match.group(1).lower()
                for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(", normalized)
            }
            scan = self.DISTINCT_FROM.sub("IS DISTINCT_FROM", normalized)
            for pattern in self.RELATION_PATTERNS:
                for match in re.finditer(pattern, scan, re.IGNORECASE):
                    relation = match.group(1).lower()
                    parts = relation.split(".")
                    self.assertLessEqual(len(parts), 2, relation)
                    if len(parts) == 2:
                        self.assertEqual(parts[0], "public", relation)
                    name = parts[-1]
                    self.assertTrue(
                        name.startswith("growth_") or name in ctes,
                        "relation outside the growth allowlist: %s" % relation,
                    )
            self.assertEqual(
                query.count("%s"),
                len(params),
                "placeholder and parameter count differ: %s" % normalized[:120],
            )

    def test_the_placeholders_survive_the_bridge_conversion(self):
        from http_growth_patch import _convert_placeholders

        for query, params in self._statements():
            converted, values = _convert_placeholders(query, params)
            self.assertNotIn("%s", converted)
            self.assertEqual(len(values), len(params))
            self.assertEqual(converted.count("$"), len(params))


if __name__ == "__main__":
    unittest.main()
