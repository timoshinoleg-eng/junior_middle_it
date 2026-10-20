import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import copilot_webhook_runtime as copilot_runtime
import durable_product_state as durable
from message_formatter import build_copilot_url, copilot_enabled


JOB = {
    "hash": "a" * 40,
    "title": "Junior Python Developer",
    "company": "Acme",
    "description": "Python, FastAPI, PostgreSQL. Опыт 1-2 года. Вопросы по вакансии пишите @hr_ivan",
    "url": "https://example.com/jobs/1",
    "source": "TG:@junior_dev",
}


class FakeDb:
    def __init__(self, job=None):
        self.job = job
        self.events = []
        self.saved = []
        self.settings = {"skills": "python, fastapi", "enabled_categories": ["development"]}

    def get_job_payload(self, job_hash):
        if self.job and str(job_hash) == str(self.job.get("hash")):
            return dict(self.job)
        return None

    def get_user_settings(self, user_id):
        return dict(self.settings)

    def save_user_settings(self, user_id, values):
        self.settings.update(values or {})

    def log_event(self, user_id, name, props=None):
        self.events.append((name, dict(props or {})))

    def create_saved_search(self, user_id, spec):
        self.saved.append(spec)
        return SimpleNamespace(id=len(self.saved))


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeQuery:
    def __init__(self, message):
        self.message = message
        self.data = ""
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)


class FakeUpdate:
    def __init__(self, message=None, query=None):
        self.effective_user = SimpleNamespace(id=4242)
        self.message = message
        self.callback_query = query


class FakeBot:
    def __init__(self, db):
        self.db = db


class CopilotRuntimeTests(unittest.TestCase):
    def test_module_installs_the_copilot_entry_points(self):
        self.assertTrue(hasattr(durable.JobBot, "cmd_copilot"))
        self.assertIsNot(durable.JobBot.cmd_start, copilot_runtime._original_cmd_start)
        self.assertIsNot(durable.JobBot.handle_callback, copilot_runtime._original_handle_callback)

    def test_deep_link_delivers_the_analysis(self):
        db = FakeDb(JOB)
        message = FakeMessage()
        update = FakeUpdate(message=message)
        context = SimpleNamespace(args=["copilot_" + JOB["hash"]])
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False):
            import asyncio

            asyncio.run(copilot_runtime._copilot_cmd_start(FakeBot(db), update, context))
        self.assertTrue(message.replies)
        text = message.replies[0][0]
        self.assertIn("fit", text.lower())
        self.assertTrue(any(name == "copilot_fit_viewed" for name, _props in db.events))

    def test_deep_link_is_ignored_when_the_feature_is_off(self):
        from unittest.mock import AsyncMock

        db = FakeDb(JOB)
        message = FakeMessage()
        update = FakeUpdate(message=message)
        context = SimpleNamespace(args=["copilot_" + JOB["hash"]])
        original = AsyncMock()
        with patch.dict(os.environ, {"COPILOT_ENABLED": "false"}, clear=False), patch.object(
            copilot_runtime, "_original_cmd_start", new=original
        ):
            import asyncio

            asyncio.run(copilot_runtime._copilot_cmd_start(FakeBot(db), update, context))
        self.assertEqual(message.replies, [])
        original.assert_awaited_once()

    def test_stale_card_is_reported_without_a_crash(self):
        db = FakeDb(None)
        message = FakeMessage()
        update = FakeUpdate(message=message)
        context = SimpleNamespace(args=["copilot_unknown"])
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False):
            import asyncio

            asyncio.run(copilot_runtime._copilot_cmd_start(FakeBot(db), update, context))
        self.assertTrue(message.replies)
        self.assertIn("устарела", message.replies[0][0])

    def test_hr_callback_sends_a_prefilled_link(self):
        import asyncio

        db = FakeDb(JOB)
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_hr:" + JOB["hash"]
        update = FakeUpdate(message=message, query=query)
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False):
            asyncio.run(copilot_runtime._copilot_handle_callback(FakeBot(db), update, SimpleNamespace()))
        self.assertTrue(message.replies)
        self.assertIn("t.me/hr_ivan", message.replies[0][0])
        self.assertTrue(any(name == "copilot_hr_link_clicked" for name, _props in db.events))

    def test_save_callback_creates_a_saved_search(self):
        import asyncio

        db = FakeDb(JOB)
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_save:" + JOB["hash"]
        update = FakeUpdate(message=message, query=query)
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False):
            asyncio.run(copilot_runtime._copilot_handle_callback(FakeBot(db), update, SimpleNamespace()))
        self.assertEqual(len(db.saved), 1)
        self.assertTrue(any(name == "copilot_search_saved" for name, _props in db.events))

    def test_unknown_copilot_callback_is_acknowledged(self):
        import asyncio

        db = FakeDb(JOB)
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_unknown"
        update = FakeUpdate(message=message, query=query)
        asyncio.run(copilot_runtime._copilot_handle_callback(FakeBot(db), update, SimpleNamespace()))
        self.assertTrue(query.answers)

    def test_events_never_carry_resume_content(self):
        import asyncio

        db = FakeDb(JOB)
        message = FakeMessage()
        update = FakeUpdate(message=message)
        context = SimpleNamespace(args=["copilot_" + JOB["hash"]])
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False):
            asyncio.run(copilot_runtime._copilot_cmd_start(FakeBot(db), update, context))
        for _name, props in db.events:
            for key in props:
                self.assertNotIn("resume", key.lower())
                self.assertNotIn("cv", key.lower())


class ChannelButtonTests(unittest.TestCase):
    def test_copilot_url_uses_the_deep_link(self):
        url = build_copilot_url(JOB, bot_username="junior_jobs_channel_bot")
        self.assertEqual(url, f"https://t.me/junior_jobs_channel_bot?start=copilot_{JOB['hash']}")

    def test_copilot_url_without_bot_or_hash_is_empty(self):
        self.assertEqual(build_copilot_url(JOB, bot_username=""), "")
        self.assertEqual(build_copilot_url({}, bot_username="bot"), "")

    def test_button_is_hidden_while_the_feature_is_off(self):
        with patch.dict(os.environ, {"COPILOT_ENABLED": "false"}, clear=False):
            self.assertFalse(copilot_enabled())
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False):
            self.assertTrue(copilot_enabled())


if __name__ == "__main__":
    unittest.main()
