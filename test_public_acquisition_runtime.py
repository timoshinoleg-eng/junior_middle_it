import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import public_acquisition_runtime as p7


class FakeDB:
    def __init__(self, job=None):
        self.job = job
        self.events = []

    def log_event(self, user_id, name, props=None):
        self.events.append((user_id, name, props or {}))

    def get_job_payload(self, job_hash):
        return self.job if self.job and self.job.get("hash") == job_hash else None

    def maybe_unlock_premium(self, _user_id):
        return False


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SimpleNamespace()


class PublicAcquisitionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def make_bot(self, job=None):
        bot = object.__new__(p7.JobBot)
        bot.db = FakeDB(job)
        bot.resume_sessions = {}
        return bot

    @staticmethod
    def make_update(message):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=77),
            message=message,
        )

    async def test_web_home_is_attributed_after_canonical_start(self):
        bot = self.make_bot()
        message = FakeMessage()
        update = self.make_update(message)
        context = SimpleNamespace(args=["web_home"])
        with patch.object(p7.referral.JobBot, "cmd_start", new=AsyncMock()) as parent:
            await bot.cmd_start(update, context)
        parent.assert_awaited_once_with(update, context)
        self.assertIn((77, "web_landing_start", {"surface": "public_site"}), bot.db.events)

    async def test_web_job_continues_specific_vacancy(self):
        job = {
            "hash": "abc123",
            "title": "Junior Python Developer",
            "company": "Example",
            "location": "Remote",
            "salary": "$1800",
            "url": "https://example.com/jobs/1",
        }
        bot = self.make_bot(job)
        message = FakeMessage()
        update = self.make_update(message)
        context = SimpleNamespace(args=["web_abc123"])
        with patch.object(p7.referral.JobBot, "cmd_start", new=AsyncMock()):
            await bot.cmd_start(update, context)
        self.assertEqual(bot.db.events[0][1], "web_job_start")
        text, kwargs = message.calls[-1]
        self.assertIn("Junior Python Developer", text)
        labels = [button.text for row in kwargs["reply_markup"].inline_keyboard for button in row]
        self.assertIn("🚀 Откликнуться", labels)
        self.assertIn("📄 Проверить резюме", labels)

    async def test_resume_deep_link_is_dispatched_once_to_p5_handler(self):
        job = {
            "hash": "abc123",
            "title": "Junior Python Developer",
            "company": "Example",
            "url": "https://example.com/jobs/1",
        }
        bot = self.make_bot(job)
        message = FakeMessage()
        update = self.make_update(message)
        context = SimpleNamespace(args=["resume_abc123"], bot=AsyncMock())

        await bot.cmd_start(update, context)

        self.assertEqual(bot.resume_sessions[77]["job_hash"], "abc123")
        self.assertEqual(len(message.calls), 1)
        prompt = message.calls[0][0]
        self.assertIn("Resume Match", prompt)
        self.assertIn("PDF или DOCX", prompt)
        event_names = [name for _uid, name, _props in bot.db.events]
        self.assertEqual(event_names.count("web_resume_start"), 1)
        self.assertEqual(event_names.count("start"), 1)
        self.assertEqual(event_names.count("resume_attributed_start"), 1)
        self.assertEqual(event_names.count("resume_match_started"), 1)
        self.assertEqual(
            bot.db.events[0],
            (77, "web_resume_start", {"surface": "public_site", "hash": "abc123"}),
        )

    async def test_stale_resume_deep_link_replies_once_and_does_not_create_session(self):
        bot = self.make_bot()
        message = FakeMessage()
        update = self.make_update(message)
        context = SimpleNamespace(args=["resume_missing"], bot=AsyncMock())

        await bot.cmd_start(update, context)

        self.assertNotIn(77, bot.resume_sessions)
        self.assertEqual(len(message.calls), 1)
        self.assertIn("устарела", message.calls[0][0])
        event_names = [name for _uid, name, _props in bot.db.events]
        self.assertEqual(event_names.count("web_resume_start"), 1)
        self.assertEqual(event_names.count("start"), 1)
        self.assertEqual(event_names.count("resume_attributed_start"), 1)
        self.assertEqual(event_names.count("resume_match_started"), 0)

    async def test_invalid_apply_url_is_not_exposed(self):
        job = {
            "hash": "abc123",
            "title": "Junior Python Developer",
            "company": "Example",
            "url": "javascript:alert(1)",
        }
        bot = self.make_bot(job)
        message = FakeMessage()
        update = self.make_update(message)
        context = SimpleNamespace(args=["web_abc123"])
        with patch.object(p7.referral.JobBot, "cmd_start", new=AsyncMock()):
            await bot.cmd_start(update, context)
        labels = [
            button.text
            for row in message.calls[-1][1]["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertNotIn("🚀 Откликнуться", labels)


if __name__ == "__main__":
    unittest.main()
