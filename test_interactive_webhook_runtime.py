import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import interactive_webhook_runtime as runtime


class InteractiveWebhookConfigTests(unittest.TestCase):
    def test_interactive_growth_url_is_pinned_to_same_supabase_project_host(self):
        base = "https://abc.supabase.co/functions/v1/growth-proxy"
        self.assertEqual(
            runtime.interactive_growth_url(base),
            "https://abc.supabase.co/functions/v1/interactive-growth-proxy",
        )
        for bad in (
            "http://abc.supabase.co/functions/v1/growth-proxy",
            "https://example.com/functions/v1/growth-proxy",
            "https://abc.supabase.co/functions/v1/other",
        ):
            self.assertEqual(runtime.interactive_growth_url(bad), "")

    def test_webhook_secret_is_deterministic_and_constant_time_checked(self):
        expected = runtime.webhook_secret("12345:abcdefghijklmnopqrstuvwxyz")
        self.assertEqual(len(expected), 64)
        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "12345:abcdefghijklmnopqrstuvwxyz"},
            clear=True,
        ):
            self.assertTrue(runtime.webhook_secret_valid(expected))
            self.assertFalse(runtime.webhook_secret_valid(expected[:-1] + "0"))
            self.assertFalse(runtime.webhook_secret_valid(""))

    def test_production_webhook_url_prefers_explicit_then_vercel_domain(self):
        with patch.dict(
            os.environ,
            {"WEBHOOK_PUBLIC_URL": "https://jobs.example.com/hook"},
            clear=True,
        ):
            self.assertEqual(
                runtime.production_webhook_url(),
                "https://jobs.example.com/hook/api/webhook",
            )
        with patch.dict(
            os.environ,
            {"VERCEL_PROJECT_PRODUCTION_URL": "junior-middle-it.vercel.app"},
            clear=True,
        ):
            self.assertEqual(
                runtime.production_webhook_url(),
                "https://junior-middle-it.vercel.app/api/webhook",
            )


class ProcessUpdateTests(unittest.IsolatedAsyncioTestCase):
    class FakeDB:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class FakeApp:
        def __init__(self, fail=False):
            self.fail = fail
            self.error_handler = None
            self.bot = object()

        def add_error_handler(self, callback):
            self.error_handler = callback

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def process_update(self, update):
            if self.fail:
                await self.error_handler(update, SimpleNamespace(error=RuntimeError("boom")))

    async def test_processed_update_completes_ledger(self):
        db = self.FakeDB()
        app = self.FakeApp()
        ledger = MagicMock()
        ledger.claim.return_value = "claimed"
        with patch.object(runtime, "build_runtime", return_value=(app, db, object())), patch.object(
            runtime, "TelegramUpdateLedger", return_value=ledger
        ), patch("telegram.Update.de_json", return_value=object()):
            state = await runtime.process_update_payload({"update_id": 123})
        self.assertEqual(state, "processed")
        ledger.complete.assert_called_once_with(123)
        self.assertTrue(db.closed)

    async def test_processed_duplicate_returns_200_semantic_without_dispatch(self):
        db = self.FakeDB()
        app = self.FakeApp()
        ledger = MagicMock()
        ledger.claim.return_value = "processed"
        with patch.object(runtime, "build_runtime", return_value=(app, db, object())), patch.object(
            runtime, "TelegramUpdateLedger", return_value=ledger
        ):
            state = await runtime.process_update_payload({"update_id": 124})
        self.assertEqual(state, "duplicate")
        ledger.complete.assert_not_called()
        self.assertTrue(db.closed)

    async def test_busy_update_remains_retryable(self):
        db = self.FakeDB()
        app = self.FakeApp()
        ledger = MagicMock()
        ledger.claim.return_value = "busy"
        with patch.object(runtime, "build_runtime", return_value=(app, db, object())), patch.object(
            runtime, "TelegramUpdateLedger", return_value=ledger
        ):
            state = await runtime.process_update_payload({"update_id": 125})
        self.assertEqual(state, "busy")
        ledger.complete.assert_not_called()
        self.assertTrue(db.closed)

    async def test_handler_error_is_not_marked_processed(self):
        db = self.FakeDB()
        app = self.FakeApp(fail=True)
        ledger = MagicMock()
        ledger.claim.return_value = "claimed"
        with patch.object(runtime, "build_runtime", return_value=(app, db, object())), patch.object(
            runtime, "TelegramUpdateLedger", return_value=ledger
        ), patch("telegram.Update.de_json", return_value=object()):
            with self.assertRaisesRegex(RuntimeError, "handler failed"):
                await runtime.process_update_payload({"update_id": 126})
        ledger.complete.assert_not_called()
        self.assertTrue(db.closed)

    async def test_invalid_update_id_fails_before_runtime_creation(self):
        with patch.object(runtime, "build_runtime") as builder:
            with self.assertRaises(ValueError):
                await runtime.process_update_payload({"update_id": True})
        builder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
