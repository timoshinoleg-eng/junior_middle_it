"""Editorial channel layout tests: factual, safe on mobile, correctly attributed."""
from datetime import datetime, timezone
from unittest import TestCase

from message_formatter import JobMessageFormatter


class EditorialCardTests(TestCase):
    def setUp(self):
        self.job = {
            "hash": "example123", "title": "Junior Python Developer",
            "company": "Example Labs", "level": "Junior",
            "primary_track": "development", "category": "development",
            "salary": "3,000–4,000 USD/month",
            "location": "Remote — United Kingdom",
            "remote_evidence": "location: Remote — United Kingdom",
            "remote_scope": "country_restricted",
            "geo_restriction": "United Kingdom",
            "source_published_at": datetime.now(timezone.utc).isoformat(),
            "description": "<p>Build APIs with Python and write tests for a distributed application.</p>",
            "tags": ["Python", "SQL", "python", "FastAPI"],
            "employment_type": "Full-time",
            "source": "RemoteJobs.org",
            "url": "https://remotejobs.org/jobs/example"
        }

    def test_mobile_card_structure_and_bounded_length(self):
        msg = JobMessageFormatter().format_job(self.job, view_mode="compact", bot_username="junior_jobs_channel_bot")
        parts = msg.text.split("\n")
        self.assertIn("JUNIOR", parts[0])
        self.assertIn("*О вакансии*", msg.text)
        self.assertIn("Источник: RemoteJobs", msg.text)
        self.assertIn("Full", msg.text)
        self.assertIn("Python", msg.text)
        self.assertIn("🧰 Python · SQL · FastAPI", msg.text)
        self.assertLess(len(msg.text), 1200)
        self.assertNotIn("[🔗 Откликнуться]", msg.text)
        self.assertEqual(msg.parse_mode, "MarkdownV2")
        buttons = [b for row in msg.reply_markup["inline_keyboard"] for b in row]
        self.assertTrue(all("url" in b and "callback_data" not in b for b in buttons))

    def test_unverified_fields_are_never_invented(self):
        job = dict(self.job, description="", salary="", employment_type="", tags=[], source="Greenhouse:demo")
        msg = JobMessageFormatter().format_job(job, "compact")
        self.assertIn("Зарплата не указана", msg.text)
        self.assertNotIn("Договорная", msg.text)
        self.assertNotIn("*О вакансии*", msg.text)
        self.assertNotIn("Full-time", msg.text)
        self.assertIn("Greenhouse", msg.text)

    def test_special_source_attribution_is_embedded(self):
        msg = JobMessageFormatter().format_job(dict(self.job, source="Remote Landers"), "compact")
        self.assertIn("[Job data provided by Remote Landers](https://remotelanders.com)", msg.text)
        self.assertEqual(msg.text.count("Remote Landers"), 1)

    def test_malformed_tags_and_user_controlled_markdown_are_safe(self):
        job = dict(
            self.job, title="Junior *Developer* [SQL]",
            company="Acme (R&D)", source="Greenhouse:[x]",
            tags="this is not a list",
            description="<script>alert('x')</script><strong>Test roles [Python]</strong> &amp; handle cloud services."
        )
        msg = JobMessageFormatter().format_job(job, "compact")
        self.assertIn(r"\*Developer\*", msg.text)
        self.assertIn(r"\[SQL\]", msg.text)
        self.assertNotIn("<script>", msg.text)
        self.assertNotIn("alert(", msg.text)
        self.assertNotIn("🧰", msg.text)
        self.assertIn("cloud services", msg.text)
        self.assertIn(r"Greenhouse:\[x\]", msg.text)

    def test_long_description_is_not_sent_unbounded(self):
        job = dict(self.job, description="Develop APIs and test integrations. " * 300)
        msg = JobMessageFormatter().format_job(job, "full")
        self.assertLess(len(msg.text), 4096)
        self.assertIn("…", msg.text)

    def test_geo_restriction_is_not_worldwide(self):
        msg = JobMessageFormatter().format_job(self.job, "compact")
        self.assertIn("Remote · United Kingdom", msg.text.replace("\\", ""))
        self.assertNotIn("Worldwide", msg.text)
