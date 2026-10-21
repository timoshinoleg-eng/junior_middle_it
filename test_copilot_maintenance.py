"""Server-side maintenance gates cover stale callbacks and pending uploads."""
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from copilot import paid_operations_enabled
import copilot_webhook_runtime as runtime
from test_copilot_credit_operations import make_store, FakeDb, JOB, call_api, pdf_request
from test_copilot_runtime import FakeMessage, FakeQuery, FakeUpdate


class MaintenanceTests(unittest.TestCase):
    def test_api_rejects_either_closed_gate_before_paid_work(self):
        for master, pdf in (("false", "true"), ("true", "false"), ("", "")):
            with self.subTest(master=master, pdf=pdf):
                store, backend = make_store(balance=3)
                with patch.object(store, "reserve_credit") as reserve, patch.object(runtime.copilot_pdf, "build_cv_pdf") as build, patch("api.copilot.copilot_webapp.send_document_to_chat") as send:
                    status, payload = call_api(store, FakeDb(backend, JOB), pdf_request(), env={"COPILOT_ENABLED": master, "COPILOT_PDF_ENABLED": pdf})
                self.assertEqual(status, 503)
                self.assertEqual(payload, {"ok": False, "error": "maintenance", "message": "Разбор резюме временно недоступен: технические работы. Попробуй позже."})
                reserve.assert_not_called()
                build.assert_not_called()
                send.assert_not_called()
                self.assertEqual(backend.events, [])

    def test_open_api_preserves_delivery(self):
        store, backend = make_store(balance=3)
        with patch("api.copilot.copilot_webapp.send_document_to_chat", return_value={"file_id": "FILE"}) as send:
            status, payload = call_api(store, FakeDb(backend, JOB), pdf_request(), env={"COPILOT_ENABLED": "true", "COPILOT_PDF_ENABLED": "true", "COPILOT_FREE_PER_DAY": "0"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(backend._balance(42), 2)
        send.assert_called_once()

    def test_closed_callbacks_never_enter_paid_handlers(self):
        for action in ("copilot_ai", "copilot_pdf"):
            message = FakeMessage()
            query = FakeQuery(message)
            query.data = action + ":job"
            update = FakeUpdate(message=message, query=query)
            with patch.dict(os.environ, {"COPILOT_ENABLED": "false", "COPILOT_PDF_ENABLED": "true"}), patch.object(runtime, "_handle_ai", new_callable=AsyncMock) as ai, patch.object(runtime, "_handle_pdf", new_callable=AsyncMock) as pdf:
                asyncio.run(runtime._copilot_handle_callback(SimpleNamespace(), update, SimpleNamespace()))
            self.assertIn("Технические работы, попробуй позже.", query.answers)
            ai.assert_not_called()
            pdf.assert_not_called()

    def test_direct_ai_and_pending_upload_refuse_before_work(self):
        message = FakeMessage()
        query = FakeQuery(message)
        update = FakeUpdate(message=message, query=query)
        with patch.dict(os.environ, {"COPILOT_ENABLED": "false", "COPILOT_PDF_ENABLED": "true"}), patch.object(runtime.CopilotStore, "from_database") as store, patch.object(runtime.copilot_llm, "analyze") as provider, patch.object(runtime.copilot_pdf, "build_cv_pdf") as build:
            asyncio.run(runtime._handle_ai(SimpleNamespace(), update, "job"))
            asyncio.run(runtime._build_cv_from_message(SimpleNamespace(), update, SimpleNamespace(), {}))
        store.assert_not_called()
        provider.assert_not_called()
        build.assert_not_called()
        self.assertIn("Технические работы, попробуй позже.", query.answers)
        self.assertIn("Технические работы, попробуй позже.", [text for text, _ in message.replies])

    def test_gate_reads_environment_on_every_call(self):
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true", "COPILOT_PDF_ENABLED": "true"}):
            self.assertTrue(paid_operations_enabled(pdf=True))
            os.environ["COPILOT_ENABLED"] = "false"
            self.assertFalse(paid_operations_enabled(pdf=True))
            os.environ["COPILOT_ENABLED"] = "true"
            os.environ["COPILOT_PDF_ENABLED"] = "false"
            self.assertFalse(paid_operations_enabled(pdf=True))
            self.assertTrue(paid_operations_enabled())

    def test_authentication_remains_first_during_maintenance(self):
        store, backend = make_store(balance=3)
        status, payload = call_api(store, FakeDb(backend, JOB), {"init_data": "invalid"}, env={"COPILOT_ENABLED": "false", "COPILOT_PDF_ENABLED": "false"})
        self.assertEqual(status, 401)
        self.assertEqual(backend.events, [])

    def test_pdf_flag_alone_closes_callback_and_pending_upload(self):
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_pdf:job"
        update = FakeUpdate(message=message, query=query)
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true", "COPILOT_PDF_ENABLED": "false"}), patch.object(runtime, "_handle_pdf", new_callable=AsyncMock) as pdf, patch.object(runtime.CopilotStore, "from_database") as store:
            asyncio.run(runtime._copilot_handle_callback(SimpleNamespace(), update, SimpleNamespace()))
            asyncio.run(runtime._build_cv_from_message(SimpleNamespace(), update, SimpleNamespace(), {}))
        pdf.assert_not_called()
        store.assert_not_called()
        self.assertIn("Технические работы, попробуй позже.", query.answers)
        self.assertIn("Технические работы, попробуй позже.", [text for text, _ in message.replies])
