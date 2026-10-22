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
from datetime import datetime, timedelta, timezone
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
        from copilot import store as store_module
        store_module._CREDIT_OPS_TABLE_MISSING = True
        store_module._CREDIT_OPS_RETRY_AT = 0.0
        self.credits = {}
        self.initial_balance = 0
        self.granted_today = set()
        self.events = []
        self.documents = {}
        self.ai_reports = {}
        self.rate_limits = {}
        self.ops = {}
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

    def _stamp(self, written=None):
        """The wall time of a row of the fake.

        The clock of the fake only moves forward, so the wall time of a row
        that was written earlier moves backwards: the runtime compares the
        stored time with the real clock, and a cache has to expire.
        """
        moment = self.clock if written is None else float(written)
        return datetime.now(timezone.utc) - timedelta(seconds=self.clock - moment)

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
        if sql == "SELECT 1 FROM public.growth_copilot_credit_ops LIMIT 1":
            self._guard("registry")
        elif sql.startswith("SELECT 1 FROM growth_copilot_credit_ops"):
            uid, ref, kind, attempt = values
            row = self.ops.get((int(uid), str(ref), str(kind)))
            self._rows = [(1,)] if row and row["attempt"] == attempt and row["state"] == "reserved" else []
        elif sql.startswith("SELECT 1 FROM growth_copilot_documents"):
            uid, ref, claim = values
            row = self.documents.get((int(uid), str(ref)))
            self._rows = [(1,)] if row and row["filename"] == claim else []
        elif sql.startswith("SELECT 1 FROM growth_copilot_ai_reports"):
            uid, ref, claim = values
            row = self.ai_reports.get((int(uid), str(ref)))
            self._rows = [(1,)] if row and row["claim"] == claim else []
        elif "op_reserved AS" in sql:
            self._guard("debit")
            self._rows = self._reserve_operation(values)
        elif "op_released AS" in sql:
            self._guard("refund")
            self._rows = self._release_operation(values)
        elif "op_delivered AS" in sql:
            self._guard("journal")
            self._rows = self._settle_operation(values, "delivered")
        elif "op_unknown AS" in sql:
            self._guard("journal")
            self._rows = self._settle_operation(values, "unknown")
        elif "SELECT state FROM growth_copilot_credit_ops" in sql:
            self._rows = self._operation_state(values)
        elif "WITH debit AS" in sql:
            self._guard("debit")
            self._rows = self._reserve(values)
        elif "WITH credit AS" in sql:
            self._guard("grant_with_event")
            self._rows = self._grant_with_event(values)
        elif "WITH slot AS" in sql:
            self._guard("budget")
            self._rows = self._reserve_llm_attempt(values)
        elif "WITH refund AS" in sql:
            self._guard("refund")
            self._rows = self._refund(values)
        elif "WITH granted AS" in sql:
            self._guard("grant")
            self._rows = self._grant(values)
        elif "SELECT reason, COUNT(*)" in sql:
            self._rows = self._counts(values)
        elif "UPDATE growth_copilot_documents" in sql:
            self._rows = self._save_document(values)
        elif "WITH empty_slot AS" in sql:
            self._rows = self._release_ai_claim(values)
        elif "UPDATE growth_copilot_ai_reports" in sql:
            self._rows = self._save_ai_report(values)
        elif "INSERT INTO growth_copilot_documents" in sql and "EXCLUDED.telegram_file_id" in sql:
            self._rows = self._save_document(values)
        elif "INSERT INTO growth_copilot_documents" in sql and "DO UPDATE" in sql:
            self._guard("claim")
            self._rows = self._claim(values)
        elif "DELETE FROM growth_copilot_documents" in sql:
            self._rows = self._release_claim(values)
        elif "INSERT INTO growth_copilot_documents" in sql:
            self._rows = self._save_document(values)
        elif "DELETE FROM growth_copilot_ai_reports" in sql:
            self._rows = self._release_ai_claim(values)
        elif "FROM growth_copilot_ai_reports" in sql:
            self._rows = self._get_ai_report(values)
        elif "INSERT INTO growth_copilot_ai_reports" in sql and "DO UPDATE" in sql:
            self._guard("ai_claim")
            self._rows = self._claim_ai_report(values)
        elif "INSERT INTO growth_copilot_ai_reports" in sql:
            self._rows = self._save_ai_report(values)
        elif "INSERT INTO growth_copilot_evaluations" in sql:
            self._rows = [(1,)]
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

    # -- the registry of the operations ---------------------------
    def _operation_key(self, user_id, ref, kind):
        return (int(user_id), str(ref), str(kind))

    def _operation_state(self, values):
        key = self._operation_key(values[0], values[1], values[2])
        row = self.ops.get(key)
        if not row:
            return []
        return [(row["state"],)]

    def _reserve_operation(self, values):
        """The reservation of the registry.

        The row of the operation is the gate: an operation that is already
        paid for keeps its state and the statement changes nothing, and an
        operation that was given back is charged again as a new attempt.
        """
        user_id = int(values[0])
        ref = str(values[2])
        kind = str(values[3])
        amount = int(values[20])
        key = self._operation_key(user_id, ref, kind)
        row = self.ops.get(key)
        if row is not None and row["state"] == "reserved":
            claim = values[8]
            if (claim and self.clock - row["clock"] > int(values[7]) * 60
                    and self._owns_build(user_id, ref, kind, claim)):
                row["clock"] = self.clock
                return [(row["attempt"],)]
            return []
        if row is not None and row["state"] != "released":
            return []
        if self._balance(user_id) < amount:
            return []
        attempt = (row["attempt"] + 1) if row else 1
        self.ops[key] = {"state": "reserved", "amount": amount, "attempt": attempt, "clock": self.clock}
        self.credits[user_id] = self._balance(user_id) - amount
        self.events.append((user_id, -amount, kind, ref))
        return [(attempt,)]

    def _release_operation(self, values):
        """The release of the registry.

        Only the owner of an open reservation can change its state, so two
        parallel releases cannot credit the user twice.
        """
        user_id = int(values[0])
        ref = str(values[2])
        kind = str(values[3])
        attempt, claim = values[4:6]
        amount = int(values[14])
        reason = str(values[18])
        key = self._operation_key(user_id, ref, kind)
        row = self.ops.get(key)
        if not row or row["state"] != "reserved" or row["attempt"] != attempt:
            return []
        if claim and not self._owns_build(user_id, ref, kind, claim):
            return []
        row["state"] = "released"
        self.credits[user_id] = self._balance(user_id) + amount
        self.events.append((user_id, amount, reason, ref))
        return [(len(self.events),)]

    def _owns_build(self, uid, ref, kind, claim):
        rows, column = (self.documents, "filename") if kind == "spend" else (self.ai_reports, "claim")
        row = rows.get((int(uid), str(ref)))
        return bool(row and row[column] == claim)

    def _settle_operation(self, values, state):
        """The end of an operation: delivered or of an unknown outcome."""
        user_id = int(values[0])
        ref = str(values[1])
        kind = str(values[2])
        attempt, claim = values[3:5]
        reason = str(values[14])
        key = self._operation_key(user_id, ref, kind)
        row = self.ops.get(key)
        if not row or row["attempt"] != attempt:
            return []
        if claim and not self._owns_build(user_id, ref, kind, claim):
            return []
        if state == "delivered":
            if row["state"] not in ("reserved", "unknown"):
                return []
        elif row["state"] != "reserved":
            return []
        row["state"] = state
        self.events.append((user_id, 0, reason, ref))
        return [(len(self.events),)]

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

    def _reserve_llm_attempt(self, values):
        user_id = int(values[0])
        reason = str(values[1])
        ref = str(values[2])
        budget = int(values[4])
        # The budget is global: the attempts of every user are counted.
        used = sum(1 for _uid, _delta, item_reason, _ref in self.events if item_reason == reason)
        if used >= budget:
            return []
        self.events.append((user_id, 0, reason, ref))
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
        token = str(values[2])
        stale_minutes = int(values[4])
        key = (user_id, job_id)
        row = self.documents.get(key)
        if row is None:
            self.documents[key] = {
                "telegram_file_id": "",
                "filename": token,
                "fit_score": 0,
                "created_at": self.clock,
            }
            return [(job_id,)]
        if row["telegram_file_id"]:
            return []
        if self.clock - row["created_at"] >= stale_minutes * 60.0:
            row["filename"] = token
            row["created_at"] = self.clock
            return [(job_id,)]
        return []

    def _release_claim(self, values):
        key = (int(values[0]), str(values[1]))
        token = str(values[2])
        row = self.documents.get(key)
        if row and not row["telegram_file_id"] and row["filename"] == token:
            del self.documents[key]
            return [(key[1],)]
        return []

    def _save_document(self, values):
        """The write of the file: an update of the claimed row.

        The row is never created, so a build whose claim was taken over and
        released cannot deliver a file into a free slot.
        """
        file_id = str(values[0] or "")
        filename = str(values[1] or "")
        fit_score = int(values[2] or 0)
        key = (int(values[3]), str(values[4]))
        claim = str(values[5] or "")
        row = self.documents.get(key)
        # Only the owner of the claim may write the file.
        if row is None or row["telegram_file_id"] or row["filename"] != claim:
            return []
        self.documents[key] = {
            "telegram_file_id": file_id,
            "filename": filename,
            "fit_score": fit_score,
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

    def _grant_with_event(self, values):
        amount = int(values[0])
        user_id = int(values[1])
        delta = int(values[3])
        reason = str(values[4])
        ref = str(values[5])
        self.credits[user_id] = self._balance(user_id) + amount
        self.events.append((user_id, delta, reason, ref))
        return [(len(self.events),)]

    def _reserve_llm_attempt(self, values):
        scope = int(values[0])
        budget = int(values[1])
        user_id = int(values[2])
        reason = str(values[3])
        ref = str(values[4])
        used = self.rate_limits.get(scope, 0)
        if used >= budget:
            return []
        self.rate_limits[scope] = used + 1
        self.events.append((user_id, 0, reason, ref))
        return [(len(self.events),)]

    def _claim_ai_report(self, values):
        user_id = int(values[0])
        job_id = str(values[1])
        token = str(values[2])
        stale_minutes = int(values[4])
        key = (user_id, job_id)
        row = self.ai_reports.get(key)
        stale = row is not None and self.clock - row["clock"] >= stale_minutes * 60.0
        if row is None or not row["claim"] or stale:
            # A free slot, a stored report or a dead claim may be taken over.
            self.ai_reports[key] = {
                "model": "",
                "claim": token,
                "answer": "",
                "used_resume": False,
                "clock": self.clock,
            }
            return [(job_id,)]
        return []

    def _release_ai_claim(self, values):
        key = (int(values[0]), str(values[1]))
        token = str(values[2])
        row = self.ai_reports.get(key)
        if row and row["claim"] == token:
            if row["answer"]:
                row["claim"] = ""
            else:
                del self.ai_reports[key]
            return [(key[1],)]
        return []

    def _save_ai_report(self, values):
        """The write of the answer: an update of the claimed row.

        The row is never created, so an answer of an attempt whose claim was
        taken over cannot create a report that nobody owns.
        """
        model = str(values[0] or "")
        answer = str(values[1] or "")
        used_resume = bool(values[2])
        key = (int(values[3]), str(values[4]))
        claim = str(values[5] or "")
        row = self.ai_reports.get(key)
        if row is None or row["claim"] != claim:
            # Only the owner of the claim may write the answer.
            return []
        self.ai_reports[key] = {
            "model": model,
            "claim": claim,
            "answer": answer,
            "used_resume": used_resume,
            "clock": self.clock,
        }
        return [(key[1],)]

    def _get_ai_report(self, values):
        key = (int(values[0]), str(values[1]))
        row = self.ai_reports.get(key)
        if not row:
            return []
        return [
            (
                row["model"],
                row["answer"],
                row["used_resume"],
                self._stamp(row["clock"]),
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
    environment = {"TELEGRAM_BOT_TOKEN": BOT_TOKEN, "COPILOT_ENABLED": "true", "COPILOT_PDF_ENABLED": "true"}
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
    with patch.dict(os.environ, {"COPILOT_ENABLED": "true", "COPILOT_PDF_ENABLED": "true"}):
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


class AttemptBindingTests(unittest.TestCase):
    """Settlement requires the token acquired by this object."""

    def setUp(self):
        patcher = patch.dict(os.environ, {"COPILOT_FREE_PER_DAY": "0"}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_attempt_is_forwarded_to_every_settlement_method(self):
        from unittest.mock import Mock
        for method, store_method in (
            ("release", "release_credit"),
            ("confirm", "confirm_credit_operation"),
            ("mark_unknown", "mark_credit_operation_unknown"),
        ):
            with self.subTest(method=method):
                store = Mock()
                store.credit_operation_state.return_value = None
                store.credit_event_counts.return_value = {}
                store.reserve_credit.return_value = 7
                getattr(store, store_method).return_value = True
                operation = credit_ops.cv_operation(store, 42, JOB["hash"])
                operation.reserve()
                getattr(operation, method)()
                call = getattr(store, store_method).call_args
                self.assertEqual(call.kwargs.get("attempt"), 7)
                self.assertIsNone(call.kwargs.get("claim"))
                self.assertEqual(operation.attempt, 7)

    def test_unreserved_object_cannot_release_someone_elses_credit(self):
        store, backend = make_store(balance=3)
        a = credit_ops.cv_operation(store, 42, JOB["hash"])
        b = credit_ops.cv_operation(store, 42, JOB["hash"])
        a.reserve()
        with self.assertRaises(credit_ops.CreditStoreUnavailable):
            b.release()
        self.assertEqual(backend._balance(42), 2)
        self.assertEqual(a.mark_unknown(), "unknown")
        self.assertNotIn(credit_ops.REASON_RELEASE, event_reasons(backend))

    def test_missing_registry_refuses_reservation_without_journal_fallback(self):
        from copilot import store as store_module
        store, backend = make_store(balance=3)
        with patch.object(store_module, "_CREDIT_OPS_TABLE_MISSING", True), patch.object(
            store_module, "_CREDIT_OPS_RETRY_AT", float("inf")
        ):
            with self.assertRaises(credit_ops.CreditStoreUnavailable):
                credit_ops.cv_operation(store, 42, JOB["hash"]).reserve()
        self.assertEqual(backend._balance(42), 3)
        self.assertEqual(backend.events, [])

    def test_non_owner_cannot_refund_before_or_after_timeout(self):
        for timeout_first in (False, True):
            with self.subTest(timeout_first=timeout_first):
                store, backend = make_store(balance=3)
                a = credit_ops.cv_operation(store, 42, JOB["hash"])
                b = credit_ops.cv_operation(store, 42, JOB["hash"])
                a.reserve()
                with self.assertRaises(credit_ops.CreditStoreUnavailable):
                    b.reserve()
                if timeout_first:
                    a.mark_unknown()
                for settle in (b.release, b.confirm, b.mark_unknown):
                    with self.assertRaises(credit_ops.CreditStoreUnavailable):
                        settle()
                if not timeout_first:
                    a.mark_unknown()
                self.assertEqual(backend._balance(42), 2)
                self.assertEqual(a.state(), "unknown")
                self.assertNotIn(credit_ops.REASON_RELEASE, event_reasons(backend))

    def test_stale_attempt_cannot_change_registry_or_balance(self):
        store, backend = make_store(balance=3)
        a = credit_ops.cv_operation(store, 42, JOB["hash"])
        a.reserve()
        a.release()
        b = credit_ops.cv_operation(store, 42, JOB["hash"])
        b.reserve()
        self.assertEqual(b.attempt, 2)
        before = list(backend.events)
        self.assertFalse(store.release_credit(
            42, 1, "spend_release", JOB["hash"], reserved_reason="spend", attempt=a.attempt
        ))
        self.assertFalse(store.confirm_credit_operation(
            42, JOB["hash"], "spend", "cv_delivered", attempt=a.attempt
        ))
        self.assertFalse(store.mark_credit_operation_unknown(
            42, JOB["hash"], "spend", "cv_delivery_unknown", attempt=a.attempt
        ))
        self.assertEqual(backend.events, before)
        self.assertEqual(backend._balance(42), 2)
        self.assertEqual(store.credit_operation_state(42, JOB["hash"], "spend"), "reserved")

    def test_pdf_takeover_after_reservation_sends_nothing(self):
        for channel in ("bot", "mini_app"):
            with self.subTest(channel=channel):
                store, backend = make_store(balance=3)
                original = credit_ops.CostedOperation.reserve

                def reserve_then_takeover(operation):
                    state = original(operation)
                    backend.advance(minutes=20)
                    store.claim_document(42, JOB["hash"], "cv.pdf")
                    return state

                with patch.object(credit_ops.CostedOperation, "reserve", reserve_then_takeover):
                    if channel == "bot":
                        _message, _db, context = run_telegram_build(store, backend)
                        self.assertEqual(context.bot.sent, [])
                        self.assertIn("Сборка уже выполняется или была прервана. Попробуй позже.",
                                      [text for text, _ in _message.replies])
                    else:
                        with patch.object(copilot_webapp, "send_document_to_chat") as send:
                            status, _payload = call_api(store, FakeDb(backend, JOB), pdf_request())
                        self.assertEqual(status, 503)
                        send.assert_not_called()
                self.assertEqual(backend._balance(42), 2)
                self.assertEqual(event_reasons(backend), [credit_ops.REASON_SPEND])

    def test_missing_registry_is_rechecked_and_restored(self):
        from unittest.mock import Mock
        from copilot import store as store_module
        store, _backend = make_store(balance=3)
        cursor = Mock()
        cursor.fetchone.return_value = ("growth_copilot_credit_ops",)
        context = Mock()
        context.__enter__ = Mock(return_value=cursor)
        context.__exit__ = Mock(return_value=False)
        with patch.object(store_module, "_CREDIT_OPS_TABLE_MISSING", True), patch.object(
            store_module, "_CREDIT_OPS_RETRY_AT", 100.0
        ), patch.object(store_module.time, "monotonic", return_value=99.0), patch.object(
            store, "_cursor", return_value=context
        ) as connect:
            self.assertFalse(store._registry_available())
            connect.assert_not_called()
            with patch.object(store_module.time, "monotonic", return_value=101.0):
                with self.assertLogs(store_module.logger, level="WARNING") as logs:
                    self.assertTrue(store._registry_available())
                self.assertIn("confirmed", " ".join(logs.output))
                self.assertFalse(store_module._CREDIT_OPS_TABLE_MISSING)
                self.assertTrue(store._registry_available())
                self.assertEqual(connect.call_count, 1)


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
        with self.assertRaises(credit_ops.CreditStoreUnavailable):
            second.reserve()
        self.assertEqual(second.state(), credit_ops.STATE_RESERVED)
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

    def test_confirm_refusal_keeps_delivered_document_and_success_response(self):
        with patch.object(credit_ops.CostedOperation, "confirm",
                          side_effect=credit_ops.CreditStoreUnavailable("refused")), \
             patch.object(copilot_webapp, "send_document_to_chat", return_value={"file_id": "FILE-1"}):
            status, payload = self._call()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(sent_document(self.store, self.backend)["telegram_file_id"], "FILE-1")
        self.assertTrue(any(name == "copilot_webapp_cv_sent" for name, _ in self.db.events))
        self.assertEqual(self.backend._balance(42), 2)

    def test_release_refusal_preserves_delivery_error_and_releases_claim(self):
        failure = copilot_webapp.WebappError(502, "telegram_error", "Отказ доставки.",
                                           delivery=copilot_webapp.DELIVERY_REJECTED)
        with patch.object(credit_ops.CostedOperation, "release",
                          side_effect=credit_ops.CreditStoreUnavailable("refused")), \
             patch.object(copilot_webapp, "send_document_to_chat", side_effect=failure):
            status, payload = self._call()
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"], "telegram_error")
        self.assertEqual(self.backend.documents, {})
        self.assertEqual(self.backend._balance(42), 2)

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
        self.backend._claim((42, JOB["hash"], "claim:owner", "claim:owner", 15))
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
        # The cache is written by the owner of a claim, so the row of the
        # vacancy is claimed before the file is stored.
        claim = self.store.claim_document(42, JOB["hash"], "cv.pdf")
        self.assertTrue(claim)
        self.assertTrue(
            self.store.save_document(
                42,
                JOB["hash"],
                telegram_file_id="FILE-9",
                filename="cv.pdf",
                fit_score=70,
                claim=claim,
            )
        )
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

    def test_confirm_refusal_keeps_document_cache_and_completion(self):
        store, backend = make_store(balance=3)
        with patch.object(credit_ops.CostedOperation, "confirm",
                          side_effect=credit_ops.CreditStoreUnavailable("refused")):
            message, db, context = run_telegram_build(store, backend)
        self.assertEqual(len(context.bot.sent), 1)
        self.assertEqual(sent_document(store, backend)["telegram_file_id"], "FILE-1")
        self.assertIn("Готово", message.replies[-1][0])
        self.assertTrue(any(name == "copilot_cv_built" for name, _ in db.events))
        self.assertEqual(backend._balance(42), 2)

    def test_settlement_refusal_after_failed_delivery_releases_own_claim(self):
        from telegram import error as telegram_error
        for method, failure in (("release", telegram_error.BadRequest("rejected")),
                                ("mark_unknown", telegram_error.TimedOut("timeout"))):
            with self.subTest(method=method):
                store, backend = make_store(balance=3)
                with patch.object(credit_ops.CostedOperation, method,
                                  side_effect=credit_ops.CreditStoreUnavailable("refused")):
                    message, db, context = run_telegram_build(store, backend, failure=failure)
                self.assertIn("Не удалось отправить файл. Попробуй позже.",
                              [text for text, _ in message.replies])
                self.assertEqual(backend.documents, {})
                self.assertEqual(backend._balance(42), 2)

    def test_ownership_refusal_releases_own_claim_and_answers(self):
        store, backend = make_store(balance=3)
        with patch.object(credit_ops.CostedOperation, "can_deliver", return_value=False):
            message, db, context = run_telegram_build(store, backend)
        self.assertEqual(context.bot.sent, [])
        self.assertIn("Сборка уже выполняется или была прервана. Попробуй позже.",
                      [text for text, _ in message.replies])
        self.assertEqual(backend.documents, {})
        self.assertEqual(event_reasons(backend), [credit_ops.REASON_SPEND])

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
        backend._claim((42, JOB["hash"], "claim:owner", "claim:owner", 15))
        backend.advance(minutes=20)
        message, _db, _context = run_telegram_build(store, backend)
        self.assertTrue(message.replies)
        self.assertIn("Готово", message.replies[-1][0])
        self.assertEqual(backend._balance(42), 2)


class DeliveryOutcomeMatrixTests(unittest.TestCase):
    """Every failure of the delivery leaves a proven state behind.

    The matrix covers the answers of the Telegram API that the bot sees and
    the HTTP answers of the same API behind the Mini App. For every case the
    state of the operation, the balance, the journal and the possibility of a
    repeat are checked, because a wrong classification either steals a credit
    or gives one away.
    """

    # The answers of the bot API and their proven outcome.
    TELEGRAM_CASES = (
        ("bad_request", "BadRequest", credit_ops.DELIVERY_REJECTED),
        ("forbidden", "Forbidden", credit_ops.DELIVERY_REJECTED),
        ("retry_after", "RetryAfter", credit_ops.DELIVERY_REJECTED),
        ("invalid_token", "InvalidToken", credit_ops.DELIVERY_REJECTED),
        ("timed_out", "TimedOut", credit_ops.DELIVERY_UNKNOWN),
        ("network_error", "NetworkError", credit_ops.DELIVERY_UNKNOWN),
        # A server error of the API reaches the bot as a plain error.
        ("server_error", "TelegramError", credit_ops.DELIVERY_UNKNOWN),
    )

    # The HTTP answers of the same API behind the Mini App.
    HTTP_CASES = (
        (400, copilot_webapp.DELIVERY_REJECTED),
        (403, copilot_webapp.DELIVERY_REJECTED),
        (429, copilot_webapp.DELIVERY_REJECTED),
        (500, copilot_webapp.DELIVERY_UNKNOWN),
        (503, copilot_webapp.DELIVERY_UNKNOWN),
    )

    def setUp(self):
        # The free allowance would hide the balance of the operation.
        patcher = patch.dict(os.environ, {"COPILOT_FREE_PER_DAY": "0"}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _telegram_error(self, name):
        from telegram import error as telegram_error

        if name == "RetryAfter":
            return telegram_error.RetryAfter(5)
        return getattr(telegram_error, name)("boom")

    def _http_error(self, code):
        import urllib.error

        return urllib.error.HTTPError(
            "https://api.telegram.org/bot123/sendDocument",
            code,
            "boom",
            None,
            io.BytesIO(b""),
        )

    def test_the_telegram_matrix_keeps_the_credit_only_when_it_must(self):
        for case, error_name, expected in self.TELEGRAM_CASES:
            with self.subTest(case=case):
                store, backend = make_store(balance=3)
                message, _db, _context = run_telegram_build(
                    store, backend, failure=self._telegram_error(error_name)
                )
                self.assertTrue(message.replies)
                state = credit_ops.cv_operation(store, 42, JOB["hash"]).state()
                reasons = event_reasons(backend)
                self.assertEqual(reasons.count(credit_ops.REASON_SPEND), 1)
                if expected == credit_ops.DELIVERY_REJECTED:
                    self.assertEqual(state, credit_ops.STATE_RELEASED)
                    self.assertEqual(backend._balance(42), 3)
                    self.assertEqual(reasons.count(credit_ops.REASON_RELEASE), 1)
                    self.assertEqual(reasons.count(credit_ops.REASON_UNKNOWN), 0)
                else:
                    self.assertEqual(state, credit_ops.STATE_UNKNOWN)
                    self.assertEqual(backend._balance(42), 2)
                    self.assertEqual(reasons.count(credit_ops.REASON_UNKNOWN), 1)
                    self.assertEqual(reasons.count(credit_ops.REASON_RELEASE), 0)
                # The slot of the vacancy is free again in every case.
                self.assertEqual(backend.documents, {})

    def test_the_mini_app_matrix_classifies_every_http_answer(self):
        for code, expected in self.HTTP_CASES:
            with self.subTest(code=code):
                with patch("urllib.request.urlopen", side_effect=self._http_error(code)):
                    with self.assertRaises(copilot_webapp.WebappError) as caught:
                        copilot_webapp.send_document_to_chat(
                            "123456:TEST-TOKEN", 42, "cv.pdf", b"%PDF-1.4 payload"
                        )
                self.assertEqual(caught.exception.delivery, expected)

    def test_the_mini_app_matrix_keeps_the_credit_only_when_it_must(self):
        for code, expected in self.HTTP_CASES:
            with self.subTest(code=code):
                store, backend = make_store(balance=3)
                with patch("urllib.request.urlopen", side_effect=self._http_error(code)):
                    status, payload = call_api(store, FakeDb(backend, JOB), pdf_request())
                self.assertEqual(status, 502)
                self.assertEqual(payload["error"], "telegram_error")
                state = credit_ops.cv_operation(store, 42, JOB["hash"]).state()
                reasons = event_reasons(backend)
                self.assertEqual(reasons.count(credit_ops.REASON_SPEND), 1)
                if expected == copilot_webapp.DELIVERY_REJECTED:
                    self.assertEqual(state, credit_ops.STATE_RELEASED)
                    self.assertEqual(backend._balance(42), 3)
                    self.assertEqual(reasons.count(credit_ops.REASON_RELEASE), 1)
                else:
                    self.assertEqual(state, credit_ops.STATE_UNKNOWN)
                    self.assertEqual(backend._balance(42), 2)
                    self.assertEqual(reasons.count(credit_ops.REASON_UNKNOWN), 1)
                self.assertEqual(backend.documents, {})

    def test_a_repeat_after_a_refusal_is_charged_again(self):
        from telegram import error as telegram_error

        store, backend = make_store(balance=3)
        run_telegram_build(
            store, backend, failure=telegram_error.BadRequest("chat not found")
        )
        self.assertEqual(backend._balance(42), 3)
        # The refusal gave the credit back, so the next attempt is a new
        # attempt and is charged again.
        message, _db, _context = run_telegram_build(store, backend)
        self.assertIn("Готово", message.replies[-1][0])
        self.assertEqual(backend._balance(42), 2)
        reasons = event_reasons(backend)
        self.assertEqual(reasons.count(credit_ops.REASON_SPEND), 2)
        self.assertEqual(reasons.count(credit_ops.REASON_RELEASE), 1)

    def test_a_repeat_after_an_open_outcome_is_free(self):
        from telegram import error as telegram_error

        store, backend = make_store(balance=3)
        run_telegram_build(store, backend, failure=telegram_error.TimedOut("timeout"))
        self.assertEqual(backend._balance(42), 2)
        # The outcome is open, so the credit stays spent: the vacancy was paid
        # for once and the document may have reached the user.
        message, _db, _context = run_telegram_build(store, backend)
        self.assertIn("Готово", message.replies[-1][0])
        self.assertEqual(backend._balance(42), 2)
        reasons = event_reasons(backend)
        self.assertEqual(reasons.count(credit_ops.REASON_SPEND), 1)
        self.assertEqual(reasons.count(credit_ops.REASON_RELEASE), 0)


class FailClosedTests(unittest.TestCase):
    """A paid document never leaves the service without a confirmed credit.

    The tests of this class have to fail when the code goes back to a
    fail-open behaviour: every one of them counts the calls of the delivery
    and looks for the marker of a successful delivery.
    """

    def setUp(self):
        patcher = patch.dict(os.environ, {"COPILOT_FREE_PER_DAY": "0"}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_broken_credit_store_sends_no_document(self):
        store, backend = make_store(balance=3, failures=("debit",))
        with patch.object(
            copilot_webapp, "send_document_to_chat", return_value={"file_id": "FILE-1"}
        ) as send:
            status, payload = call_api(store, FakeDb(backend, JOB), pdf_request())
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "store_unavailable")
        # No call of the delivery, no marker of a delivery and no credit.
        self.assertEqual(send.call_count, 0)
        self.assertEqual(backend._balance(42), 3)
        self.assertEqual(event_reasons(backend), [])
        self.assertNotIn(credit_ops.REASON_DELIVERED, event_reasons(backend))
        self.assertNotIn(credit_ops.REASON_SPEND, event_reasons(backend))
        # The slot of the vacancy is free again.
        self.assertEqual(backend.documents, {})

    def test_a_broken_credit_store_sends_no_document_in_the_bot(self):
        store, backend = make_store(balance=3, failures=("debit",))
        message, _db, context = run_telegram_build(store, backend)
        self.assertTrue(message.replies)
        # No call of the delivery and no marker of a delivery.
        self.assertEqual(context.bot.sent, [])
        self.assertEqual(backend._balance(42), 3)
        self.assertEqual(event_reasons(backend), [])
        self.assertNotIn(credit_ops.REASON_DELIVERED, event_reasons(backend))
        self.assertEqual(backend.documents, {})

    def test_a_failed_reservation_answers_that_the_check_failed(self):
        """The answer must not tell the user that the balance is empty.

        An empty balance and an unreachable store are different problems, and
        the user must not be told that the free allowance is over when the
        service simply could not look it up.
        """
        store, backend = make_store(balance=3, failures=("debit",))
        message, _db, _context = run_telegram_build(store, backend)
        self.assertTrue(message.replies)
        text = message.replies[-1][0]
        self.assertIn("Не удалось проверить кредиты", text)
        self.assertNotIn("закончились", text)

    def test_a_broken_credit_store_sends_no_ai_analysis(self):
        """The paid model call needs a confirmed credit as well."""
        import asyncio

        from test_copilot_runtime import FakeDb as RuntimeDb
        from test_copilot_runtime import FakeMessage, FakeQuery, FakeUpdate

        from test_copilot_credit_operations import FakeGrowthStore

        backend = FakeGrowthStore()
        backend.failures = {"debit"}
        db = RuntimeDb(JOB)
        db._growth_store = backend
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_ai:" + JOB["hash"]
        update = FakeUpdate(message=message, query=query)
        calls = []

        def fake_analyze(job, skills, deterministic, resume_text, **kwargs):
            calls.append(job)
            raise AssertionError("the model must not be called without a credit")

        env = {
            "COPILOT_ENABLED": "true",
            "COPILOT_LLM_ENABLED": "true",
            "COPILOT_LLM_API_KEY": "test-key",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            copilot_runtime.copilot_llm, "analyze", new=fake_analyze
        ):
            asyncio.run(
                copilot_runtime._copilot_handle_callback(
                    RuntimeFakeBot(db), update, SimpleNamespace()
                )
            )
        self.assertEqual(calls, [])
        self.assertNotIn(
            "copilot_ai_viewed", [name for name, _props in db.events]
        )


class RuntimeFakeBot:
    """The bot of the runtime tests."""

    def __init__(self, db):
        self.db = db


class ClaimReleasedOnFailureTests(unittest.TestCase):
    """A build that fails gives the claim of the vacancy back.

    The claim is the lock of the vacancy: a build that fails without giving
    it back blocks the next request for the whole stale window. Every step
    that can fail after the claim has to release it, and a refused write of
    the result must not be read as a success.
    """

    def setUp(self):
        patcher = patch.dict(os.environ, {"COPILOT_FREE_PER_DAY": "0"}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store, self.backend = make_store(balance=3)
        self.db = FakeDb(self.backend, JOB)

    def _call(self, payload=None):
        return call_api(self.store, self.db, payload or pdf_request())

    def test_a_failing_settings_read_releases_the_claim(self):
        def broken_settings(user_id):
            raise RuntimeError("the database is down")

        self.db.get_user_settings = broken_settings
        status, _payload = self._call()
        self.assertEqual(status, 503)
        self.assertEqual(self.backend.documents, {})

    def test_a_failing_analysis_releases_the_claim(self):
        with patch.object(
            api_copilot.copilot_fit, "compute_fit", side_effect=RuntimeError("no analysis")
        ):
            status, _payload = self._call()
        self.assertEqual(status, 503)
        self.assertEqual(self.backend.documents, {})

    def test_a_failing_validation_releases_the_claim(self):
        with patch.object(
            api_copilot.copilot_pdf,
            "validate_pdf_bytes",
            side_effect=RuntimeError("no text layer"),
        ):
            status, _payload = self._call()
        self.assertEqual(status, 503)
        self.assertEqual(self.backend.documents, {})

    def test_a_failing_caption_releases_the_claim(self):
        class BrokenReport:
            ok = True
            pages = 1
            has_text_layer = True
            keyword_coverage_pct = 0
            problems = ()

            def summary(self):
                raise RuntimeError("no summary")

        with patch.object(
            api_copilot.copilot_pdf, "validate_pdf_bytes", return_value=BrokenReport()
        ):
            status, _payload = self._call()
        self.assertEqual(status, 503)
        self.assertEqual(self.backend.documents, {})

    def test_a_refused_cache_write_is_not_a_cached_document(self):
        """A write that was refused is not a success.

        The document was delivered, so the request is answered, but the cache
        does not know the file and the claim of the vacancy is free again.
        """
        with patch.object(
            copilot_webapp, "send_document_to_chat", return_value={"file_id": "FILE-1"}
        ), patch.object(CopilotStore, "save_document", return_value=False):
            status, payload = self._call()
        self.assertEqual(status, 200)
        self.assertFalse(payload["cached"])
        self.assertEqual(self.backend.documents, {})

    def test_a_failing_model_releases_the_analysis_claim(self):
        import asyncio

        from test_copilot_runtime import FakeDb as RuntimeDb
        from test_copilot_runtime import FakeMessage, FakeQuery, FakeUpdate

        backend = FakeGrowthStore()
        backend.seed_credits(4242, 3)
        db = RuntimeDb(JOB)
        db._growth_store = backend
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_ai:" + JOB["hash"]
        update = FakeUpdate(message=message, query=query)

        def broken_analyze(job, skills, deterministic, resume_text, **kwargs):
            raise RuntimeError("the model is down")

        env = {
            "COPILOT_ENABLED": "true",
            "COPILOT_LLM_ENABLED": "true",
            "COPILOT_LLM_API_KEY": "test-key",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            copilot_runtime.copilot_llm, "analyze", new=broken_analyze
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(
                    copilot_runtime._copilot_handle_callback(
                        RuntimeFakeBot(db), update, SimpleNamespace()
                    )
                )
        # The slot of the analysis is free again.
        self.assertEqual(backend.ai_reports, {})

    def test_a_refused_report_write_releases_the_analysis_claim(self):
        """The answer that was not written must not keep the slot locked."""
        import asyncio

        from copilot import llm as copilot_llm
        from test_copilot_runtime import FakeDb as RuntimeDb
        from test_copilot_runtime import FakeMessage, FakeQuery, FakeUpdate

        backend = FakeGrowthStore()
        backend.seed_credits(4242, 3)
        db = RuntimeDb(JOB)
        db._growth_store = backend
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_ai:" + JOB["hash"]
        update = FakeUpdate(message=message, query=query)

        def fake_analyze(job, skills, deterministic, resume_text, **kwargs):
            return copilot_llm.LLMResult(
                ok=True,
                summary="Нормальная junior-вакансия",
                sections={
                    "strengths": ["Совпадает Python"],
                    "gaps": [],
                    "risks": [],
                    "next_steps": [],
                    "questions": [],
                },
                model="test-model",
            )

        env = {
            "COPILOT_ENABLED": "true",
            "COPILOT_LLM_ENABLED": "true",
            "COPILOT_LLM_API_KEY": "test-key",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            copilot_runtime.copilot_llm, "analyze", new=fake_analyze
        ), patch.object(CopilotStore, "save_ai_report", return_value=False):
            asyncio.run(
                copilot_runtime._copilot_handle_callback(
                    RuntimeFakeBot(db), update, SimpleNamespace()
                )
            )
        # The slot of the analysis is free again.
        self.assertEqual(backend.ai_reports, {})


class DocumentClaimOwnershipTests(unittest.TestCase):
    """Only the owner of a claim may release it or write the document.

    A claim that was taken over belongs to the new owner: a late release of
    the old owner must not delete the slot, and a late write of the old owner
    must not overwrite the file of the new one.
    """

    def test_a_late_release_does_not_delete_the_claim_of_the_new_owner(self):
        store, backend = make_store(balance=3)
        first = store.claim_document(42, JOB["hash"], "cv.pdf")
        self.assertTrue(first)
        # The claim goes stale and another build takes it over.
        backend.advance(minutes=20)
        second = store.claim_document(42, JOB["hash"], "cv.pdf")
        self.assertTrue(second)
        self.assertNotEqual(first, second)
        # The late release of the first owner must not free the slot.
        self.assertFalse(store.release_document_claim(42, JOB["hash"], first))
        row = sent_document(store, backend)
        self.assertIsNotNone(row)
        self.assertEqual(row["filename"], second)
        self.assertEqual(row["telegram_file_id"], "")

    def test_a_late_write_does_not_overwrite_the_document_of_the_new_owner(self):
        store, backend = make_store(balance=3)
        first = store.claim_document(42, JOB["hash"], "cv.pdf")
        backend.advance(minutes=20)
        second = store.claim_document(42, JOB["hash"], "cv.pdf")
        # The old owner writes its file too late.
        store.save_document(
            42,
            JOB["hash"],
            telegram_file_id="FILE-OLD",
            filename="cv.pdf",
            fit_score=10,
            claim=first,
        )
        row = sent_document(store, backend)
        self.assertEqual(row["telegram_file_id"], "")
        self.assertEqual(row["filename"], second)
        # The new owner writes its own file.
        store.save_document(
            42,
            JOB["hash"],
            telegram_file_id="FILE-NEW",
            filename="cv.pdf",
            fit_score=20,
            claim=second,
        )
        self.assertEqual(sent_document(store, backend)["telegram_file_id"], "FILE-NEW")

    def test_a_delivered_document_is_not_overwritten_by_a_late_build(self):
        store, backend = make_store(balance=3)
        claim = store.claim_document(42, JOB["hash"], "cv.pdf")
        store.save_document(
            42,
            JOB["hash"],
            telegram_file_id="FILE-NEW",
            filename="cv.pdf",
            fit_score=20,
            claim=claim,
        )
        # A build that was overtaken writes its file too late.
        store.save_document(
            42,
            JOB["hash"],
            telegram_file_id="FILE-OLD",
            filename="cv.pdf",
            fit_score=10,
            claim="claim:somebody-else",
        )
        self.assertEqual(sent_document(store, backend)["telegram_file_id"], "FILE-NEW")

    def test_a_takeover_during_the_build_keeps_the_file_of_the_new_owner(self):
        """The end to end version of the two tests above.

        Another build takes the stale claim while the first build is still
        running. The first build must not delete the slot of the second one
        and must not write its file into the cache of the second one.
        """
        store, backend = make_store(balance=3)
        original = copilot_runtime.copilot_pdf.build_cv_pdf

        def build_with_takeover(*args, **kwargs):
            backend.advance(minutes=20)
            backend._claim((42, JOB["hash"], "claim:other", "claim:other", 15))
            return original(*args, **kwargs)

        with patch.object(
            copilot_runtime.copilot_pdf, "build_cv_pdf", new=build_with_takeover
        ):
            message, _db, _context = run_telegram_build(store, backend)
        self.assertTrue(message.replies)
        row = sent_document(store, backend)
        self.assertIsNotNone(row)
        # The slot of the other build is still there and holds no file.
        self.assertEqual(row["filename"], "claim:other")
        self.assertEqual(row["telegram_file_id"], "")

    def test_a_failing_build_does_not_delete_the_claim_of_the_new_owner(self):
        store, backend = make_store(balance=3)

        def build_with_takeover(*args, **kwargs):
            backend.advance(minutes=20)
            backend._claim((42, JOB["hash"], "claim:other", "claim:other", 15))
            raise RuntimeError("no font")

        with patch.object(
            copilot_runtime.copilot_pdf, "build_cv_pdf", new=build_with_takeover
        ):
            message, _db, _context = run_telegram_build(store, backend)
        self.assertTrue(message.replies)
        row = sent_document(store, backend)
        self.assertIsNotNone(row)
        self.assertEqual(row["filename"], "claim:other")


class ReferralAtomicityTests(unittest.TestCase):
    """The bonus of a referral and its journal entry are one statement."""

    def test_the_referral_writes_the_balance_and_the_journal_together(self):
        store, backend = make_store(balance=0)
        manager = copilot_credits.CreditManager(store)
        with patch.dict(
            os.environ, {"COPILOT_REFERRAL_BONUS": "5"}, clear=False
        ):
            self.assertEqual(manager.grant_referral(42), 5)
        self.assertEqual(backend._balance(42), 5)
        reasons = event_reasons(backend)
        self.assertEqual(reasons.count(copilot_credits.REASON_REFERRAL), 1)
        # One statement writes the balance and the journal entry.
        touching = [
            query
            for query, _params in backend.statements
            if "WITH credit AS" in query
        ]
        self.assertEqual(len(touching), 1, touching)

    def test_a_failed_referral_writes_neither_the_balance_nor_the_journal(self):
        store, backend = make_store(balance=0, failures=("grant_with_event",))
        manager = copilot_credits.CreditManager(store)
        with patch.dict(
            os.environ, {"COPILOT_REFERRAL_BONUS": "5"}, clear=False
        ):
            with self.assertRaises(Exception):
                manager.grant_referral(42)
        self.assertEqual(backend._balance(42), 0)
        self.assertNotIn(copilot_credits.REASON_REFERRAL, event_reasons(backend))

    def test_the_rules_of_the_bonus_are_unchanged(self):
        store, backend = make_store(balance=0)
        manager = copilot_credits.CreditManager(store)
        with patch.dict(
            os.environ, {"COPILOT_REFERRAL_BONUS": "0"}, clear=False
        ):
            self.assertEqual(manager.grant_referral(42), 0)
        self.assertEqual(backend._balance(42), 0)
        self.assertEqual(event_reasons(backend), [])


class EdgeStatementTests(unittest.TestCase):
    """The new statements must reach the Edge bridge unharmed.

    The rules of the validator live in the TypeScript of the Edge functions,
    so they are not copied here: ``test_copilot_edge_validator`` runs the
    real validators. This class only checks the conversion of the
    placeholders, which the bridge does on the way.
    """

    def _statements(self):
        store, backend = make_store(balance=3)
        operation = credit_ops.cv_operation(store, 42, JOB["hash"])
        operation.reserve()
        operation.release()
        copilot_credits.CreditManager(store).ensure_daily_allowance(42)
        copilot_credits.CreditManager(store).grant_referral(42)
        store.claim_document(42, JOB["hash"], "cv.pdf")
        store.release_document_claim(42, JOB["hash"], "claim:test")
        store.save_document(
            42,
            JOB["hash"],
            telegram_file_id="FILE-1",
            filename="cv.pdf",
            fit_score=50,
            claim="claim:test",
        )
        store.claim_ai_report(42, JOB["hash"])
        store.save_ai_report(
            42, JOB["hash"], claim="claim:test", model="m", answer="a"
        )
        store.release_ai_report_claim(42, JOB["hash"], "claim:test")
        store.grant_credits_with_event(42, 5, copilot_credits.REASON_REFERRAL, "")
        store.log_credit_event(42, 0, credit_ops.REASON_DELIVERED, JOB["hash"])
        store.credit_event_counts(42, (credit_ops.REASON_SPEND,), JOB["hash"])
        copilot_credits.CreditManager(store).reserve_llm_call(42, JOB["hash"])
        return backend.statements

    def test_the_placeholders_survive_the_bridge_conversion(self):
        from http_growth_patch import _convert_placeholders

        for query, params in self._statements():
            converted, values = _convert_placeholders(query, params)
            self.assertNotIn("%s", converted)
            self.assertEqual(len(values), len(params))
            self.assertEqual(converted.count("$"), len(params))

    def test_every_statement_has_one_parameter_per_placeholder(self):
        for query, params in self._statements():
            self.assertEqual(
                query.count("%s"),
                len(params),
                "placeholder and parameter count differ: %s" % query[:120],
            )


if __name__ == "__main__":
    unittest.main()
