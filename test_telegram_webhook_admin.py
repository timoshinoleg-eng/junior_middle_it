import os
import unittest
from unittest.mock import patch

import telegram_webhook_admin as admin


class TelegramWebhookAdminTests(unittest.TestCase):
    def test_enable_refuses_preview(self):
        with patch.dict(
            os.environ,
            {
                "VERCEL_ENV": "preview",
                "TELEGRAM_BOT_TOKEN": "12345:abcdefghijklmnopqrstuvwxyz",
                "VERCEL_PROJECT_PRODUCTION_URL": "junior-middle-it.vercel.app",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "not_production"):
                admin.enable_webhook()

    def test_enable_preflights_and_verifies_exact_production_url(self):
        expected_url = "https://junior-middle-it.vercel.app/api/webhook"
        calls = []

        def fake_api(method, payload=None, timeout=15.0):
            calls.append((method, payload))
            if method == "getWebhookInfo":
                return {
                    "ok": True,
                    "result": {
                        "url": expected_url,
                        "pending_update_count": 0,
                        "max_connections": 20,
                        "allowed_updates": ["message", "callback_query"],
                    },
                }
            return {"ok": True, "result": True}

        with patch.dict(
            os.environ,
            {
                "VERCEL_ENV": "production",
                "TELEGRAM_BOT_TOKEN": "12345:abcdefghijklmnopqrstuvwxyz",
                "VERCEL_PROJECT_PRODUCTION_URL": "junior-middle-it.vercel.app",
            },
            clear=True,
        ), patch.object(admin, "interactive_backend_preflight") as preflight, patch.object(
            admin, "_telegram_api", side_effect=fake_api
        ):
            info = admin.enable_webhook()

        preflight.assert_called_once_with()
        self.assertEqual(info["url"], expected_url)
        self.assertEqual(calls[0][0], "setWebhook")
        payload = calls[0][1]
        self.assertEqual(payload["url"], expected_url)
        self.assertEqual(payload["allowed_updates"], ["message", "callback_query"])
        self.assertFalse(payload["drop_pending_updates"])
        self.assertEqual(payload["max_connections"], 20)
        self.assertEqual(len(payload["secret_token"]), 64)
        self.assertEqual(calls[1][0], "getWebhookInfo")

    def test_disable_refuses_preview(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "preview"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "not_production"):
                admin.disable_webhook()

    def test_status_is_sanitized_and_bounded(self):
        huge = "x" * 1000
        with patch.object(
            admin,
            "_telegram_api",
            return_value={
                "ok": True,
                "result": {
                    "url": "https://example.com/hook",
                    "pending_update_count": 2,
                    "last_error_message": huge,
                },
            },
        ):
            info = admin.webhook_status()
        self.assertEqual(info["pending_update_count"], 2)
        self.assertLessEqual(len(info["last_error_message"]), 300)


if __name__ == "__main__":
    unittest.main()
