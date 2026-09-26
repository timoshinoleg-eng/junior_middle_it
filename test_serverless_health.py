import io
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import serverless_health as health


GROWTH_URL = "https://project.supabase.co/functions/v1/growth-proxy"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self, _limit):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


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

    def test_publication_counters_are_off_by_default_and_never_change_ok(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "token",
            "CHANNEL_ID": "@channel",
            "CRON_SECRET": "cron",
            "BOT_USERNAME": "junior_jobs_channel_bot",
            "GROWTH_PUBLIC_URL": GROWTH_URL,
        }
        with patch.dict(os.environ, env, clear=True):
            plain = health.readiness()
            with_counters = health.readiness(include_publication_counters=True)
        self.assertNotIn("publication", plain)
        self.assertEqual(with_counters["ok"], plain["ok"])
        self.assertIn("publication", with_counters)

    def test_publication_counters_count_ledger_rows_per_window(self):
        now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
        rows = [
            {"updated_at": (now - timedelta(minutes=30)).isoformat()},
            {"updated_at": (now - timedelta(hours=5)).isoformat()},
            {"updated_at": (now - timedelta(days=3)).isoformat()},
            {"updated_at": (now - timedelta(days=6)).isoformat()},
        ]
        with patch.dict(os.environ, {"GROWTH_PUBLIC_URL": GROWTH_URL}, clear=True), patch(
            "serverless_health.urlopen", return_value=_FakeResponse({"rows": rows})
        ):
            counters = health.publication_counters(now=now)
        self.assertEqual(counters["published_1h"], 1)
        self.assertEqual(counters["published_24h"], 2)
        self.assertEqual(counters["published_7d"], 4)

    def test_publication_counters_report_unknown_instead_of_a_false_zero(self):
        with patch.dict(os.environ, {}, clear=True):
            missing = health.publication_counters()
        self.assertFalse(missing["ledger_visible"])

        def _boom(*_args, **_kwargs):
            raise OSError("edge unreachable")

        with patch.dict(os.environ, {"GROWTH_PUBLIC_URL": GROWTH_URL}, clear=True), patch(
            "serverless_health.urlopen", side_effect=_boom
        ):
            unreachable = health.publication_counters()
        self.assertTrue(unreachable["ledger_visible"])
        self.assertIsNone(unreachable["published_24h"])

    def test_public_jobs_url_rejects_a_non_supabase_host(self):
        with patch.dict(
            os.environ, {"GROWTH_PUBLIC_URL": "https://example.com/growth-proxy"}, clear=True
        ):
            self.assertEqual(health._public_jobs_url(), "")
        with patch.dict(os.environ, {"GROWTH_PUBLIC_URL": GROWTH_URL}, clear=True):
            self.assertEqual(
                health._public_jobs_url(),
                "https://project.supabase.co/functions/v1/growth-proxy/public-jobs",
            )


if __name__ == "__main__":
    unittest.main()
