import os
import unittest
from unittest.mock import patch

import serverless_health as health


class ServerlessHealthTests(unittest.TestCase):
    def test_missing_posting_env_is_explicit_but_value_free(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                health.missing_posting_env(),
                ["TELEGRAM_BOT_TOKEN", "CHANNEL_ID", "CRON_SECRET"],
            )

    def test_cron_auth_fails_closed_and_uses_bearer_only(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(health.cron_authorized("Bearer anything"))

        with patch.dict(os.environ, {"CRON_SECRET": "secret-value"}, clear=True):
            self.assertTrue(health.cron_authorized("Bearer secret-value"))
            self.assertFalse(health.cron_authorized("secret-value"))
            self.assertFalse(health.cron_authorized("?secret=secret-value"))
            self.assertFalse(health.cron_authorized("Bearer wrong"))

    def test_readiness_distinguishes_working_collector_from_full_p7(self):
        base = {
            "TELEGRAM_BOT_TOKEN": "token",
            "CHANNEL_ID": "@channel",
            "CRON_SECRET": "cron",
            "BOT_USERNAME": "junior_jobs_channel_bot",
        }
        with patch.dict(os.environ, base, clear=True):
            state = health.readiness()
            self.assertTrue(state["collector_ready"])
            self.assertFalse(state["durable_growth"])
            self.assertFalse(state["public_acquisition_ready"])
            self.assertFalse(state["ok"])

        with patch.dict(
            os.environ,
            {**base, "GROWTH_DATABASE_URL": "postgresql://user:pass@db/db"},
            clear=True,
        ):
            state = health.readiness()
            self.assertTrue(state["collector_ready"])
            self.assertTrue(state["durable_growth"])
            self.assertTrue(state["public_acquisition_ready"])
            self.assertTrue(state["ok"])
            self.assertNotIn("postgresql://", repr(state))

    def test_database_url_compatibility_fallback_counts_as_durable(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://x"}, clear=True):
            self.assertTrue(health.durable_growth_configured())

    def test_public_edge_projection_counts_as_durable_for_vercel(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "token",
            "CHANNEL_ID": "@channel",
            "CRON_SECRET": "cron",
            "BOT_USERNAME": "junior_jobs_channel_bot",
            "GROWTH_PUBLIC_URL": "https://project.supabase.co/functions/v1/growth-proxy",
        }
        with patch.dict(os.environ, env, clear=True):
            state = health.readiness()
        self.assertTrue(state["durable_growth"])
        self.assertTrue(state["public_acquisition_ready"])
        self.assertTrue(state["ok"])


if __name__ == "__main__":
    unittest.main()
