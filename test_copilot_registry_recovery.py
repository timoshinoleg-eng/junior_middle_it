"""Fail-closed registry and separate AI ownership across both transports."""
import io
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import patch

import http_growth_patch as bridge
from copilot import store as store_module
from copilot import credit_operations as ops
from copilot.store import CopilotStore, CopilotStoreUnavailable
from growth_store import GrowthStoreUnavailable
from test_copilot_credit_operations import make_store, JOB


class RegistryRecoveryTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("_CREDIT_OPS_TABLE_MISSING", True), ("_CREDIT_OPS_RETRY_AT", 0.0)):
            patcher = patch.object(store_module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_http_400_and_503_close_paid_operations_without_journal_queries(self):
        for status in (400, 503):
            with self.subTest(status=status):
                store_module._CREDIT_OPS_TABLE_MISSING = True
                store_module._CREDIT_OPS_RETRY_AT = 0.0
                conn = bridge.RemoteGrowthConnection(
                    "https://example.supabase.co/functions/v1/growth-proxy", "test-key")
                store = CopilotStore(SimpleNamespace(_ensure_conn=lambda: conn))
                error = HTTPError("https://example.supabase.co", status,
                                  "failure", {}, io.BytesIO(b'{"error":"query rejected"}' if status == 400 else b'{"error":"query failed"}'))
                with patch.object(bridge, "urlopen", side_effect=error) as send, self.assertLogs(store_module.logger, "WARNING") as logs:
                    with self.assertRaises(ops.CreditStoreUnavailable):
                        ops.cv_operation(store, 42, "job").reserve()
                    for action in (
                        lambda: store.reserve_credit(42, 1, "spend", "job"),
                        lambda: store.release_credit(42, 1, "spend_release", "job", reserved_reason="spend", attempt=1),
                        lambda: store.confirm_credit_operation(42, "job", "spend", "cv_delivered", attempt=1),
                        lambda: store.mark_credit_operation_unknown(42, "job", "spend", "cv_delivery_unknown", attempt=1),
                    ):
                        with self.assertRaises(CopilotStoreUnavailable):
                            action()
                self.assertEqual(send.call_count, 1)
                self.assertIn("not confirmed through bridge", " ".join(logs.output))
                self.assertIn("HTTP " + str(status), " ".join(logs.output))
                import json
                query = json.loads(send.call_args.args[0].data)["query"]
                self.assertEqual(query, "SELECT 1 FROM public.growth_copilot_credit_ops LIMIT 1")

    def test_http_error_preserves_status_in_exception(self):
        for status in (400, 503):
            conn = bridge.RemoteGrowthConnection("https://example.supabase.co/functions/v1/growth-proxy", "test-key")
            error = HTTPError("https://example.supabase.co", status, "failure", {}, io.BytesIO(b'{"error":"query rejected"}' if status == 400 else b'{"error":"query failed"}'))
            with patch.object(bridge, "urlopen", side_effect=error), self.assertRaises(GrowthStoreUnavailable) as caught:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM public.growth_copilot_credit_ops LIMIT 1")
            self.assertIn("HTTP " + str(status), str(caught.exception))
            self.assertEqual(caught.exception.http_status, status)

    def test_missing_p13_closes_reservation_then_recovers_after_retry(self):
        store, backend = make_store(balance=3)
        class MissingP13(Exception):
            sqlstate = "42P01"
        def unavailable(query, params=()):
            raise MissingP13()
        with patch.object(backend, "execute", side_effect=unavailable), self.assertLogs(store_module.logger, "WARNING") as logs:
            with self.assertRaises(ops.CreditStoreUnavailable):
                ops.cv_operation(store, 42, JOB["hash"]).reserve()
        self.assertIn("migration P13 is not applied", " ".join(logs.output))
        self.assertEqual(backend.events, [])
        self.assertEqual(backend._balance(42), 3)
        with patch.object(store_module.time, "monotonic", return_value=store_module._CREDIT_OPS_RETRY_AT + 1):
            operation = ops.cv_operation(store, 42, JOB["hash"])
            self.assertEqual(operation.reserve(), "reserved")
        self.assertEqual(backend._balance(42), 2)
        self.assertEqual(len(backend.events), 1)

    def test_saved_ai_report_keeps_claim_until_explicit_release(self):
        store, backend = make_store(balance=3)
        claim = store.claim_ai_report(42, JOB["hash"])
        self.assertTrue(store.save_ai_report(42, JOB["hash"], claim=claim, model="test-model", answer="answer"))
        self.assertTrue(store.owns_claim(42, JOB["hash"], claim, "llm_call"))
        self.assertEqual(store.get_ai_report(42, JOB["hash"])["model"], "test-model")
        self.assertIsNone(store.claim_ai_report(42, JOB["hash"]))
        self.assertTrue(store.release_ai_report_claim(42, JOB["hash"], claim))
        self.assertFalse(store.owns_claim(42, JOB["hash"], claim, "llm_call"))
        self.assertEqual(store.get_ai_report(42, JOB["hash"])["answer"], "answer")
        self.assertTrue(store.claim_ai_report(42, JOB["hash"]))

    def test_resume_answer_is_not_stored_and_slot_reopens_immediately(self):
        store, backend = make_store(balance=3)
        claim = store.claim_ai_report(42, JOB["hash"])
        self.assertTrue(store.save_ai_report(42, JOB["hash"], claim=claim, model="test-model", answer="resume content", used_resume=True))
        self.assertEqual(store.get_ai_report(42, JOB["hash"])["answer"], "")
        self.assertTrue(store.release_ai_report_claim(42, JOB["hash"], claim))
        self.assertTrue(store.claim_ai_report(42, JOB["hash"]))

    def test_ai_claim_only_expires_after_stale_window(self):
        store, backend = make_store(balance=3)
        first = store.claim_ai_report(42, JOB["hash"])
        backend.advance(minutes=14)
        self.assertIsNone(store.claim_ai_report(42, JOB["hash"]))
        backend.advance(minutes=2)
        second = store.claim_ai_report(42, JOB["hash"])
        self.assertTrue(second)
        self.assertNotEqual(first, second)
        self.assertFalse(store.release_ai_report_claim(42, JOB["hash"], first))
        self.assertTrue(store.owns_claim(42, JOB["hash"], second, "llm_call"))

    def test_adoption_uses_current_claim_and_never_debits_twice(self):
        for kind in ("spend", "llm_call"):
            store, backend = make_store(balance=1)
            claim_fn = (lambda: store.claim_document(42, JOB["hash"], "cv.pdf")) if kind == "spend" else (lambda: store.claim_ai_report(42, JOB["hash"]))
            first = claim_fn()
            a = ops.CostedOperation(store, 42, JOB["hash"], spend_reason=kind).bind_claim(first)
            a.reserve()
            backend.advance(minutes=16)
            second = claim_fn()
            b = ops.CostedOperation(store, 42, JOB["hash"], spend_reason=kind).bind_claim(second)
            b.reserve()
            self.assertEqual(b.attempt, a.attempt)
            self.assertTrue(b.can_deliver())
            self.assertFalse(a.can_deliver())
            self.assertEqual(backend._balance(42), 0)
            self.assertEqual(len(backend.events), 1)
            self.assertEqual(b.mark_unknown(), "unknown")
            self.assertEqual(backend._balance(42), 0)


class RegistryFailureScopeTests(unittest.TestCase):
    def setUp(self):
        self.store, self.backend = make_store(balance=3)
        self.assertTrue(self.store._registry_available())
        self.addCleanup(setattr, store_module, "_CREDIT_OPS_TABLE_MISSING", False)
        self.addCleanup(setattr, store_module, "_CREDIT_OPS_RETRY_AT", 0.0)

    def test_http_failure_during_real_query_does_not_delay_next_reservation(self):
        for status in (400, 503):
            with self.subTest(status=status):
                failure = GrowthStoreUnavailable("transport response body must not be logged")
                failure.http_status = status
                with patch.object(self.backend, "execute", side_effect=failure), self.assertLogs(store_module.logger, "WARNING") as logs:
                    with self.assertRaises(GrowthStoreUnavailable) as caught:
                        self.store.reserve_credit(42, 1, "spend", "retry-" + str(status))
                self.assertIs(caught.exception, failure)
                self.assertFalse(store_module._CREDIT_OPS_TABLE_MISSING)
                self.assertEqual(store_module._CREDIT_OPS_RETRY_AT, 0.0)
                self.assertEqual(self.backend.events, [] if status == 400 else [(42, -1, "spend", "retry-400")])
                self.assertIn("not confirmed through bridge/backend", " ".join(logs.output))
                self.assertIn("HTTP " + str(status), " ".join(logs.output))
                self.assertNotIn("transport response body", " ".join(logs.output))
                # The next request reaches the database immediately, without advancing time.
                self.assertEqual(self.store.reserve_credit(42, 1, "spend", "retry-" + str(status)), 1)
        self.assertEqual(self.backend._balance(42), 1)
        self.assertEqual(len(self.backend.events), 2)

    def test_execute_value_error_does_not_delay_next_reservation(self):
        failure = ValueError("incorrect parameter count")
        with patch.object(self.backend, "execute", side_effect=failure), self.assertLogs(store_module.logger, "WARNING"):
            with self.assertRaises(ValueError) as caught:
                self.store.reserve_credit(42, 1, "spend", "job")
        self.assertIs(caught.exception, failure)
        self.assertFalse(store_module._CREDIT_OPS_TABLE_MISSING)
        self.assertEqual(self.backend.events, [])
        self.assertEqual(self.store.reserve_credit(42, 1, "spend", "job"), 1)
        self.assertEqual(self.backend._balance(42), 2)

    def test_connection_failure_does_not_delay_next_reservation(self):
        with patch.object(self.backend, "_ensure_conn", side_effect=TimeoutError("network timeout")), self.assertLogs(store_module.logger, "WARNING"):
            with self.assertRaises(CopilotStoreUnavailable):
                self.store.reserve_credit(42, 1, "spend", "job")
        self.assertFalse(store_module._CREDIT_OPS_TABLE_MISSING)
        self.assertEqual(self.backend.events, [])
        self.assertEqual(self.store.reserve_credit(42, 1, "spend", "job"), 1)
        self.assertEqual(self.backend._balance(42), 2)

    def test_result_processing_error_is_not_classified_as_database_failure(self):
        class MisleadingResultError(ValueError):
            sqlstate = "42P01"
        failure = MisleadingResultError("result conversion failed")
        with patch.object(self.store, "_registry_failed", wraps=self.store._registry_failed) as classify:
            with self.assertRaises(MisleadingResultError) as caught:
                with self.store._credit_cursor() as cur:
                    cur.execute("SELECT state FROM growth_copilot_credit_ops WHERE user_id = %s AND ref = %s AND kind = %s",
                                (42, "job", "spend"))
                    cur.fetchone()
                    raise failure
            classify.assert_not_called()
        self.assertIs(caught.exception, failure)
        self.assertFalse(store_module._CREDIT_OPS_TABLE_MISSING)
        self.assertEqual(self.store.reserve_credit(42, 1, "spend", "job"), 1)

    def test_missing_p13_during_real_query_blocks_until_successful_probe(self):
        class MissingP13(Exception):
            sqlstate = "42P01"
        with patch.object(self.backend, "execute", side_effect=MissingP13()), self.assertLogs(store_module.logger, "WARNING") as logs:
            with self.assertRaises(MissingP13):
                self.store.reserve_credit(42, 1, "spend", "job")
        self.assertTrue(store_module._CREDIT_OPS_TABLE_MISSING)
        self.assertIn("migration P13 is not applied", " ".join(logs.output))
        with patch.object(self.backend, "execute", wraps=self.backend.execute) as execute:
            with self.assertRaises(CopilotStoreUnavailable):
                self.store.reserve_credit(42, 1, "spend", "job")
            execute.assert_not_called()
        self.assertEqual(self.backend.events, [])
        with patch.object(store_module.time, "monotonic", return_value=store_module._CREDIT_OPS_RETRY_AT + 1):
            self.assertEqual(self.store.reserve_credit(42, 1, "spend", "job"), 1)
        self.assertFalse(store_module._CREDIT_OPS_TABLE_MISSING)
        self.assertEqual(self.backend._balance(42), 2)
