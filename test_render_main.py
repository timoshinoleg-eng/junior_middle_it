import os
import unittest
from unittest.mock import patch

import render_main


class RenderMainTests(unittest.TestCase):
    def setUp(self):
        with render_main.STATE_LOCK:
            render_main.STATE.update(
                started_at=render_main.time.time(),
                bot_thread_started=False,
                bot_running=False,
                error=None,
            )

    def test_missing_required_env(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                render_main.missing_required_env(),
                ["TELEGRAM_BOT_TOKEN", "CHANNEL_ID"],
            )
        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "token", "CHANNEL_ID": "@channel"},
            clear=True,
        ):
            self.assertEqual(render_main.missing_required_env(), [])

    def test_health_is_503_until_worker_is_running(self):
        status, payload = render_main.health_snapshot()
        self.assertEqual(status, 503)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["bot"], "starting")

        with render_main.STATE_LOCK:
            render_main.STATE["bot_running"] = True
        status, payload = render_main.health_snapshot()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["bot"], "running")

    def test_health_is_503_on_runtime_failure_and_error_is_sanitized(self):
        render_main._set_runtime_failure("bot_runtime_failed:SystemExit")
        status, payload = render_main.health_snapshot()
        self.assertEqual(status, 503)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["bot"], "crashed")
        self.assertEqual(payload["error"], "bot_runtime_failed:SystemExit")

    def test_config_summary_never_exposes_secret_values(self):
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "123456:super-secret",
                "CHANNEL_ID": "@private-ish-channel",
                "GROWTH_DATABASE_URL": "postgresql://secret:password@example/db",
            },
            clear=True,
        ):
            summary = render_main.config_summary()
        rendered = repr(summary)
        self.assertTrue(summary["TELEGRAM_BOT_TOKEN"])
        self.assertTrue(summary["CHANNEL_ID"])
        self.assertTrue(summary["GROWTH_DATABASE"])
        self.assertNotIn("super-secret", rendered)
        self.assertNotIn("private-ish-channel", rendered)
        self.assertNotIn("postgresql://", rendered)


if __name__ == "__main__":
    unittest.main()
