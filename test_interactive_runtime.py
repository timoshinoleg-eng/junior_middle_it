import json
import os
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from docx import Document
from telegram.ext import filters as tg_filters

import interactive_runtime as ir


class FakeMessage:
    def __init__(self, text="", document=None):
        self.text = text
        self.document = document
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SimpleNamespace()


def build_docx_resume() -> tuple[bytes, str]:
    text = (
        "Junior Python backend developer with hands-on FastAPI and PostgreSQL experience. "
        "Built REST API integrations, Docker services and automated tests. Used Git, CI/CD, "
        "monitoring and database migrations in production projects with cross-functional teams. "
        "Worked on performance improvements and documented service contracts for other engineers."
    )
    doc = Document()
    doc.add_paragraph(text)
    out = BytesIO()
    doc.save(out)
    return out.getvalue(), text


class InteractiveRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        env = patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "REQUIRE_DURABLE_GROWTH": "false"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        self.db = ir.DatabaseConnection(":memory:")
        self.addCleanup(self.db.close)
        self.application = SimpleNamespace(bot=AsyncMock())
        self.bot = ir.JobBot(self.application, self.db)
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

    async def test_resume_deeplink_starts_private_session(self):
        message = FakeMessage()
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), message=message)
        context = SimpleNamespace(args=["resume_job123"], bot=AsyncMock())

        await self.bot.cmd_start(update, context)

        self.assertIn(42, self.bot.resume_sessions)
        self.assertIn("PDF или DOCX", message.calls[0][0])
        row = self.db.fetchone(
            "SELECT props FROM events WHERE user_id=? AND name='start' ORDER BY id DESC LIMIT 1",
            (42,),
        )
        self.assertEqual(json.loads(row[0])["payload"], "resume_job123")

    async def test_docx_resume_completes_without_persisting_content_or_filename(self):
        start_message = FakeMessage()
        start_update = SimpleNamespace(effective_user=SimpleNamespace(id=42), message=start_message)
        await self.bot.cmd_start(
            start_update,
            SimpleNamespace(args=["resume_job123"], bot=AsyncMock()),
        )

        payload, resume_text = build_docx_resume()
        document = SimpleNamespace(
            file_id="file-1",
            file_size=len(payload),
            file_name="oleg-private-resume.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        tg_file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(payload)))
        context = SimpleNamespace(bot=SimpleNamespace(get_file=AsyncMock(return_value=tg_file)))
        message = FakeMessage(document=document)
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), message=message)

        await self.bot.handle_setup_text(update, context)

        self.assertNotIn(42, self.bot.resume_sessions)
        self.assertIn("Resume Match:", message.calls[0][0])
        row = self.db.fetchone(
            "SELECT props FROM events WHERE user_id=? AND name='resume_match_completed' ORDER BY id DESC LIMIT 1",
            (42,),
        )
        props = json.loads(row[0])
        self.assertEqual(props["input_type"], "docx")
        self.assertNotIn(resume_text, row[0])
        self.assertNotIn("oleg-private-resume.docx", row[0])
        doc_event = self.db.fetchone(
            "SELECT props FROM events WHERE user_id=? AND name='resume_match_document_completed' ORDER BY id DESC LIMIT 1",
            (42,),
        )
        self.assertIsNotNone(doc_event)

    async def test_oversized_document_does_not_end_session_or_download(self):
        self.bot.resume_sessions[42] = {"job_hash": "job123", "job": self.job}
        document = SimpleNamespace(
            file_id="file-1",
            file_size=6 * 1024 * 1024,
            file_name="resume.pdf",
            mime_type="application/pdf",
        )
        context = SimpleNamespace(bot=SimpleNamespace(get_file=AsyncMock()))
        message = FakeMessage(document=document)
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), message=message)

        await self.bot.handle_setup_text(update, context)

        self.assertIn(42, self.bot.resume_sessions)
        context.bot.get_file.assert_not_awaited()
        self.assertIn("максимум 5 МБ", message.calls[0][0])

    async def test_main_temporarily_adds_document_filter_and_restores_text_filter(self):
        original = tg_filters.TEXT

        async def fake_core_main():
            self.assertIsNot(tg_filters.TEXT, original)

        with patch.object(ir.core, "main", side_effect=fake_core_main) as mocked:
            await ir.main()
        mocked.assert_awaited_once()
        self.assertIs(tg_filters.TEXT, original)


class UtilityMetricsTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "REQUIRE_DURABLE_GROWTH": "false"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        self.db = ir.DatabaseConnection(":memory:")
        self.addCleanup(self.db.close)

    def test_utility_stats_are_unique_user_funnel_metrics(self):
        self.db.log_event(1, "resume_match_started", {"hash": "a"})
        self.db.log_event(1, "resume_match_started", {"hash": "b"})
        self.db.log_event(1, "resume_match_completed", {"score": 80})
        self.db.log_event(1, "resume_match_document_completed", {"input_type": "pdf"})
        self.db.log_event(1, "saved_search_created", {"source": "resume_match"})
        self.db.log_event(1, "realtime_alert_sent", {"count": 1})
        self.db.log_event(2, "resume_match_started", {"hash": "c"})
        self.db.log_event(2, "resume_match_completed", {"score": 60})
        self.db.log_event(2, "saved_search_created", {"source": "profile"})
        self.db.log_event(3, "start", {"payload": "magnet_picks_2026w37"})
        self.db.log_event(None, "salary_magnet_posted", {"campaign": "magnet_salary_2026w37"})
        self.db.log_event(None, "weekly_picks_posted", {"campaign": "magnet_picks_2026w37"})
        self.db.create_saved_search(
            1,
            {"categories": ["development"], "skills": "python, fastapi", "name": "Python"},
        )

        stats = self.db.utility_stats(7)

        self.assertEqual(stats["resume_started_users"], 2)
        self.assertEqual(stats["resume_completed_users"], 2)
        self.assertEqual(stats["resume_completion_pct"], 100.0)
        self.assertEqual(stats["resume_document_users"], 1)
        self.assertEqual(stats["resume_avg_score"], 70.0)
        self.assertEqual(stats["saved_search_users"], 2)
        self.assertEqual(stats["resume_to_search_pct"], 50.0)
        self.assertEqual(stats["active_saved_searches"], 1)
        self.assertEqual(stats["active_saved_search_users"], 1)
        self.assertEqual(stats["alert_users"], 1)
        self.assertEqual(stats["magnet_start_users"], 1)
        self.assertEqual(stats["salary_posts"], 1)
        self.assertEqual(stats["weekly_picks_posts"], 1)


if __name__ == "__main__":
    unittest.main()
