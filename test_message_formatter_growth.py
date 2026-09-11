import unittest
from urllib.parse import parse_qs, urlparse

from message_formatter import (
    JobMessageFormatter,
    build_bot_home_url,
    build_resume_match_url,
    build_telegram_share_url,
)


class TelegramShareGrowthTests(unittest.TestCase):
    def setUp(self):
        self.job = {
            "title": "Junior Python Developer",
            "company": "Example Labs",
            "level": "Junior",
            "level_source": "explicit_title",
            "category": "development",
            "primary_track": "development",
            "salary": "$2000-3000",
            "location": "Remote",
            "remote_evidence": "location: Remote",
            "remote_scope": "worldwide",
            "description": "Build APIs with Python and FastAPI for a distributed product team.",
            "tags": ["Python", "FastAPI"],
            "source": "Test",
            "url": "https://example.com/jobs/123?utm_source=board",
            "hash": "abc123def4567890",
        }

    def test_share_button_uses_official_telegram_url(self):
        formatter = JobMessageFormatter()
        keyboard = formatter.create_inline_keyboard(self.job, bot_username="junior_middle_bot")
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
            "https://t.me/junior_middle_bot?start=web_home",
            shared_text,
        )

    def test_resume_check_cta_opens_private_deeplink(self):
        formatter = JobMessageFormatter()
        keyboard = formatter.create_inline_keyboard(self.job, bot_username="junior_middle_bot")
        resume = next(
            button
            for row in keyboard["inline_keyboard"]
            for button in row
            if button.get("text") == "📄 Проверить резюме"
        )
        self.assertEqual(
            resume["url"],
            "https://t.me/junior_middle_bot?start=resume_abc123def4567890",
        )
        self.assertNotIn("callback_data", resume)
        self.assertEqual(
            build_resume_match_url(self.job, "@junior_middle_bot"),
            "https://t.me/junior_middle_bot?start=resume_abc123def4567890",
        )

    def test_public_channel_keyboard_is_url_only(self):
        formatter = JobMessageFormatter()
        keyboard = formatter.create_inline_keyboard(self.job, bot_username="junior_middle_bot")
        buttons = [button for row in keyboard["inline_keyboard"] for button in row]
        self.assertTrue(buttons)
        self.assertTrue(all("callback_data" not in button for button in buttons))
        labels = [button["text"] for button in buttons]
        self.assertNotIn("⬇️ Подробнее", labels)
        self.assertNotIn("💾 Сохранить", labels)
        self.assertFalse(any(label.startswith("🚫") for label in labels))
        self.assertIn("🎯 Мой поиск вакансий", labels)

    def test_channel_card_is_mobile_first_and_has_no_duplicate_apply_link(self):
        formatter = JobMessageFormatter()
        message = formatter.format_job(
            self.job, view_mode="compact", bot_username="junior_middle_bot"
        )
        self.assertIn("JUNIOR", message.text)
        self.assertIn("РАЗРАБОТКА", message.text)
        self.assertIn("Junior Python Developer", message.text)
        self.assertIn("Example Labs", message.text)
        self.assertIn("Remote", message.text)
        self.assertIn("$2000", message.text)
        self.assertIn("Build APIs with Python", message.text)
        self.assertNotIn("[🔗", message.text)
        self.assertNotIn("Источник:", message.text)
        self.assertEqual(message.parse_mode, "MarkdownV2")

    def test_unknown_salary_is_not_called_negotiable(self):
        formatter = JobMessageFormatter()
        job = dict(self.job, salary="Не указана")
        message = formatter.format_job(job, view_mode="compact", bot_username="junior_middle_bot")
        self.assertIn("Зарплата не указана", message.text)
        self.assertNotIn("Договорная", message.text)

    def test_html_summary_is_cleaned(self):
        formatter = JobMessageFormatter()
        job = dict(
            self.job,
            description="<p><strong>Build APIs</strong> &amp; data products for customers.</p>",
        )
        message = formatter.format_job(job, view_mode="compact", bot_username="junior_middle_bot")
        self.assertIn("Build APIs & data products", message.text.replace("\\&", "&"))
        self.assertNotIn("<strong>", message.text)
        self.assertNotIn("&amp;", message.text)

    def test_geo_restriction_is_visible_without_technical_wording(self):
        formatter = JobMessageFormatter()
        job = dict(
            self.job,
            location="München",
            remote_scope="country_restricted",
            geo_restriction="Germany",
        )
        message = formatter.format_job(job, view_mode="compact", bot_username="junior_middle_bot")
        self.assertIn("Remote · Germany", message.text)
        self.assertNotIn("country_restricted", message.text)

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

    def test_bot_home_url(self):
        self.assertEqual(
            build_bot_home_url("@junior_middle_bot"),
            "https://t.me/junior_middle_bot?start=web_home",
        )
        self.assertEqual(build_bot_home_url(""), "")


if __name__ == "__main__":
    unittest.main()
