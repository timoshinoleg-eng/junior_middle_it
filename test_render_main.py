import os
import unittest
from unittest.mock import MagicMock, patch

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
                "GROWTH_DATABASE_URL": "https://project.supabase.co/functions/v1/growth-proxy",
                "GROWTH_HTTP_KEY": "bridge-secret",
            },
            clear=True,
        ):
            summary = render_main.config_summary()
        rendered = repr(summary)
        self.assertTrue(summary["TELEGRAM_BOT_TOKEN"])
        self.assertTrue(summary["CHANNEL_ID"])
        self.assertTrue(summary["GROWTH_DATABASE"])
        self.assertTrue(summary["GROWTH_HTTP_KEY"])
        self.assertNotIn("super-secret", rendered)
        self.assertNotIn("private-ish-channel", rendered)
        self.assertNotIn("project.supabase.co", rendered)
        self.assertNotIn("bridge-secret", rendered)

    def test_direct_postgres_skips_http_bridge_preflight(self):
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "postgresql://example/db"},
            clear=True,
        ):
            self.assertEqual(render_main.durable_growth_preflight(), (True, "not_http_bridge"))

    def test_http_bridge_preflight_executes_bounded_read_only_probe(self):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with patch.dict(
            os.environ,
            {
                "GROWTH_DATABASE_URL": "https://project.supabase.co/functions/v1/growth-proxy",
                "GROWTH_HTTP_KEY": "secret",
            },
            clear=True,
        ), patch("http_growth_patch.RemoteGrowthConnection", return_value=conn) as ctor:
            ok, status = render_main.durable_growth_preflight()

        self.assertTrue(ok)
        self.assertEqual(status, "ok")
        ctor.assert_called_once_with(
            "https://project.supabase.co/functions/v1/growth-proxy",
            "secret",
            timeout=10.0,
        )
        cursor.execute.assert_called_once_with(
            "SELECT user_id FROM growth_user_settings LIMIT 0"
        )
        conn.close.assert_called_once()

    def test_http_bridge_preflight_sanitizes_failure_type(self):
        with patch.dict(
            os.environ,
            {
                "GROWTH_DATABASE_URL": "https://project.supabase.co/functions/v1/growth-proxy",
                "GROWTH_HTTP_KEY": "secret",
            },
            clear=True,
        ), patch(
            "http_growth_patch.RemoteGrowthConnection",
            side_effect=RuntimeError("do not expose this detail"),
        ):
            self.assertEqual(
                render_main.durable_growth_preflight(),
                (False, "RuntimeError"),
            )


if __name__ == "__main__":
    unittest.main()
