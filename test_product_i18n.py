import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import localized_product_runtime_v2 as runtime
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
        self.assertEqual(proxy_context.args, [])
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


if __name__ == "__main__":
    unittest.main()
