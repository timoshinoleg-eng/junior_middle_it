"""Regression tests for Stage 0 (stop-valve) fixes — B01/B02/B03/B04/B05/B06/B12/B20.

No network, no live Telegram: all Telegram I/O is mocked.
"""
import asyncio
import importlib.util
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import channel_bot
from channel_bot import (
    Config,
    JobBot,
    _send_job_to_chat,
    collect_and_post_once,
    env_int_positive,
    select_jobs_for_publication,
)
from message_formatter import JobMessageFormatter

from telegram.error import RetryAfter


def _load_api_cron():
    """Load api/cron.py without requiring api/ to be a package."""
    spec = importlib.util.spec_from_file_location(
        "api_cron_module", os.path.join(REPO_ROOT, "api", "cron.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api_cron = _load_api_cron()


class EnvIntPositiveTests(unittest.TestCase):
    def test_zero_is_clamped_to_default(self):
        with mock.patch.dict(os.environ, {"EMERGENCY_MAX_POSTS_PER_CYCLE": "0"}):
            self.assertEqual(env_int_positive("EMERGENCY_MAX_POSTS_PER_CYCLE", 20), 20)

    def test_negative_is_clamped_to_default(self):
        with mock.patch.dict(os.environ, {"EMERGENCY_MAX_POSTS_PER_CYCLE": "-5"}):
            self.assertEqual(env_int_positive("EMERGENCY_MAX_POSTS_PER_CYCLE", 20), 20)

    def test_positive_value_is_kept(self):
        with mock.patch.dict(os.environ, {"EMERGENCY_MAX_POSTS_PER_CYCLE": "7"}):
            self.assertEqual(env_int_positive("EMERGENCY_MAX_POSTS_PER_CYCLE", 20), 7)


class ConfigValidationTests(unittest.TestCase):
    def _patched(self, **attrs):
        return mock.patch.multiple(channel_bot.Config, **attrs)

    def test_missing_admin_user_id_fails_when_required(self):
        with self._patched(
            TELEGRAM_BOT_TOKEN="t", CHANNEL_ID="@c",
            ADMIN_USER_ID=None, CRON_SECRET="s",
        ):
            self.assertFalse(Config.validate(require_admin=True))
            # not required → passes
            self.assertTrue(Config.validate())

    def test_missing_cron_secret_fails_when_required(self):
        with self._patched(
            TELEGRAM_BOT_TOKEN="t", CHANNEL_ID="@c",
            ADMIN_USER_ID="1", CRON_SECRET=None,
        ):
            self.assertFalse(Config.validate(require_cron_secret=True))
            self.assertTrue(Config.validate(require_admin=True))


class CheckAdminFailClosedTests(unittest.TestCase):
    def _bot(self):
        return JobBot(application=None, db=None)

    def _update(self, user_id):
        message = SimpleNamespace(reply_text=mock.AsyncMock())
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id), message=message
        )

    def test_no_admin_configured_denies_everyone(self):
        bot = self._bot()
        with mock.patch.object(Config, "ADMIN_USER_ID", None):
            self.assertFalse(asyncio.run(bot.check_admin(self._update(42))))

    def test_admin_passes_and_stranger_denied(self):
        bot = self._bot()
        with mock.patch.object(Config, "ADMIN_USER_ID", 42):
            self.assertTrue(asyncio.run(bot.check_admin(self._update(42))))
            self.assertFalse(asyncio.run(bot.check_admin(self._update(43))))


class CronAuthTests(unittest.TestCase):
    def test_missing_secret_fails_closed_with_503(self):
        status, payload = api_cron.authorize_cron_request(
            {"authorization": "Bearer anything"}, None
        )
        self.assertEqual(status, 503)
        self.assertFalse(payload["ok"])

    def test_wrong_bearer_rejected(self):
        status, _ = api_cron.authorize_cron_request(
            {"authorization": "Bearer wrong"}, "sekrit"
        )
        self.assertEqual(status, 401)

    def test_no_header_rejected(self):
        status, _ = api_cron.authorize_cron_request({}, "sekrit")
        self.assertEqual(status, 401)

    def test_query_string_secret_is_not_a_vector(self):
        # The query string is never consulted; a URL-borne secret cannot pass.
        status, _ = api_cron.authorize_cron_request({}, "sekrit")
        self.assertEqual(status, 401)

    def test_correct_bearer_passes(self):
        status, payload = api_cron.authorize_cron_request(
            {"authorization": "Bearer sekrit"}, "sekrit"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, {})


class CronHandlerTests(unittest.TestCase):
    def _run_handler(self, secret_env, headers, collect_result=None, collect_raises=None):
        handler = api_cron.handler.__new__(api_cron.handler)
        handler.path = "/api/cron"
        handler.headers = headers
        calls = []

        def fake_send_json(self, status, payload):
            calls.append((status, payload))

        async def fake_collect(**kwargs):
            if collect_raises is not None:
                raise collect_raises
            return collect_result or {"ok": True}

        with mock.patch.object(api_cron.handler, "_send_json", fake_send_json), \
             mock.patch.object(api_cron, "collect_and_post_once", fake_collect), \
             mock.patch.object(Config, "CRON_SECRET", secret_env):
            handler.do_GET()
        return calls

    def test_unauthorized_returns_401_without_running_cycle(self):
        calls = self._run_handler("sekrit", {}, collect_result={"ok": True})
        self.assertEqual(calls, [(401, {"ok": False, "error": "unauthorized"})])

    def test_success_returns_200(self):
        calls = self._run_handler(
            "sekrit", {"authorization": "Bearer sekrit"}, collect_result={"ok": True}
        )
        self.assertEqual(calls[0][0], 200)
        self.assertTrue(calls[0][1]["ok"])

    def test_error_body_has_no_traceback(self):
        calls = self._run_handler(
            "sekrit",
            {"authorization": "Bearer sekrit"},
            collect_raises=RuntimeError("boom-secret-leak"),
        )
        status, payload = calls[0]
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"], "internal_error")
        self.assertNotIn("boom-secret-leak", str(payload))
        self.assertNotIn("Traceback", str(payload))


class KillSwitchTests(unittest.TestCase):
    def test_publish_disabled_skips_cycle(self):
        with mock.patch.object(Config, "PUBLISH_ENABLED", False), \
             mock.patch.object(Config, "validate", side_effect=AssertionError("must not run")):
            result = asyncio.run(collect_and_post_once())
        self.assertEqual(result, {"ok": True, "skipped": "publish_disabled"})

    def test_non_publisher_role_skips_cycle(self):
        with mock.patch.object(Config, "PUBLISH_ENABLED", True), \
             mock.patch.object(Config, "PROCESS_ROLE", "bot"), \
             mock.patch.object(Config, "validate", side_effect=AssertionError("must not run")):
            result = asyncio.run(collect_and_post_once())
        self.assertEqual(result, {"ok": True, "skipped": "role:bot"})


class NonInteractiveKeyboardTests(unittest.TestCase):
    def test_non_interactive_card_has_no_callbacks(self):
        formatter = JobMessageFormatter()
        job = {"hash": "abc123", "url": "https://example.com/j/1",
               "title": "Junior Dev", "level": "Junior", "category": "development"}
        kb = formatter.create_inline_keyboard(job, "compact", interactive=False)
        buttons = [b for row in kb["inline_keyboard"] for b in row]
        self.assertTrue(buttons)
        for button in buttons:
            self.assertNotIn("callback_data", button)
            self.assertNotIn("switch_inline_query", button)
        self.assertEqual(buttons[0]["url"], "https://example.com/j/1")

    def test_interactive_card_still_has_save_button(self):
        formatter = JobMessageFormatter()
        job = {"hash": "abc123", "url": "https://example.com/j/1",
               "title": "Junior Dev", "level": "Junior", "category": "development"}
        kb = formatter.create_inline_keyboard(job, "compact", interactive=True)
        callbacks = [
            b.get("callback_data", "") for row in kb["inline_keyboard"] for b in row
        ]
        self.assertTrue(any(c.startswith("save:") for c in callbacks))


class FloodRetryBoundedTests(unittest.TestCase):
    def test_retry_after_retries_are_bounded(self):
        send_calls = {"n": 0}

        async def always_flood(**kwargs):
            send_calls["n"] += 1
            raise RetryAfter(0)

        bot = SimpleNamespace(send_message=always_flood)
        job = {"title": "Junior Dev", "company": "Acme", "url": "https://e.com/1",
               "level": "Junior", "category": "development", "source": "Test",
               "location": "Remote", "salary": "Не указана"}
        async def no_sleep(_):
            return None

        with mock.patch.object(Config, "ENABLE_MARKDOWN_V2", False), \
             mock.patch.object(asyncio, "sleep", no_sleep):
            result = asyncio.run(_send_job_to_chat(bot, job, "@chan"))
        self.assertFalse(result)
        # initial attempt + exactly 3 bounded retries, then give up
        self.assertEqual(send_calls["n"], 4)


if __name__ == "__main__":
    unittest.main()
