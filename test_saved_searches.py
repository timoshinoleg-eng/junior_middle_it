import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import channel_bot as core
import production_bot as prod
from saved_searches import (
    normalize_skills,
    search_fingerprint,
    suggested_search_from_resume,
)


class SavedSearchHelperTests(unittest.TestCase):
    def test_normalization_is_stable_and_deduplicated(self):
        self.assertEqual(
            normalize_skills("Python, fastapi, PYTHON, postgres"),
            "python, fastapi, postgres",
        )
        a = search_fingerprint(["development"], "python, fastapi", 0, True)
        b = search_fingerprint(["development"], "fastapi, python", 0, True)
        self.assertEqual(a, b)
        self.assertNotEqual(a, "")

    def test_resume_suggestion_uses_real_matched_skills(self):
        spec = suggested_search_from_resume(
            {"category": "development", "title": "Python Developer"},
            ["python", "fastapi", "postgresql"],
        )
        self.assertEqual(spec["categories"], ["development"])
        self.assertEqual(spec["skills"], "python, fastapi, postgresql")
        self.assertIn("development", spec["name"])


class SavedSearchDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "REQUIRE_DURABLE_GROWTH": "false"},
            clear=False,
        )
        self.env.start()
        self.db = prod.DatabaseConnection(":memory:")

    def tearDown(self):
        self.db.close()
        self.env.stop()

    def test_create_is_idempotent_and_delete_is_user_scoped(self):
        spec = {
            "name": "Python remote",
            "categories": ["development"],
            "skills": "python, fastapi",
            "min_salary_filter": 0,
            "hide_senior": True,
        }
        first = self.db.create_saved_search(1, spec)
        second = self.db.create_saved_search(
            1,
            {**spec, "skills": "fastapi, python"},
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self.db.list_saved_searches(1)), 1)
        self.assertFalse(self.db.delete_saved_search(2, first.id))
        self.assertTrue(self.db.delete_saved_search(1, first.id))
        self.assertEqual(self.db.list_saved_searches(1), [])

    def test_saved_search_reuses_existing_profile_matcher(self):
        search = self.db.create_saved_search(
            1,
            {
                "categories": ["development"],
                "skills": "python, fastapi",
                "min_salary_filter": 0,
                "hide_senior": True,
            },
        )
        good = {
            "title": "Junior Python Developer",
            "description": "Build APIs with FastAPI",
            "category": "development",
            "level": "Junior",
            "tags": ["Python", "FastAPI"],
        }
        wrong = {
            "title": "QA Engineer",
            "description": "Manual testing",
            "category": "qa",
            "level": "Junior",
            "tags": [],
        }
        self.assertTrue(core.job_matches_profile(good, search.as_profile()))
        self.assertFalse(core.job_matches_profile(wrong, search.as_profile()))


class SavedSearchAlertTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "REQUIRE_DURABLE_GROWTH": "false"},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db = prod.DatabaseConnection(":memory:")
        self.addCleanup(self.db.close)
        self.app = SimpleNamespace(bot=AsyncMock())
        self.bot = prod.JobBot(self.app, self.db)
        self.db.save_user_settings(7, {"alerts_enabled": True, "onboarding_done": True})
        self.db.create_saved_search(
            7,
            {
                "name": "Python",
                "categories": ["development"],
                "skills": "python",
                "min_salary_filter": 0,
                "hide_senior": True,
            },
        )

    async def test_alerts_use_saved_search_not_broad_profile(self):
        jobs = [
            {
                "hash": "qa1",
                "title": "Junior QA",
                "company": "A",
                "description": "Manual QA testing",
                "category": "qa",
                "level": "Junior",
                "url": "https://example.com/qa",
                "tags": [],
            },
            {
                "hash": "py1",
                "title": "Junior Python Developer",
                "company": "B",
                "description": "Python backend API",
                "category": "development",
                "level": "Junior",
                "url": "https://example.com/python",
                "tags": ["Python"],
            },
        ]
        sent = await self.bot.push_realtime_alerts(jobs)
        self.assertEqual(sent, 1)
        payloads = [
            call.kwargs.get("text", "")
            for call in self.app.bot.send_message.await_args_list
        ]
        self.assertTrue(any("сохранённым поискам" in text for text in payloads))
        self.assertTrue(any("Python" in text for text in payloads))
        self.assertFalse(any("Junior QA" in text for text in payloads))


if __name__ == "__main__":
    unittest.main()
