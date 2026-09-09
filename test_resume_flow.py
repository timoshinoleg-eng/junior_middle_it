import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import production_bot as prod


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SimpleNamespace()


class FakeQuery:
    def __init__(self, data, message):
        self.data = data
        self.message = message
        self.answers = []
        self.reply_markup_edits = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_message_reply_markup(self, reply_markup=None):
        self.reply_markup_edits.append(reply_markup)


class ResumeFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        env = patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "REQUIRE_DURABLE_GROWTH": "false"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        self.db = prod.DatabaseConnection(":memory:")
        self.addCleanup(self.db.close)
        self.bot = prod.JobBot(SimpleNamespace(bot=AsyncMock()), self.db)
        self.job = {
            "hash": "job123",
            "title": "Junior Python Developer",
            "company": "Example",
            "description": "Python FastAPI PostgreSQL Docker REST API remote role",
            "category": "development",
            "level": "Junior",
            "salary": "$2000",
            "location": "Remote",
            "url": "https://example.com/jobs/123",
            "tags": ["Python", "FastAPI", "PostgreSQL", "Docker"],
        }
        self.db.save_job_payload("job123", self.job)

    async def test_resume_callback_starts_private_in_memory_session(self):
        message = FakeMessage()
        query = FakeQuery("resume_match:job123", message)
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=42))
        await self.bot.handle_callback(update, SimpleNamespace(bot=AsyncMock()))
        self.assertIn(42, self.bot.resume_sessions)
        self.assertIn("не записывается в БД", message.calls[0][0])

    async def test_resume_text_completes_match_and_is_not_logged(self):
        start_message = FakeMessage()
        query = FakeQuery("resume_match:job123", start_message)
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=42))
        await self.bot.handle_callback(update, SimpleNamespace(bot=AsyncMock()))

        resume_text = (
            "Junior Python backend developer. Python FastAPI PostgreSQL REST API. "
            "Built 3 services for 12000 users and used Git for code review. "
            "Worked on API integrations and database queries in two projects."
        )
        message = FakeMessage(resume_text)
        text_update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            message=message,
        )
        await self.bot.handle_setup_text(text_update, SimpleNamespace(bot=AsyncMock()))

        self.assertNotIn(42, self.bot.resume_sessions)
        self.assertIn("Resume Match:", message.calls[0][0])
        row = self.db.fetchone(
            "SELECT props FROM events WHERE user_id=? AND name='resume_match_completed' ORDER BY id DESC LIMIT 1",
            (42,),
        )
        self.assertIsNotNone(row)
        props = json.loads(row[0])
        self.assertNotIn(resume_text, row[0])
        self.assertEqual(props["hash"], "job123")
        self.assertIn("score", props)

    async def test_short_resume_is_rejected_without_ending_session(self):
        start_message = FakeMessage()
        query = FakeQuery("resume_match:job123", start_message)
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=42))
        await self.bot.handle_callback(update, SimpleNamespace(bot=AsyncMock()))

        message = FakeMessage("Python FastAPI")
        text_update = SimpleNamespace(effective_user=SimpleNamespace(id=42), message=message)
        await self.bot.handle_setup_text(text_update, SimpleNamespace(bot=AsyncMock()))
        self.assertIn(42, self.bot.resume_sessions)
        self.assertIn("Нужно чуть больше текста", message.calls[0][0])


if __name__ == "__main__":
    unittest.main()
