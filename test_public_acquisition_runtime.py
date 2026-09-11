import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import public_acquisition_runtime as p7


class FakeDB:
    def __init__(self, job=None):
        self.job = job
        self.events = []
        self.settings = {
            "enabled_categories": [],
            "min_salary_filter": 0,
            "skills": "",
            "hide_senior": True,
            "digest_enabled": False,
            "onboarding_done": False,
            "premium_unlocked": False,
        }
        self.preview_jobs = []

    def log_event(self, user_id, name, props=None):
        self.events.append((user_id, name, props or {}))

    def get_job_payload(self, job_hash):
        return self.job if self.job and self.job.get("hash") == job_hash else None

    def maybe_unlock_premium(self, _user_id):
        return False

    def get_user_settings(self, _user_id):
        return dict(self.settings)

    def save_user_settings(self, _user_id, values):
        self.settings.update(values)
        return True

    def recent_jobs_for_digest(self, hours=36, limit=80):
        return list(self.preview_jobs)[:limit]


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SimpleNamespace()


class FakeQuery:
    def __init__(self, data, message):
        self.data = data
        self.message = message
        self.answers = []
        self.edits = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class PublicAcquisitionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def make_bot(self, job=None):
        bot = object.__new__(p7.JobBot)
        bot.db = FakeDB(job)
        bot.resume_sessions = {}
        bot.setup_steps = {}
        bot.pending_saved_searches = {}
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

    async def test_first_value_selection_is_capped_at_three(self):
        bot = self.make_bot()
        bot.db.preview_jobs = [
            {"hash": f"job{i}", "title": f"Job {i}"}
            for i in range(5)
        ]
        with patch.object(p7.core, "GROWTH_UTILS_AVAILABLE", False):
            _settings, jobs = bot._select_first_value_jobs(77)
        self.assertEqual([job["hash"] for job in jobs], ["job0", "job1", "job2"])
        self.assertEqual(len(jobs), p7.FIRST_VALUE_PREVIEW_MAX)

    async def test_quick_fresh_uses_three_job_preview_and_prompts_for_setup(self):
        bot = self.make_bot()
        bot._send_first_value_preview = AsyncMock(return_value=3)
        message = FakeMessage()
        query = FakeQuery("growth_quick_fresh", message)
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=77),
        )
        context = SimpleNamespace(bot=AsyncMock())

        await bot.handle_callback(update, context)

        bot._send_first_value_preview.assert_awaited_once_with(77, source="quick_fresh")
        self.assertEqual(query.answers[0][0], "Ищу 3 свежих совпадения")
        self.assertTrue(any("30 секунд" in text for text, _kwargs in message.calls))
        self.assertIn((77, "fresh_preview", {"count": 3}), bot.db.events)

    async def test_onboarding_completion_delivers_three_job_preview(self):
        bot = self.make_bot()
        bot.setup_steps[77] = "digest"
        bot._send_first_value_preview = AsyncMock(return_value=3)
        message = FakeMessage()
        query = FakeQuery("setup_digest_on", message)
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=77),
        )
        context = SimpleNamespace(bot=AsyncMock())

        await bot.handle_callback(update, context)

        self.assertTrue(bot.db.settings["onboarding_done"])
        self.assertTrue(bot.db.settings["digest_enabled"])
        bot._send_first_value_preview.assert_awaited_once_with(77, source="onboarding")
        self.assertIn("3 первых", query.edits[0][0])
        self.assertIn((77, "first_value_delivered", {"count": 3}), bot.db.events)
        self.assertTrue(any("Resume Match" in text for text, _kwargs in message.calls))


if __name__ == "__main__":
    unittest.main()
