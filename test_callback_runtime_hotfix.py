import unittest
from types import SimpleNamespace

import localized_product_runtime_v4 as runtime


class FakeSearch:
    id = 1
    name = "Python"
    skills = "python"
    categories = ["development"]


class FakeDB:
    def __init__(self, language="en"):
        self.language = language
        self.settings = {
            "enabled_categories": ["development"],
            "skills": "python",
            "min_salary_filter": 0,
            "onboarding_done": False,
            "alerts_enabled": False,
            "digest_enabled": False,
        }
        self.language_reads = 0
        self.settings_reads = 0
        self.search_reads = 0
        self.events = []

    def get_user_language(self, _uid):
        self.language_reads += 1
        return self.language

    def set_user_language(self, uid, language):
        self.language = language
        self.events.append((uid, "language_selected", {"language": language}))
        return True

    def get_user_settings(self, _uid):
        self.settings_reads += 1
        return dict(self.settings)

    def list_saved_searches(self, _uid):
        self.search_reads += 1
        return [FakeSearch()]

    def log_event(self, uid, name, props=None):
        self.events.append((uid, name, props or {}))


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))


class FailingAckQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeMessage()
        self.answer_calls = 0

    async def answer(self, *_args, **_kwargs):
        self.answer_calls += 1
        raise RuntimeError("callback ack unavailable")


class CallbackRuntimeHotfixTests(unittest.IsolatedAsyncioTestCase):
    def make_bot(self, language="en"):
        bot = object.__new__(runtime.JobBot)
        bot.db = FakeDB(language)
        bot._language_cache = {}
        return bot

    async def test_hub_avoids_duplicate_state_reads(self):
        bot = self.make_bot("en")
        message = FakeMessage()

        await bot._send_retention_hub(message, 77)

        self.assertEqual(bot.db.language_reads, 1)
        self.assertEqual(bot.db.settings_reads, 1)
        self.assertEqual(bot.db.search_reads, 1)
        self.assertEqual(len(message.calls), 1)
        labels = [
            button.text
            for row in message.calls[0][1]["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("🇷🇺 Русский", labels)
        self.assertIn("✓ 🇬🇧 English", labels)

    async def test_language_switch_survives_callback_ack_failure(self):
        bot = self.make_bot("en")
        query = FailingAckQuery("p10_lang_ru")
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=77),
            callback_query=query,
        )
        context = SimpleNamespace(args=[])

        await bot.handle_callback(update, context)

        self.assertEqual(query.answer_calls, 1)
        self.assertEqual(bot.db.language, "ru")
        self.assertEqual(bot._lang(77), "ru")
        self.assertEqual(len(query.message.calls), 1)
        text, kwargs = query.message.calls[0]
        self.assertIn("Я помогу искать", text)
        labels = [
            button.text
            for row in kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("✓ 🇷🇺 Русский", labels)
        self.assertIn("🇬🇧 English", labels)

    async def test_old_language_switch_button_still_opens_picker(self):
        bot = self.make_bot("en")
        query = FailingAckQuery("p9_switch_language")
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=77),
            callback_query=query,
        )
        context = SimpleNamespace(args=[])

        await bot.handle_callback(update, context)

        self.assertEqual(len(query.message.calls), 1)
        labels = [
            button.text
            for row in query.message.calls[0][1]["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertEqual(labels, ["🇷🇺 Русский", "🇬🇧 English"])


if __name__ == "__main__":
    unittest.main()
