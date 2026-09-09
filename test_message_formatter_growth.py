import unittest
from urllib.parse import parse_qs, urlparse

from message_formatter import JobMessageFormatter, build_telegram_share_url


class TelegramShareGrowthTests(unittest.TestCase):
    def setUp(self):
        self.job = {
            "title": "Junior Python Developer",
            "company": "Example Labs",
            "level": "Junior",
            "category": "development",
            "salary": "$2000-3000",
            "location": "Remote",
            "description": "Build APIs with Python and FastAPI",
            "tags": ["Python", "FastAPI"],
            "source": "Test",
            "url": "https://example.com/jobs/123?utm_source=board",
            "hash": "abc123def4567890",
        }

    def test_share_button_uses_official_telegram_url(self):
        formatter = JobMessageFormatter()
        keyboard = formatter.create_inline_keyboard(
            self.job, bot_username="junior_middle_bot"
        )
        share = next(
            button
            for row in keyboard["inline_keyboard"]
            for button in row
            if button.get("text") == "📤 Поделиться"
        )
        self.assertNotIn("switch_inline_query", share)
        self.assertTrue(share["url"].startswith("https://t.me/share/url?"))

        params = parse_qs(urlparse(share["url"]).query)
        self.assertEqual(params["url"][0], self.job["url"])
        shared_text = params["text"][0]
        self.assertIn("Junior Python Developer", shared_text)
        self.assertIn("Example Labs", shared_text)
        self.assertIn(
            "https://t.me/junior_middle_bot?start=share_abc123def4567890",
            shared_text,
        )

    def test_share_url_works_without_bot_username(self):
        share_url = build_telegram_share_url(self.job)
        params = parse_qs(urlparse(share_url).query)
        self.assertEqual(params["url"][0], self.job["url"])
        self.assertIn("Junior Python Developer", params["text"][0])
        self.assertNotIn("t.me/?start", params["text"][0])

    def test_share_falls_back_to_bot_deeplink_without_job_url(self):
        job = dict(self.job)
        job["url"] = ""
        share_url = build_telegram_share_url(job, "@junior_middle_bot")
        params = parse_qs(urlparse(share_url).query)
        self.assertEqual(
            params["url"][0],
            "https://t.me/junior_middle_bot?start=share_abc123def4567890",
        )

    def test_formatter_contract_stays_intact(self):
        formatter = JobMessageFormatter()
        message = formatter.format_job(
            self.job, view_mode="compact", bot_username="junior_middle_bot"
        )
        self.assertIn("Junior Python Developer", message.text)
        self.assertEqual(message.parse_mode, "MarkdownV2")
        self.assertTrue(message.reply_markup["inline_keyboard"])


if __name__ == "__main__":
    unittest.main()
