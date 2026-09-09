import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import content_runtime
from content_magnets import (
    append_campaign_cta,
    build_weekly_picks_report,
    campaign_payload,
    select_weekly_picks,
)


class ContentMagnetTests(unittest.TestCase):
    def setUp(self):
        self.moment = datetime(2026, 9, 9, tzinfo=timezone.utc)
        self.jobs = [
            {
                "hash": "1",
                "title": "Junior Python Developer",
                "company": "Alpha",
                "category": "development",
                "level": "Junior",
                "salary": "$2000-3000",
                "location": "Remote worldwide",
                "url": "https://example.com/1",
                "tags": ["Python", "FastAPI"],
            },
            {
                "hash": "2",
                "title": "Junior QA Engineer",
                "company": "Beta",
                "category": "qa",
                "level": "Junior",
                "salary": "Не указана",
                "location": "Remote Europe",
                "url": "https://example.com/2",
                "tags": ["Playwright"],
            },
            {
                "hash": "3",
                "title": "Middle Data Analyst",
                "company": "Gamma",
                "category": "data",
                "level": "Middle",
                "salary": "$3500",
                "location": "Remote",
                "url": "https://example.com/3",
                "tags": ["SQL", "Python"],
            },
        ]

    def test_campaign_payload_is_stable_and_telegram_safe(self):
        payload = campaign_payload("weekly picks!", self.moment)
        self.assertEqual(payload, "magnet_weekly_picks_2026w37")
        self.assertLessEqual(len(payload), 64)

    def test_weekly_report_contains_attributable_cta(self):
        text, payload = build_weekly_picks_report(
            self.jobs,
            "junior_middle_bot",
            moment=self.moment,
            limit=3,
        )
        self.assertIn("Junior Python Developer", text)
        self.assertIn("https://example.com/1", text)
        self.assertIn(
            f"https://t.me/junior_middle_bot?start={payload}",
            text,
        )
        self.assertEqual(payload, "magnet_picks_2026w37")

    def test_weekly_picks_diversify_companies(self):
        duplicate_company = dict(self.jobs[0])
        duplicate_company.update({"hash": "4", "title": "Python Backend 2", "url": "https://example.com/4"})
        picks = select_weekly_picks([duplicate_company, *self.jobs], limit=3)
        companies = [job["company"] for job in picks]
        self.assertEqual(len(companies), len(set(companies)))

    def test_salary_cta_uses_separate_campaign(self):
        text, payload = append_campaign_cta(
            "Salary pulse",
            "junior_middle_bot",
            "salary",
            self.moment,
        )
        self.assertEqual(payload, "magnet_salary_2026w37")
        self.assertIn(f"?start={payload}", text)


class FakeDB:
    def __init__(self, jobs):
        self.jobs = jobs
        self.events = []

    def recent_jobs_for_magnets(self, hours=168, limit=250):
        return [dict(job) for job in self.jobs[:limit]]

    def log_event(self, user_id, name, props=None):
        self.events.append((user_id, name, props or {}))


class ContentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_weekly_hook_posts_salary_and_curated_magnet(self):
        jobs = [
            {
                "hash": "a",
                "title": "Junior Python Developer",
                "company": "Alpha",
                "category": "development",
                "level": "Junior",
                "salary": "$24000-36000",
                "salary_min_usd": 24000,
                "location": "Remote worldwide",
                "url": "https://example.com/a",
                "tags": ["Python"],
            },
            {
                "hash": "b",
                "title": "Junior QA Engineer",
                "company": "Beta",
                "category": "qa",
                "level": "Junior",
                "salary": "$20000",
                "salary_min_usd": 20000,
                "location": "Remote",
                "url": "https://example.com/b",
                "tags": ["Playwright"],
            },
        ]
        db = FakeDB(jobs)
        app = SimpleNamespace(bot=AsyncMock())
        bot = content_runtime.JobBot(app, db)

        import channel_bot as core
        old_channel = core.Config.CHANNEL_ID
        old_username = core.Config.BOT_USERNAME
        core.Config.CHANNEL_ID = "@junior_test"
        core.Config.BOT_USERNAME = "junior_test_bot"
        try:
            ok = await bot.post_weekly_salary_magnet()
        finally:
            core.Config.CHANNEL_ID = old_channel
            core.Config.BOT_USERNAME = old_username

        self.assertTrue(ok)
        self.assertEqual(app.bot.send_message.await_count, 2)
        names = [event[1] for event in db.events]
        self.assertIn("salary_magnet_posted", names)
        self.assertIn("weekly_picks_posted", names)
        sent_text = "\n".join(
            str(call.kwargs.get("text", "")) for call in app.bot.send_message.await_args_list
        )
        self.assertIn("?start=magnet_salary_", sent_text)
        self.assertIn("?start=magnet_picks_", sent_text)


if __name__ == "__main__":
    unittest.main()
