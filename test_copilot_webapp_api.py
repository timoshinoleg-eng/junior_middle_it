import hashlib
import hmac
import io
import json
import os
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from api import copilot as api_copilot
from copilot import webapp as copilot_webapp
from copilot import webapp_auth


BOT_TOKEN = "123456:TEST-TOKEN"
AUTH_DATE = int(time.time())
OLD_AUTH_DATE = AUTH_DATE - 10 * 24 * 3600

JOB = {
    "hash": "abc123",
    "title": "Junior Python Developer",
    "company": "Acme",
    "description": "Python, FastAPI, PostgreSQL. Опыт 1-2 года.",
    "requirements": "",
    "tags": [],
    "url": "https://example.com/jobs/1",
}


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


class FakeDb:
    def __init__(self, job=None):
        self.job = job
        self.events = []
        self.settings = {"skills": "python, fastapi"}
        self.closed = False

    def get_job_payload(self, job_hash):
        if self.job and str(job_hash) == str(self.job.get("hash")):
            return dict(self.job)
        return None

    def get_user_settings(self, user_id):
        return dict(self.settings)

    def list_saved_searches(self, user_id):
        return []

    def log_event(self, user_id, name, props=None):
        self.events.append((name, dict(props or {})))

    def close(self):
        self.closed = True


class FakeStore:
    def __init__(self, *, allowed=True, document=None, balance=3):
        self.allowed = allowed
        self.document = document
        self.balance_value = balance
        self.spent = []
        self.saved = []
        self.events = []
        self.claimed = set()

    # rate limit
    def bump_rate_limit(self, user_id, *, limit=10, window_minutes=10):
        return self.allowed, 1

    # documents
    def get_document(self, user_id, job_id):
        return self.document

    def save_document(self, user_id, job_id, *, claim=None, **kwargs):
        self.saved.append((user_id, job_id, kwargs))
        return True

    def claim_document(self, user_id, job_id, filename, *, stale_after_minutes=15):
        key = (int(user_id), str(job_id))
        if key in self.claimed:
            return None
        self.claimed.add(key)
        return "claim:%s" % key[1]

    def release_document_claim(self, user_id, job_id, claim=None):
        key = (int(user_id), str(job_id))
        if key in self.claimed:
            self.claimed.discard(key)
            return True
        return False

    def get_forwarded_job(self, job_hash):
        return None

    # credits
    def get_balance(self, user_id):
        return self.balance_value

    def ensure_credit_row(self, user_id):
        return True

    def grant_daily_allowance(self, user_id, amount):
        return None

    def grant_daily_allowance_with_event(self, user_id, amount, reason, ref=""):
        return False

    def spend_credits(self, user_id, amount):
        if self.balance_value < amount:
            return None
        self.balance_value -= amount
        self.spent.append(amount)
        return self.balance_value

    def _count(self, user_id, reason, ref):
        return sum(
            1
            for uid, _delta, item_reason, item_ref in self.events
            if int(uid) == int(user_id)
            and str(item_reason) == str(reason)
            and str(item_ref) == str(ref)
        )

    def reserve_credit(self, user_id, amount, reason, ref, *, release_reason="spend_release"):
        if self._count(user_id, reason, ref) > self._count(user_id, release_reason, ref):
            return False
        if self.balance_value < int(amount):
            return False
        self.balance_value -= int(amount)
        self.spent.append(int(amount))
        self.events.append((int(user_id), -int(amount), reason, ref))
        return True

    def release_credit(self, user_id, amount, reason, ref, *, reserved_reason):
        if self._count(user_id, reserved_reason, ref) <= self._count(user_id, reason, ref):
            return False
        self.balance_value += int(amount)
        self.events.append((int(user_id), int(amount), reason, ref))
        return True

    def credit_event_counts(self, user_id, reasons, ref):
        counts = {}
        for uid, _delta, reason, item_ref in self.events:
            if int(uid) != int(user_id) or str(item_ref) != str(ref):
                continue
            if reason in reasons:
                counts[reason] = counts.get(reason, 0) + 1
        return counts

    def log_credit_event(self, user_id, delta, reason, ref=""):
        self.events.append((int(user_id), int(delta), reason, ref))
        return True

    def count_credit_events(self, reason, today_only=False):
        return 0


class CapturingHandler(api_copilot.handler):
    def _send_json(self, status, payload):
        self.captured_status = status
        self.captured_payload = payload


def call(payload, *, path="/api/copilot", db=None, store=None, env=None):
    """Run one request through the handler and return the captured answer."""
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


class AuthTests(unittest.TestCase):
    def test_forged_signature_gets_nothing(self):
        db = FakeDb(JOB)
        store = FakeStore()
        status, payload = call(
            {"init_data": build_init_data(token="other-token"), "job_hash": "abc123"},
            db=db,
            store=store,
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")
        self.assertEqual(db.events, [])

    def test_empty_init_data_gets_nothing(self):
        status, payload = call({"init_data": "", "job_hash": "abc123"})
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")

    def test_expired_signature_gets_nothing(self):
        db = FakeDb(JOB)
        store = FakeStore()
        status, payload = call(
            {
                "init_data": build_init_data(auth_date=OLD_AUTH_DATE),
                "job_hash": "abc123",
            },
            db=db,
            store=store,
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")

    def test_missing_bot_token_is_a_service_error(self):
        status, payload = call(
            {"init_data": build_init_data(), "job_hash": "abc123"},
            env={"TELEGRAM_BOT_TOKEN": ""},
        )
        self.assertEqual(status, 503)


class FitTests(unittest.TestCase):
    def test_fit_returns_the_deterministic_analysis(self):
        db = FakeDb(JOB)
        store = FakeStore()
        status, payload = call(
            {"init_data": build_init_data(), "action": "fit", "job_hash": "abc123"},
            db=db,
            store=store,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["job"]["title"], "Junior Python Developer")
        self.assertIsInstance(payload["fit"]["score"], int)
        self.assertIn("grade_audit", payload)
        self.assertTrue(any(name == "copilot_webapp_fit_viewed" for name, _ in db.events))

    def test_unknown_vacancy_is_not_found(self):
        db = FakeDb()
        store = FakeStore()
        status, payload = call(
            {"init_data": build_init_data(), "action": "fit", "job_hash": "nope"},
            db=db,
            store=store,
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "job_not_found")

    def test_missing_vacancy_is_a_bad_request(self):
        db = FakeDb(JOB)
        store = FakeStore()
        status, payload = call(
            {"init_data": build_init_data(), "action": "fit"}, db=db, store=store
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "missing_job")

    def test_unknown_action_is_rejected(self):
        db = FakeDb(JOB)
        store = FakeStore()
        status, payload = call(
            {"init_data": build_init_data(), "action": "hack"}, db=db, store=store
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "unsupported_action")

    def test_searches_are_returned_for_the_signed_user(self):
        db = FakeDb(JOB)
        store = FakeStore()
        status, payload = call(
            {"init_data": build_init_data(), "action": "searches"}, db=db, store=store
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["searches"], [])

    def test_rate_limit_is_enforced(self):
        db = FakeDb(JOB)
        store = FakeStore(allowed=False)
        status, payload = call(
            {"init_data": build_init_data(), "action": "fit", "job_hash": "abc123"},
            db=db,
            store=store,
        )
        self.assertEqual(status, 429)
        self.assertEqual(payload["error"], "rate_limited")


class PdfTests(unittest.TestCase):
    def test_cached_document_is_sent_again_without_a_credit(self):
        db = FakeDb(JOB)
        store = FakeStore(document={"telegram_file_id": "FILE-1", "filename": "cv.pdf"})
        with patch.object(
            copilot_webapp, "resend_document", return_value={"file_id": "FILE-1"}
        ) as resend:
            status, payload = call(
                {"init_data": build_init_data(), "job_hash": "abc123"},
                path="/api/copilot/pdf",
                db=db,
                store=store,
            )
        self.assertEqual(status, 200)
        self.assertTrue(payload["cached"])
        self.assertEqual(store.spent, [])
        self.assertTrue(resend.called)

    def test_without_credits_the_api_answers_402(self):
        db = FakeDb(JOB)
        store = FakeStore(balance=0)
        status, payload = call(
            {
                "init_data": build_init_data(),
                "job_hash": "abc123",
                "resume": {"filename": "cv.pdf", "content_base64": "aGk="},
            },
            path="/api/copilot/pdf",
            db=db,
            store=store,
        )
        self.assertEqual(status, 402)
        self.assertEqual(payload["error"], "no_credits")

    def test_missing_resume_is_a_bad_request(self):
        db = FakeDb(JOB)
        store = FakeStore()
        status, payload = call(
            {"init_data": build_init_data(), "job_hash": "abc123"},
            path="/api/copilot/pdf",
            db=db,
            store=store,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "missing_resume")

    def test_broken_base64_is_a_bad_request(self):
        db = FakeDb(JOB)
        store = FakeStore()
        status, payload = call(
            {
                "init_data": build_init_data(),
                "job_hash": "abc123",
                "resume": {"filename": "cv.pdf", "content_base64": "!!!"},
            },
            path="/api/copilot/pdf",
            db=db,
            store=store,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid_resume")

    def test_unknown_vacancy_is_not_found_for_pdf(self):
        db = FakeDb()
        store = FakeStore()
        status, payload = call(
            {"init_data": build_init_data(), "job_hash": "nope"},
            path="/api/copilot/pdf",
            db=db,
            store=store,
        )
        self.assertEqual(status, 404)

    def test_resume_content_never_reaches_the_events(self):
        db = FakeDb(JOB)
        store = FakeStore()
        status, _payload = call(
            {
                "init_data": build_init_data(),
                "action": "fit",
                "job_hash": "abc123",
                "resume": {"filename": "secret.pdf", "content_base64": "aGk="},
            },
            db=db,
            store=store,
        )
        self.assertEqual(status, 200)
        for _name, props in db.events:
            for value in props.values():
                self.assertNotIn("secret.pdf", str(value))
                self.assertNotIn("aGk=", str(value))


class BodyTests(unittest.TestCase):
    def test_empty_body_is_rejected(self):
        handler = object.__new__(CapturingHandler)
        handler.rfile = io.BytesIO(b"")
        handler.headers = {"content-length": "0"}
        handler.captured_status = None
        handler.captured_payload = None
        with self.assertRaises(copilot_webapp.WebappError):
            handler._read_json()

    def test_broken_json_is_rejected(self):
        handler = object.__new__(CapturingHandler)
        handler.rfile = io.BytesIO(b"{not json")
        handler.headers = {"content-length": "8"}
        with self.assertRaises(copilot_webapp.WebappError):
            handler._read_json()

    def test_oversized_body_is_rejected(self):
        handler = object.__new__(CapturingHandler)
        handler.rfile = io.BytesIO(b"{}")
        handler.headers = {"content-length": str(copilot_webapp.MAX_BODY_BYTES + 1)}
        with self.assertRaises(copilot_webapp.WebappError):
            handler._read_json()


class MultipartTests(unittest.TestCase):
    def test_multipart_contains_the_fields_and_the_file(self):
        body, content_type = copilot_webapp.build_multipart(
            {"chat_id": "42", "caption": "привет"}, "cv.pdf", b"%PDF-1.4 payload"
        )
        self.assertIn("multipart/form-data; boundary=", content_type)
        self.assertIn(b'name="chat_id"', body)
        self.assertIn(b"42", body)
        self.assertIn(b'filename="cv.pdf"', body)
        self.assertIn(b"%PDF-1.4 payload", body)

    def test_filename_is_sanitized(self):
        body, _content_type = copilot_webapp.build_multipart(
            {}, 'cv"\r\nX-Injected: 1.pdf', b"data"
        )
        filename_part = body.split(b'filename="', 1)[1].split(b'"', 1)[0]
        self.assertNotIn(b"\r", filename_part)
        self.assertNotIn(b"\n", filename_part)
        self.assertNotIn(b":", filename_part)


if __name__ == "__main__":
    unittest.main()
