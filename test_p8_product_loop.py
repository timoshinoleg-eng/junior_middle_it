import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import durable_product_state as p8
from message_formatter import JobMessageFormatter
from p7_growth_metrics import HIGH_INTENT_EVENTS


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SimpleNamespace()


class FakeQuery:
    def __init__(self, data, message=None):
        self.data = data
        self.message = message or FakeMessage()
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


class FakeSearch:
    def __init__(self, search_id=1, name="Python", skills="python", categories=None):
        self.id = search_id
        self.name = name
        self.skills = skills
        self.categories = categories or ["development"]


class FakeDB:
    def __init__(self):
        self.settings = {
            "enabled_categories": ["development"],
            "skills": "python",
            "min_salary_filter": 0,
            "hide_senior": True,
            "onboarding_done": True,
            "alerts_enabled": False,
            "digest_enabled": False,
        }
        self.searches = [FakeSearch()]
        self.events = []
        self.deleted = []
        self.saved_favorites = []

    def get_user_settings(self, _user_id):
        return dict(self.settings)

    def save_user_settings(self, _user_id, changes):
        self.settings.update(changes)
        return True

    def list_saved_searches(self, _user_id):
        return list(self.searches)

    def delete_saved_search(self, _user_id, search_id):
        self.deleted.append(search_id)
        self.searches = [s for s in self.searches if s.id != search_id]
        return True

    def add_favorite(self, user_id, job_hash):
        self.saved_favorites.append((user_id, job_hash))
        return True

    def log_event(self, user_id, name, props=None):
        self.events.append((user_id, name, props or {}))

    def maybe_unlock_premium(self, _user_id):
        return False


class P8VacancyCardTests(unittest.TestCase):
    def test_card_prioritizes_apply_and_resume_without_referral_clutter(self):
        job = {
            "hash": "abc123",
            "title": "Junior Python Developer",
            "company": "Example",
            "level": "Junior",
            "category": "development",
            "url": "https://example.com/job",
        }
        keyboard = JobMessageFormatter().create_inline_keyboard(
            job,
            bot_username="junior_jobs_channel_bot",
        )["inline_keyboard"]
        labels = [[button["text"] for button in row] for row in keyboard]

        self.assertEqual(labels[0], ["🚀 Откликнуться", "📄 Resume Match"])
        self.assertEqual(labels[1], ["💾 Сохранить", "📤 Поделиться"])
        self.assertIn("⬇️ Подробнее", labels[2])
        self.assertNotIn("🎁 Пригласить друзей", [label for row in labels for label in row])
        self.assertEqual(len(keyboard), 3)


class P8RetentionHubTests(unittest.IsolatedAsyncioTestCase):
    def make_bot(self):
        bot = object.__new__(p8.JobBot)
        bot.db = FakeDB()
        bot.application = SimpleNamespace(bot=AsyncMock())
        bot.formatter = None
        bot.resume_sessions = {}
        bot.pending_saved_searches = {}
        return bot

    async def test_direct_start_opens_retention_hub(self):
        bot = self.make_bot()
        message = FakeMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=77),
            message=message,
        )
        context = SimpleNamespace(args=[])

        await bot.cmd_start(update, context)

        self.assertEqual(len(message.calls), 1)
        text, kwargs = message.calls[0]
        self.assertIn("Профиль готов", text)
        labels = [
            button.text
            for row in kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("⚡ 3 свежих match", labels)
        self.assertIn("🔔 Алерты: ВЫКЛ", labels)
        self.assertIn("📬 Digest: ВЫКЛ", labels)
        self.assertIn("💾 Поиски (1)", labels)
        self.assertIn("⚙️ Профиль", labels)
        event_names = [name for _uid, name, _props in bot.db.events]
        self.assertIn("start", event_names)
        self.assertIn("p8_hub_view", event_names)

    async def test_alert_toggle_persists_and_refreshes_hub(self):
        bot = self.make_bot()
        query = FakeQuery("p8_alerts_toggle")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=77),
        )
        context = SimpleNamespace(bot=AsyncMock())

        await bot.handle_callback(update, context)

        self.assertTrue(bot.db.settings["alerts_enabled"])
        self.assertEqual(query.answers[0][0], "Алерты включены")
        self.assertIn(
            (77, "alerts_on", {"source": "p8_hub"}),
            bot.db.events,
        )
        self.assertTrue(query.message.calls)

    async def test_digest_toggle_persists(self):
        bot = self.make_bot()
        query = FakeQuery("p8_digest_toggle")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=77),
        )
        context = SimpleNamespace(bot=AsyncMock())

        await bot.handle_callback(update, context)

        self.assertTrue(bot.db.settings["digest_enabled"])
        self.assertIn(
            (77, "digest_on", {"source": "p8_hub"}),
            bot.db.events,
        )

    async def test_quick_fresh_uses_three_job_first_value_path(self):
        bot = self.make_bot()
        bot._send_first_value_preview = AsyncMock(return_value=3)
        query = FakeQuery("growth_quick_fresh")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=77),
        )
        context = SimpleNamespace(bot=AsyncMock())

        await bot.handle_callback(update, context)

        bot._send_first_value_preview.assert_awaited_once_with(
            77,
            source="p8_quick_fresh",
        )
        self.assertIn(
            (77, "fresh_preview", {"count": 3, "source": "p8_hub"}),
            bot.db.events,
        )
        self.assertGreaterEqual(len(query.message.calls), 1)

    async def test_saved_search_delete_is_available_without_commands(self):
        bot = self.make_bot()
        query = FakeQuery("p8_saved_search_delete:1")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=77),
        )
        context = SimpleNamespace(bot=AsyncMock())

        await bot.handle_callback(update, context)

        self.assertEqual(bot.db.deleted, [1])
        self.assertEqual(query.answers[0][0], "Поиск удалён")
        self.assertIn(
            (77, "saved_search_deleted", {"search_id": 1, "source": "p8_hub"}),
            bot.db.events,
        )

    async def test_save_continues_into_resume_match_or_alerts(self):
        bot = self.make_bot()
        query = FakeQuery("save:abc123")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=77),
        )
        context = SimpleNamespace(bot=AsyncMock())

        await bot.handle_callback(update, context)

        self.assertEqual(bot.db.saved_favorites, [(77, "abc123")])
        context.bot.send_message.assert_awaited_once()
        kwargs = context.bot.send_message.await_args.kwargs
        labels = [
            button.text
            for row in kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("📄 Resume Match", labels)
        self.assertIn("🔔 Управлять алертами", labels)

    def test_resume_prompt_explains_actionable_three_step_flow(self):
        text = p8.JobBot._resume_prompt({"title": "Backend", "company": "Example"})
        self.assertIn("PDF", text)
        self.assertIn("DOCX", text)
        self.assertIn("сохранить похожий поиск", text)
        self.assertIn("алерты", text)
        self.assertIn("не сохраняется", text)


class P8FunnelTests(unittest.TestCase):
    def test_activation_events_include_p8_first_value_and_retention_actions(self):
        self.assertIn("first_value_preview_sent", HIGH_INTENT_EVENTS)
        self.assertIn("resume_match_completed", HIGH_INTENT_EVENTS)
        self.assertIn("saved_search_created", HIGH_INTENT_EVENTS)

    def test_funnel_composes_existing_cohort_and_utility_metrics(self):
        db = object.__new__(p8.DatabaseConnection)
        db.growth_stats = MagicMock(return_value={
            "starts": 20,
            "activated_users": 10,
            "setup_done": 8,
            "saves": 5,
            "alert_subscribers": 4,
            "digest_subscribers": 6,
            "d1_retention_pct": 40.0,
            "d7_retention_pct": 20.0,
        })
        db.utility_stats = MagicMock(return_value={
            "resume_completed_users": 3,
            "resume_completion_pct": 75.0,
            "saved_search_users": 2,
            "resume_to_search_pct": 66.7,
            "active_saved_searches": 4,
        })

        stats = db.p8_funnel_stats(7)

        self.assertEqual(stats["starts"], 20)
        self.assertEqual(stats["activated_pct"], 50.0)
        self.assertEqual(stats["setup_pct"], 40.0)
        self.assertEqual(stats["resume_completed"], 3)
        self.assertEqual(stats["saved_search_users"], 2)
        self.assertEqual(stats["active_saved_searches"], 4)
        self.assertEqual(stats["d7_retention_pct"], 20.0)


if __name__ == "__main__":
    unittest.main()
