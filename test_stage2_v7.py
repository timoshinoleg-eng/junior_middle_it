"""Regression tests for Stage 2 (user bot) — B14/B15/B16/B17/B18/B19.

No network, no live Telegram: all Telegram I/O is mocked.
"""
import asyncio
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import channel_bot
from channel_bot import (
    Config,
    DatabaseConnection,
    JobBot,
    _on_unhandled_error,
    _post_init_bot,
    run_v7_migration,
)
from message_formatter import JobMessageFormatter


class TempDbTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = DatabaseConnection(self.path)
        run_v7_migration(self.db)
        self.bot = JobBot(application=None, db=self.db)

    def tearDown(self):
        try:
            self.db.conn.close()
        finally:
            os.unlink(self.path)

    def _job(self, hash_, url="https://e.com/1"):
        return {"hash": hash_, "title": "Junior Dev", "company": "Acme",
                "url": url, "level": "Junior", "category": "development",
                "source": "Test", "location": "Remote", "salary": "Не указана"}


class ShareButtonTests(unittest.TestCase):
    def test_share_is_url_button_not_inline_query(self):
        formatter = JobMessageFormatter()
        job = {"hash": "h", "url": "https://e.com/j/1", "title": "Junior Dev",
               "level": "Junior", "category": "development"}
        kb = formatter.create_inline_keyboard(job, "compact", interactive=True)
        buttons = [b for row in kb["inline_keyboard"] for b in row]
        for b in buttons:
            self.assertNotIn("switch_inline_query", b)  # B16: dead vector removed
        share = next((b for b in buttons if "Поделиться" in b.get("text", "")), None)
        self.assertIsNotNone(share)
        self.assertTrue(share["url"].startswith("https://t.me/share/url?url="))
        self.assertIn("https%3A%2F%2Fe.com%2Fj%2F1", share["url"])


class ExpandToDMTests(TempDbTestCase):
    def _update(self, chat_type):
        query = SimpleNamespace(
            data="expand:h1",
            message=SimpleNamespace(chat=SimpleNamespace(type=chat_type)),
            answer=mock.AsyncMock(),
            edit_message_text=mock.AsyncMock(),
        )
        return SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=7),
        )

    def test_channel_expand_sends_dm_and_does_not_edit_post(self):
        job = self._job("h1")
        self.db.save_job_payload("h1", job)
        update = self._update("channel")
        send_message = mock.AsyncMock()
        context = SimpleNamespace(bot=SimpleNamespace(send_message=send_message),
                                  user_data={})
        with mock.patch.object(Config, "BOT_USERNAME", "testbot"):
            asyncio.run(self.bot.handle_callback(update, context))
        send_message.assert_awaited_once()
        self.assertEqual(send_message.await_args.kwargs["chat_id"], 7)  # DM to user
        update.callback_query.edit_message_text.assert_not_awaited()  # B17

    def test_private_expand_edits_in_place(self):
        job = self._job("h1")
        self.db.save_job_payload("h1", job)
        update = self._update("private")
        send_message = mock.AsyncMock()
        context = SimpleNamespace(bot=SimpleNamespace(send_message=send_message),
                                  user_data={})
        with mock.patch.object(Config, "BOT_USERNAME", "testbot"):
            asyncio.run(self.bot.handle_callback(update, context))
        update.callback_query.edit_message_text.assert_awaited_once()
        send_message.assert_not_awaited()


class FavoritesManagementTests(TempDbTestCase):
    def test_payload_fallback_favorites_render_with_buttons(self):
        job = self._job("h1")
        self.db.save_job_payload("h1", job)      # no posted_jobs row — Vercel case
        self.db.add_favorite(7, "h1")
        text, markup = self.bot._render_favorites(7, page=0)
        self.assertIn("Junior Dev", text)         # B19: JOIN no longer empty
        buttons = [b for row in markup.inline_keyboard for b in row]
        urls = [b.get("url") for b in buttons if b.get("url")]
        callbacks = [b.get("callback_data", "") for b in buttons]
        self.assertIn("https://e.com/1", urls)                    # «Открыть»
        self.assertTrue(any(c.startswith("fav_del:") for c in callbacks))
        self.assertTrue(any(c.startswith("fav_status:") for c in callbacks))

    def test_status_cycle_and_removal(self):
        self.assertEqual(self.bot._next_favorite_status("saved"), "applied")
        self.assertEqual(self.bot._next_favorite_status("applied"), "interview")
        self.assertEqual(self.bot._next_favorite_status("rejected"), "saved")
        job = self._job("h1")
        self.db.save_job_payload("h1", job)
        self.db.add_favorite(7, "h1")
        self.db.set_favorite_status(7, "h1", "offer")
        text, _ = self.bot._render_favorites(7, page=0)
        self.assertIn("оффер", text)
        self.db.remove_favorite(7, "h1")
        text, markup = self.bot._render_favorites(7, page=0)
        self.assertIsNone(markup)
        self.assertIn("пуст", text)

    def test_pagination(self):
        for i in range(7):
            h = f"h{i}"
            self.db.save_job_payload(h, self._job(h, url=f"https://e.com/{i}"))
            self.db.add_favorite(7, h)
        text, markup = self.bot._render_favorites(7, page=0)
        self.assertIn("стр. 1/2", text)
        callbacks = [b.get("callback_data", "") for row in markup.inline_keyboard for b in row]
        self.assertIn("fav_page:1", callbacks)


class DigestStructuredResultTests(TempDbTestCase):
    def test_no_matches_reason(self):
        self.db.recent_jobs_for_digest = lambda hours, limit: []
        res = asyncio.run(self.bot.send_personal_digest(7))
        self.assertEqual(res["matched"], 0)
        self.assertEqual(res["reason"], "no_matches")

    def test_send_failure_is_reported(self):
        self.db.recent_jobs_for_digest = lambda hours, limit: [self._job("h1")]
        self.bot.application = SimpleNamespace(
            bot=SimpleNamespace(send_message=mock.AsyncMock(side_effect=Exception("blocked")))
        )
        with mock.patch.object(channel_bot, "job_matches_profile", lambda job, s: True):
            res = asyncio.run(self.bot.send_personal_digest(7))
        self.assertEqual(res["matched"], 1)
        self.assertEqual(res["sent"], 0)
        self.assertIn("header_send_failed", res["reason"])

    def test_success_counts(self):
        self.db.recent_jobs_for_digest = lambda hours, limit: [self._job("h1")]
        self.bot.application = SimpleNamespace(
            bot=SimpleNamespace(send_message=mock.AsyncMock())
        )
        with mock.patch.object(channel_bot, "job_matches_profile", lambda job, s: True), \
             mock.patch.object(asyncio, "sleep", mock.AsyncMock()), \
             mock.patch.object(Config, "ENABLE_MARKDOWN_V2", False):
            res = asyncio.run(self.bot.send_personal_digest(7))
        self.assertEqual(res["matched"], 1)
        self.assertEqual(res["sent"], 1)
        self.assertEqual(res["failed"], 0)


class SetupValidationTests(unittest.TestCase):
    def test_abc_is_rejected_not_saved_as_zero(self):
        db = SimpleNamespace(save_user_settings=mock.MagicMock(),
                             log_event=mock.MagicMock())
        bot = JobBot(application=None, db=db)
        replies = []

        async def reply(text, **kwargs):
            replies.append(text)

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7),
            message=SimpleNamespace(text="abc", reply_text=reply),
        )
        context = SimpleNamespace(user_data={"setup_step": "salary"})
        asyncio.run(bot.handle_setup_text(update, context))
        db.save_user_settings.assert_not_called()           # B19: not silently 0
        self.assertTrue(any("цифрами" in r for r in replies))

    def test_number_is_accepted(self):
        db = SimpleNamespace(save_user_settings=mock.MagicMock(),
                             log_event=mock.MagicMock())
        bot = JobBot(application=None, db=db)

        async def reply(text, **kwargs):
            return None

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7),
            message=SimpleNamespace(text="$2500", reply_text=reply),
        )
        context = SimpleNamespace(user_data={"setup_step": "salary"})
        asyncio.run(bot.handle_setup_text(update, context))
        db.save_user_settings.assert_called_once_with(7, {"min_salary_filter": 2500})
        self.assertEqual(context.user_data["setup_step"], "skills")


class WizardPersistenceHelpersTests(unittest.TestCase):
    def test_step_lives_in_user_data(self):
        context = SimpleNamespace(user_data={})
        JobBot._set_wizard_step(context, "salary")
        self.assertEqual(context.user_data["setup_step"], "salary")
        self.assertEqual(JobBot._wizard_step(context), "salary")
        JobBot._set_wizard_step(context, None)
        self.assertIsNone(JobBot._wizard_step(context))


class ErrorHandlerTests(unittest.TestCase):
    def test_callback_query_always_answered(self):
        query = SimpleNamespace(answer=mock.AsyncMock())
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(error=RuntimeError("boom"))
        asyncio.run(_on_unhandled_error(update, context))
        query.answer.assert_awaited_once()

    def test_non_callback_update_does_not_crash(self):
        context = SimpleNamespace(error=RuntimeError("boom"))
        asyncio.run(_on_unhandled_error(SimpleNamespace(), context))  # no raise


class PostInitCommandsTests(unittest.TestCase):
    def test_public_and_admin_menus_published(self):
        set_my_commands = mock.AsyncMock()
        application = SimpleNamespace(bot=SimpleNamespace(set_my_commands=set_my_commands))
        with mock.patch.object(Config, "ADMIN_USER_ID", 42):
            asyncio.run(_post_init_bot(application))
        self.assertEqual(set_my_commands.await_count, 2)


class SeniorPromiseRemovedTests(unittest.TestCase):
    def test_no_senior_promise_in_source(self):
        with open(os.path.join(REPO_ROOT, "channel_bot.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("Senior-вакансии в match", src)
        self.assertNotIn("Senior + больше digests", src)


if __name__ == "__main__":
    unittest.main()
