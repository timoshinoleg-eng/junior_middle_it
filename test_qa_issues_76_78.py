"""Independent regressions for production QA issues #76, #77, #78."""
import asyncio
import inspect
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import channel_bot
import copilot_webhook_runtime as webhook
import render_main
from api.copilot import is_pdf_route
from resume_matcher import analyze_resume_match, format_resume_match_report


class PdfRouteContractTests(unittest.TestCase):
    def test_public_pdf_url_is_rewritten_to_actual_function(self):
        cfg = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
        routes = [x for x in cfg["rewrites"] if x["source"] == "/api/copilot/pdf"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["destination"], "/api/copilot?__copilot_route=pdf")
        self.assertTrue(is_pdf_route(routes[0]["destination"]))

    def test_pdf_dispatch_accepts_only_intended_path_or_marker(self):
        self.assertTrue(is_pdf_route("/api/copilot/pdf"))
        self.assertTrue(is_pdf_route("/api/copilot?__copilot_route=pdf"))
        self.assertFalse(is_pdf_route("/api/copilot"))
        self.assertFalse(is_pdf_route("/api/copilot?__copilot_route=fit"))
        self.assertFalse(is_pdf_route("/api/other?__copilot_route=pdf"))


class PollingOwnershipTests(unittest.TestCase):
    def test_render_receives_no_update_polls(self):
        # Prevent a silent reintroduction of unconditional start_polling()
        # while Render is an outbound notification-only worker.
        src = inspect.getsource(channel_bot.main)
        self.assertIn("if _render_crawl_blocked():", src)
        self.assertIn("else:\n        await application.updater.start_polling()", src)
        with patch.dict(os.environ, {
            "RENDER_SERVICE_ID": "srv-qa", "VERCEL": "", "VERCEL_ENV": "",
        }):
            self.assertTrue(channel_bot._render_crawl_blocked())
            _, health = render_main.health_snapshot()
            self.assertFalse(health["receives_telegram_updates"])
            self.assertEqual(health["telegram_updates"], "disabled_vercel_webhook_canonical")

    def test_non_render_polling_stays_possible(self):
        with patch.dict(os.environ, {"RENDER_SERVICE_ID": "", "VERCEL": ""}):
            self.assertFalse(channel_bot._render_crawl_blocked())
            _, health = render_main.health_snapshot()
            self.assertTrue(health["receives_telegram_updates"])


class ResumeTextAndScoreTests(unittest.TestCase):
    def _update(self, text, *, forwarded=False):
        message = SimpleNamespace(
            text=text, document=None,
            forward_date="fake-forward-date" if forwarded else None,
        )
        return SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=42),
        )

    def test_unlabelled_english_resume_receives_guidance(self):
        bot = SimpleNamespace(setup_steps={}, resume_sessions={})
        txt = ("Software engineer with 3 years in backend systems. "
               "Built Python and FastAPI microservices, improved reliability, "
               "shipped features and wrote integration tests.")
        self.assertTrue(webhook._unmatched_resume_text(bot, self._update(txt)))

    def test_forwarded_vacancy_stays_in_vacancy_flow(self):
        bot = SimpleNamespace(setup_steps={}, resume_sessions={})
        txt = ("Hiring backend developer. Our company needs Java and SQL engineers "
               "with production experience. Join a remote international team.")
        self.assertFalse(webhook._unmatched_resume_text(
            bot, self._update(txt, forwarded=True)
        ))

    def test_active_resume_session_is_not_stolen(self):
        bot = SimpleNamespace(setup_steps={}, resume_sessions={42: {"job_hash": "x"}})
        txt = ("Software engineer with 3 years in backend systems. "
               "Built Python and FastAPI services, improved reliability.")
        self.assertFalse(webhook._unmatched_resume_text(bot, self._update(txt)))

    def test_one_generic_skill_with_zero_keywords_is_low_confidence(self):
        filler = " ".join(f"topic{i:02d}" for i in range(45))
        job = {"title": "Project Engineer", "description": filler + " Agile", "tags": []}
        result = analyze_resume_match("Agile Scrum practitioner.", job)
        self.assertEqual(result.keyword_overlap_pct, 0)
        self.assertLessEqual(result.score, 30)
        self.assertIn("Низкая уверенность", format_resume_match_report(result, job))

    def test_no_required_skills_gets_no_unsupported_bonus(self):
        job = {"title": "Opportunity", "description": "Looking for motivated team member"}
        report = analyze_resume_match("Python developer Docker Redis", job)
        self.assertLessEqual(report.score, 25)
        self.assertIn("Низкая уверенность", format_resume_match_report(report, job))


if __name__ == "__main__":
    unittest.main()
