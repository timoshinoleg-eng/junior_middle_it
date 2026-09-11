import unittest
from types import SimpleNamespace

import localized_product_runtime_v5 as runtime


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
            "onboarding_done": True,
            "alerts_enabled": False,
            "digest_enabled": False,
        }
        self.events = []
        self.jobs = []

    def get_user_language(self, _uid):
        return self.language

    def get_user_settings(self, _uid):
        return dict(self.settings)

    def list_saved_searches(self, _uid):
        return [FakeSearch()]

    def log_event(self, uid, name, props=None):
        self.events.append((uid, name, props or {}))

    def recent_jobs_for_digest(self, hours=36, limit=80):
        return list(self.jobs)[:limit]


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeMessage()
        self.answer_calls = 0

    async def answer(self, *_args, **_kwargs):
        self.answer_calls += 1
        return True


class FakeTelegramBot:
    def __init__(self, member=None, error=None):
        self.member = member
        self.error = error
        self.member_calls = []
        self.sent = []

    async def get_chat_member(self, chat_id, user_id):
        self.member_calls.append((chat_id, user_id))
        if self.error:
            raise self.error
        return self.member

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=len(self.sent))


class ChannelBotSoftLinkTests(unittest.IsolatedAsyncioTestCase):
    def make_bot(self, language="en", member=None, error=None):
        bot = object.__new__(runtime.JobBot)
        bot.db = FakeDB(language)
        bot._language_cache = {}
        bot.application = SimpleNamespace(bot=FakeTelegramBot(member=member, error=error))
        return bot

    async def test_hub_uses_measurable_channel_callback_not_direct_url(self):
        bot = self.make_bot("en")
        markup = bot._retention_keyboard_from_state(
            "en",
            bot.db.settings,
            [FakeSearch()],
        )
        channel_buttons = [
            button
            for row in markup.inline_keyboard
            for button in row
            if "channel" in button.text.lower()
        ]
        self.assertEqual(len(channel_buttons), 1)
        self.assertEqual(channel_buttons[0].callback_data, "p11_channel")
        self.assertIsNone(channel_buttons[0].url)

    async def test_subscribed_user_gets_status_bonus_and_both_metric(self):
        member = SimpleNamespace(status="member")
        bot = self.make_bot("ru", member=member)
        query = FakeQuery("p11_channel")
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=77),
            callback_query=query,
        )

        await bot.handle_callback(update, SimpleNamespace(args=[]))

        self.assertEqual(query.answer_calls, 1)
        self.assertEqual(len(bot.application.bot.member_calls), 1)
        names = [name for _uid, name, _props in bot.db.events]
        self.assertIn("bot_to_channel_intent", names)
        self.assertIn("channel_membership_checked", names)
        self.assertIn("channel_bot_both", names)
        self.assertEqual(len(query.message.calls), 1)
        text, kwargs = query.message.calls[0]
        self.assertIn("Подписка на канал подтверждена", text)
        labels = [
            button.text
            for row in kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("⭐ Показать 5 свежих вакансий", labels)
        self.assertIn("📢 Открыть канал", labels)

    async def test_non_subscriber_is_not_blocked(self):
        member = SimpleNamespace(status="left")
        bot = self.make_bot("en", member=member)
        query = FakeQuery("p11_channel")
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=88),
            callback_query=query,
        )

        await bot.handle_callback(update, SimpleNamespace(args=[]))

        text, kwargs = query.message.calls[0]
        self.assertIn("nothing is locked", text)
        names = [name for _uid, name, _props in bot.db.events]
        self.assertIn("bot_to_channel_intent", names)
        self.assertIn("channel_membership_checked", names)
        self.assertNotIn("channel_bot_both", names)
        labels = [
            button.text
            for row in kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("📢 Open channel", labels)
        self.assertIn("✅ Check subscription", labels)

    async def test_membership_api_failure_degrades_without_gate(self):
        bot = self.make_bot("ru", error=RuntimeError("forbidden"))
        query = FakeQuery("p11_channel")
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=99),
            callback_query=query,
        )

        await bot.handle_callback(update, SimpleNamespace(args=[]))

        text, kwargs = query.message.calls[0]
        self.assertIn("не смог автоматически проверить", text)
        names = [name for _uid, name, _props in bot.db.events]
        self.assertIn("channel_membership_check_failed", names)
        self.assertNotIn("channel_bot_both", names)
        urls = [
            button.url
            for row in kwargs["reply_markup"].inline_keyboard
            for button in row
            if button.url
        ]
        self.assertTrue(any(url.startswith("https://t.me/") for url in urls))

    async def test_subscriber_bonus_sends_at_most_five_fresh_jobs(self):
        member = SimpleNamespace(status="administrator")
        bot = self.make_bot("en", member=member)
        bot.db.jobs = [
            {
                "hash": f"job-{idx}",
                "title": f"Job {idx}",
                "company": "Example",
                "location": "Remote",
                "level": "Junior",
                "category": "development",
                "url": f"https://example.com/{idx}",
            }
            for idx in range(7)
        ]
        old_growth_available = runtime.core.GROWTH_UTILS_AVAILABLE
        runtime.core.GROWTH_UTILS_AVAILABLE = False
        try:
            query = FakeQuery("p11_channel_bonus")
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=111),
                callback_query=query,
            )
            await bot.handle_callback(update, SimpleNamespace(args=[]))
        finally:
            runtime.core.GROWTH_UTILS_AVAILABLE = old_growth_available

        # one header + five vacancy cards
        self.assertEqual(len(bot.application.bot.sent), 6)
        self.assertIn("5 fresh jobs", bot.application.bot.sent[0]["text"])
        bonus_events = [e for e in bot.db.events if e[1] == "channel_subscriber_bonus_sent"]
        self.assertEqual(len(bonus_events), 1)
        self.assertEqual(bonus_events[0][2]["count"], 5)


if __name__ == "__main__":
    unittest.main()
