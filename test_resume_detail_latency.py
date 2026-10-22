"""Regression checks for detailed Resume Match and user-visible response ordering."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import localized_product_runtime as localized
import localized_product_runtime_v2 as localized_v2
from resume_matcher import analyze_resume_match, format_resume_match_report


JOB = {
    "hash": "a" * 40,
    "title": "Junior Python Backend Developer",
    "company": "Sample Labs",
    "description": "Build Python and FastAPI APIs, maintain PostgreSQL and Docker.",
    "tags": ["Python", "FastAPI", "Docker", "PostgreSQL"],
}


class ResumeDetailTests(unittest.TestCase):
    def setUp(self):
        self.report = analyze_resume_match(
            "Python and FastAPI backend engineer. I built APIs with PostgreSQL in my own project.",
            JOB,
        )

    def test_detailed_report_explains_evidence_gaps_and_actions(self):
        message = format_resume_match_report(self.report, JOB)
        for section in (
            "1. Что удалось проверить",
            "2. Что мешает отклику",
            "3. Как улучшить резюме",
            "4. Что важно понимать",
            "Подтверждено текстом:",
            "Навыки вакансии:",
            "Пересечение ключевых слов:",
            "Текст резюме не сохранялся.",
        ):
            self.assertIn(section, message)
        self.assertGreater(len(message), 900)
        self.assertLessEqual(len(message), 3900)
        self.assertIn("[твой измеримый эффект]", message)
        self.assertNotIn("100% соответствует", message)

    def test_telegram_text_path_uses_same_detailed_report_as_pdf(self):
        bot = object.__new__(localized.JobBot)
        bot._lang = lambda _uid: "ru"
        message = bot._format_resume_report(42, self.report, JOB)
        self.assertEqual(message, format_resume_match_report(self.report, JOB))

    def test_low_evidence_is_clearly_disclaimed(self):
        score = analyze_resume_match("Agile.", {
            "title": "Engineer", "description": "Agile", "tags": [],
        })
        message = format_resume_match_report(score, {"title": "Engineer"})
        self.assertIn("Низкая уверенность", message)
        self.assertIn("не вероятность найма", message)


class ResponseLatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_match_button_ack_precedes_remote_queries(self):
        order = []
        bot = object.__new__(localized_v2.JobBot)

        class DB:
            def get_job_payload(self, job_hash):
                order.append("job_read")
                return dict(JOB)

            def log_event(self, *_args):
                order.append("telemetry")

        bot.db = DB()
        bot.resume_sessions = {}
        bot._lang = lambda uid: order.append("language_read") or "ru"
        bot._resume_prompt_for_user = lambda uid, job: "Send your resume"

        async def ack(*_args, **_kwargs):
            order.append("ack")

        async def reply(*_args, **_kwargs):
            order.append("prompt")

        callback = SimpleNamespace(
            data="resume_match:" + JOB["hash"],
            answer=AsyncMock(side_effect=ack),
            message=SimpleNamespace(reply_text=AsyncMock(side_effect=reply)),
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42), callback_query=callback
        )
        await bot.handle_callback(update, SimpleNamespace())
        self.assertEqual(order[0], "ack")
        self.assertLess(order.index("prompt"), order.index("telemetry"))
        self.assertEqual(callback.answer.await_count, 1)

    async def test_resume_text_reply_precedes_remote_telemetry(self):
        order = []
        bot = object.__new__(localized.JobBot)
        bot._lang = lambda _uid: "ru"
        bot.resume_sessions = {42: {"job": dict(JOB), "job_hash": JOB["hash"]}}
        bot.pending_saved_searches = {}
        bot.db = SimpleNamespace(log_event=lambda *_args: order.append("telemetry"))

        async def reply(*_args, **_kwargs):
            order.append("reply")

        update = SimpleNamespace(message=SimpleNamespace(
            reply_text=AsyncMock(side_effect=reply)
        ))
        await bot._complete_resume(
            update, 42, "Python FastAPI PostgreSQL backend engineering projects", "text"
        )
        self.assertEqual(order[:2], ["reply", "telemetry"])


if __name__ == "__main__":
    unittest.main()
