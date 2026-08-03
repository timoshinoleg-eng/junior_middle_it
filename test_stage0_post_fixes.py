"""Stage-0 post quality fixes: HTML-entity stripping and placeholder hiding."""
import os
import unittest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("CHANNEL_ID", "@test_channel")

import channel_bot


TWILIO_ENTITY_HTML = (
    '&lt;div class=&quot;content-intro&quot;&gt;&lt;p&gt;&lt;strong&gt;Who we are&amp;nbsp;'
    '&lt;/strong&gt;&lt;/p&gt; &lt;p&gt;At Twilio, we\u2019re shaping the future of '
    'communications.&lt;/p&gt;&lt;/div&gt;'
)


class StripHtmlTests(unittest.TestCase):
    def test_plain_html(self):
        out = channel_bot.strip_html("<h3>Role</h3><p>Hello <b>world</b></p>")
        self.assertEqual(out, "Role Hello world")

    def test_entity_encoded_html(self):
        out = channel_bot.strip_html(TWILIO_ENTITY_HTML)
        self.assertNotIn("&lt;", out)
        self.assertNotIn("<div", out)
        self.assertIn("Who we are", out)
        self.assertIn("shaping the future of communications", out)

    def test_double_encoded(self):
        double = TWILIO_ENTITY_HTML.replace("&", "&amp;")
        out = channel_bot.strip_html(double)
        self.assertNotIn("&lt;", out)
        self.assertIn("Who we are", out)

    def test_entities_decoded_in_plain_text(self):
        out = channel_bot.strip_html("A &amp; B &lt; C")
        self.assertEqual(out, "A & B < C")

    def test_empty(self):
        self.assertEqual(channel_bot.strip_html(""), "")
        self.assertEqual(channel_bot.strip_html(None), "")


class ExtractDescriptionTests(unittest.TestCase):
    def test_entity_html_cleaned(self):
        job = {"description": TWILIO_ENTITY_HTML}
        desc = channel_bot.extract_description(job)
        self.assertNotIn("&lt;", desc)
        self.assertTrue(desc.startswith("Who we are"))

    def test_empty_returns_empty_not_placeholder(self):
        self.assertEqual(channel_bot.extract_description({"description": ""}), "")
        self.assertEqual(channel_bot.extract_description({}), "")

    def test_truncation(self):
        job = {"description": "word " * 200}
        desc = channel_bot.extract_description(job)
        self.assertTrue(desc.endswith("..."))
        self.assertLessEqual(len(desc), 360)


class LegacyFormatterPlaceholderTests(unittest.TestCase):
    def _job(self, **overrides):
        job = {
            "title": "Associate Application Engineer",
            "company": "twilio",
            "level": "Junior",
            "category": "development",
            "location": "Remote - India",
            "url": "https://boards.greenhouse.io/twilio/jobs/1",
            "source": "Greenhouse:twilio",
            "salary": "Не указана",
            "description": TWILIO_ENTITY_HTML,
        }
        job.update(overrides)
        return job

    def test_no_placeholder_lines(self):
        msg = channel_bot.format_job_message_legacy(self._job())
        self.assertNotIn("Не указана", msg)
        self.assertNotIn("Недавно", msg)
        self.assertNotIn("Не указаны", msg)

    def test_html_cleaned_in_output(self):
        msg = channel_bot.format_job_message_legacy(self._job())
        self.assertNotIn("content-intro", msg)
        self.assertIn("Who we are", msg)

    def test_empty_description_hides_block(self):
        msg = channel_bot.format_job_message_legacy(self._job(description=""))
        self.assertNotIn("Описание:", msg)

    def test_known_salary_still_shown(self):
        msg = channel_bot.format_job_message_legacy(self._job(salary="$3,000-$5,000"))
        self.assertIn("💵 <b>Зарплата:</b> $3,000-$5,000", msg)

    def test_known_date_and_employment_still_shown(self):
        msg = channel_bot.format_job_message_legacy(
            self._job(published="2026-07-31T10:00:00Z", employment_type="full-time")
        )
        self.assertIn("📅 31 июл", msg)
        self.assertIn("⏰ Полная", msg)

    def test_title_escaped_once(self):
        msg = channel_bot.format_job_message_legacy(self._job(title="Dev & Ops <Senior>"))
        self.assertIn("Dev &amp; Ops &lt;Senior&gt;", msg)
        self.assertNotIn("&amp;amp;", msg)


if __name__ == "__main__":
    unittest.main()
