import asyncio
import inspect
import unittest
from unittest.mock import patch

import channel_bot as core
from publication_authority import (
    PublicChannelPublicationDenied,
    install_telegram_channel_publish_guard,
    is_canonical_production_publisher,
    protected_channel_ids,
    should_block_public_channel_send,
)


class PublicationAuthorityTests(unittest.TestCase):
    def test_only_production_vercel_is_canonical(self):
        self.assertTrue(
            is_canonical_production_publisher(
                {"VERCEL": "1", "VERCEL_ENV": "production"}
            )
        )
        self.assertFalse(
            is_canonical_production_publisher(
                {"VERCEL": "1", "VERCEL_ENV": "preview"}
            )
        )
        self.assertFalse(is_canonical_production_publisher({"RAILWAY_ENVIRONMENT": "production"}))

    def test_protected_channels_include_main_shortcuts_and_routes(self):
        env = {
            "CHANNEL_ID": "@junior_middle_it",
            "CHANNEL_ID_QA": "@junior_qa",
            "CHANNEL_ROUTES": "development,devops:@junior_dev;data:-100123456;*:@junior_middle_it",
        }
        self.assertEqual(
            protected_channel_ids(env),
            {"@junior_middle_it", "@junior_qa", "@junior_dev", "-100123456"},
        )

    def test_render_collector_skips_blocked_channel_posts_before_fetching(self):
        with patch.dict(
            "os.environ",
            {"RENDER_SERVICE_ID": "srv-test", "VERCEL": "", "VERCEL_ENV": ""},
        ), patch.object(core.Config, "validate", return_value=True), patch.object(
            core, "configure_webshare_proxy", side_effect=AssertionError("must not crawl")
        ):
            result = asyncio.run(core.collect_and_post_once(use_sqlite=False))
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["posted"], 0)
        self.assertEqual(result["reason"], "vercel_publication_authority")

    def test_render_persistent_loop_respects_publication_authority(self):
        # Regression: Render's main() uses a DIFFERENT fetch loop from
        # collect_and_post_once(). Both must honor the same authority gate.
        with patch.dict(
            "os.environ",
            {"RENDER_SERVICE_ID": "srv-render", "VERCEL": "", "VERCEL_ENV": ""},
        ):
            self.assertTrue(core._render_crawl_blocked())
        with patch.dict(
            "os.environ",
            {"RENDER_SERVICE_ID": "", "VERCEL": "1", "VERCEL_ENV": "production"},
        ):
            self.assertFalse(core._render_crawl_blocked())
        source = inspect.getsource(core.main)
        self.assertIn("if _render_crawl_blocked():", source)
        self.assertIn('CYCLE_TELEMETRY["last_source"] = "disabled_noncanonical_render"', source)

    def test_non_vercel_public_channel_is_blocked_but_dm_is_allowed(self):
        env = {"CHANNEL_ID": "@junior_middle_it"}
        self.assertTrue(should_block_public_channel_send("@junior_middle_it", env))
        self.assertFalse(should_block_public_channel_send(123456789, env))

    def test_vercel_production_public_channel_is_allowed(self):
        env = {
            "VERCEL": "1",
            "VERCEL_ENV": "production",
            "CHANNEL_ID": "@junior_middle_it",
        }
        self.assertFalse(should_block_public_channel_send("@junior_middle_it", env))

    def test_transport_guard_rejects_non_vercel_channel_send(self):
        calls = []

        class DummyBot:
            async def send_message(self, chat_id, text, **kwargs):
                calls.append((chat_id, text))
                return "ok"

        self.assertTrue(install_telegram_channel_publish_guard(DummyBot))
        self.assertFalse(install_telegram_channel_publish_guard(DummyBot))

        with patch.dict(
            "os.environ",
            {"CHANNEL_ID": "@junior_middle_it", "VERCEL": "", "VERCEL_ENV": ""},
            clear=False,
        ):
            with self.assertRaises(PublicChannelPublicationDenied):
                asyncio.run(DummyBot().send_message("@junior_middle_it", "vacancy"))

        self.assertEqual(calls, [])

    def test_transport_guard_preserves_direct_messages(self):
        calls = []

        class DummyBot:
            async def send_message(self, chat_id, text, **kwargs):
                calls.append((chat_id, text))
                return "ok"

        install_telegram_channel_publish_guard(DummyBot)
        with patch.dict(
            "os.environ",
            {"CHANNEL_ID": "@junior_middle_it", "VERCEL": "", "VERCEL_ENV": ""},
            clear=False,
        ):
            result = asyncio.run(DummyBot().send_message(123456789, "hello"))

        self.assertEqual(result, "ok")
        self.assertEqual(calls, [(123456789, "hello")])

    def test_transport_guard_allows_production_vercel(self):
        calls = []

        class DummyBot:
            async def send_message(self, chat_id, text, **kwargs):
                calls.append((chat_id, text))
                return "ok"

        install_telegram_channel_publish_guard(DummyBot)
        with patch.dict(
            "os.environ",
            {
                "CHANNEL_ID": "@junior_middle_it",
                "VERCEL": "1",
                "VERCEL_ENV": "production",
            },
            clear=False,
        ):
            result = asyncio.run(DummyBot().send_message("@junior_middle_it", "fresh vacancy"))

        self.assertEqual(result, "ok")
        self.assertEqual(calls, [("@junior_middle_it", "fresh vacancy")])


if __name__ == "__main__":
    unittest.main()
