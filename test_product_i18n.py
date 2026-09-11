import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import localized_product_runtime_v3 as runtime
from product_i18n import t


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))


class FakeQuery:
    def __init__(self, data, message=None):
        self.data = data
        self.message = message or FakeMessage()
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


class FakeSearch:
    id = 1
    name = "Python"
    skills = "python"
    categories = ["development"]

    def as_profile(self):
        return {
            "enabled_categories": list(self.categories),
            "skills": self.skills,
            "min_salary_filter": 0,
            "hide_senior": True,
        }


class FakeDB:
    def __init__(self, language=""):
        self.language = language
        self.settings = {
            "enabled_categories": ["development", "qa"],
            "skills": "python",
            "min_salary_filter": 2000,
            "hide_senior": True,
            "onboarding_done": False,
            "alerts_enabled": False,
            "digest_enabled": False,
        }
        self.events = []
        self.referrals = []
        self.jobs = [{
            "hash": "abc123",
            "title": "Junior Python Developer",
            "company": "Example",
            "category": "development",
            "level": "Junior",
            "location": "Remote",
            "url": "https://example.com/job",
            "tags": ["python"],
        }]

    def get_user_language(self, _uid):
        return self.language

    def set_user_language(self, uid, language):
        self.language = language
        self.log_event(uid, "language_selected", {"language": language})
        return True

    def get_user_settings(self, _uid):
        return dict(self.settings)

    def save_user_settings(self, _uid, values):
        self.settings.update(values)
        return True

    def list_saved_searches(self, _uid):
        return [FakeSearch()]

    def list_alert_subscribers(self):
        return [77]

    def recent_jobs_for_digest(self, **_kwargs):
        return list(self.jobs)

    def register_referral(self, user_id, referrer_id):
        self.referrals.append((user_id, referrer_id))
        return True

    def log_event(self, uid, name, props=None):
        self.events.append((uid, name, props or {}))

    def maybe_unlock_premium(self, _uid):
        return False


class ProductCopyTests(unittest.TestCase):
    def test_russian_first_time_copy_uses_plain_terms(self):
        text = t("ru", "welcome_new")
        self.assertIn("подобрать вакансии", text)
        self.assertIn("раз в день", text)
        self.assertNotIn("Digest", text)
        self.assertNotIn("алерт", text.lower())
        self.assertNotIn("match", text.lower())

    def test_english_first_time_copy_is_fully_english(self):
        text = t("en", "welcome_new")
        self.assertIn("find jobs", text)
        self.assertIn("daily roundup", text)
        self.assertNotIn("ваканс", text.lower())
        self.assertNotIn("резюм", text.lower())


class LocalizedHubTests(unittest.IsolatedAsyncioTestCase):
    def make_bot(self, language):
        bot = object.__new__(runtime.JobBot)
        bot.db = FakeDB(language)
        bot.application = SimpleNamespace(bot=AsyncMock())
        bot.formatter = None
        bot.setup_steps = {}
        bot.resume_sessions = {}
        bot.pending_saved_searches = {}
        return bot

    async def test_first_start_requires_language_choice(self):
        bot = self.make_bot("")
        message = FakeMessage()
        update = SimpleNamespace(effective_user=SimpleNamespace(id=77), message=message)
        context = SimpleNamespace(args=[])
        await bot.cmd_start(update, context)
        self.assertEqual(len(message.calls), 1)
        text, kwargs = message.calls[0]
        self.assertIn("Выберите язык", text)
        labels = [button.text for row in kwargs["reply_markup"].inline_keyboard for button in row]
        self.assertEqual(labels, ["🇷🇺 Русский", "🇬🇧 English"])

    async def test_referral_is_attributed_before_language_event(self):
        bot = self.make_bot("")
        message = FakeMessage()
        update = SimpleNamespace(effective_user=SimpleNamespace(id=77), message=message)
        context = SimpleNamespace(args=["ref_123"])
        with patch.object(runtime.core, "GROWTH_UTILS_AVAILABLE", True):
            await bot.cmd_start(update, context)
        self.assertEqual(bot.db.referrals, [(77, 123)])
        self.assertEqual(len(message.calls), 1)
        self.assertIn("Выберите язык", message.calls[0][0])

    async def test_language_choice_is_persisted_and_deeplink_continues(self):
        bot = self.make_bot("")
        bot.cmd_start = AsyncMock()
        query = FakeQuery("p9_lang_en:web_home")
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=77),
            callback_query=query,
        )
        context = SimpleNamespace(args=[])

        await bot.handle_callback(update, context)

        self.assertEqual(bot.db.language, "en")
        self.assertIn((77, "language_selected", {"language": "en"}), bot.db.events)
        bot.cmd_start.assert_awaited_once()
        proxy_update, proxy_context = bot.cmd_start.await_args.args
        self.assertIs(proxy_update.message, query.message)
        self.assertIs(proxy_context, context)
        self.assertEqual(context.args, [])

    async def test_russian_hub_is_novice_friendly(self):
        bot = self.make_bot("ru")
        message = FakeMessage()
        await bot._send_retention_hub(message, 77)
        text, kwargs = message.calls[0]
        labels = [button.text for row in kwargs["reply_markup"].inline_keyboard for button in row]
        self.assertIn("Новые вакансии сразу", text)
        self.assertIn("подборку раз в день", text.lower())
        self.assertIn("🔎 Показать 3 вакансии", labels)
        self.assertIn("🔔 Новые сразу: ВЫКЛ", labels)
        self.assertIn("📅 Раз в день: ВЫКЛ", labels)
        self.assertIn("💾 Мои подборки (1)", labels)
        self.assertNotIn("Digest", text + " ".join(labels))
        self.assertNotIn("Алерты", text + " ".join(labels))

    async def test_english_hub_has_no_russian_product_copy(self):
        bot = self.make_bot("en")
        message = FakeMessage()
        await bot._send_retention_hub(message, 77)
        text, kwargs = message.calls[0]
        labels = [button.text for row in kwargs["reply_markup"].inline_keyboard for button in row]
        self.assertIn("new jobs immediately", text.lower())
        self.assertIn("🔎 Show 3 jobs", labels)
        self.assertIn("📅 Daily roundup: OFF", labels)
        self.assertIn("💾 My searches (1)", labels)
        self.assertNotIn("ваканс", text.lower())
        self.assertNotIn("подбор", text.lower())

    async def test_setup_categories_follow_selected_language(self):
        bot = self.make_bot("en")
        keyboard = bot._category_keyboard(77)
        labels = [button.text for row in keyboard.inline_keyboard for button in row]
        self.assertIn("✅ Development", labels)
        self.assertIn("✅ QA / Testing", labels)
        self.assertIn("✅ Next: salary", labels)
        self.assertNotIn("Разработка", labels)

    async def test_daily_roundup_follows_english_language(self):
        bot = self.make_bot("en")
        bot._effective_profile = lambda _uid: dict(bot.db.settings)
        bot._digest_limit = lambda _settings: 5
        with patch.object(runtime.core, "GROWTH_UTILS_AVAILABLE", False):
            sent = await bot.send_personal_digest(77)

        self.assertEqual(sent, 1)
        calls = bot.application.bot.send_message.await_args_list
        self.assertGreaterEqual(len(calls), 2)
        header = calls[0].kwargs["text"]
        card = calls[1].kwargs["text"]
        self.assertIn("Your daily roundup", header)
        self.assertNotIn("подбор", header.lower())
        self.assertIn("Junior Python Developer", card)
        labels = [
            button.text
            for row in calls[1].kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("🚀 Apply", labels)
        self.assertIn("📄 Check my resume", labels)


if __name__ == "__main__":
    unittest.main()
