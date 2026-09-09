import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import channel_bot as core
import channel_bot_v2 as v2
import growth_store


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SimpleNamespace()


class FakeQuery:
    def __init__(self, data, message):
        self.data = data
        self.message = message
        self.answers = []
        self.edits = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class PostgresGrowthStoreConnectionTests(unittest.TestCase):
    def test_psycopg_auto_prepare_is_disabled_for_supavisor_transaction_pooler(self):
        fake_connection = MagicMock()
        with patch.object(growth_store.psycopg, "connect", return_value=fake_connection) as connect:
            store = growth_store.PostgresGrowthStore.__new__(growth_store.PostgresGrowthStore)
            store.dsn = "postgresql://postgres.project:secret@pooler.example:6543/postgres"
            store.conn = None
            store._connect()

        self.assertIs(store.conn, fake_connection)
        connect.assert_called_once_with(
            store.dsn,
            autocommit=True,
            connect_timeout=8,
            prepare_threshold=None,
        )


class GrowthPersistenceFallbackTests(unittest.TestCase):
    def make_db(self):
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "REQUIRE_DURABLE_GROWTH": "false"},
            clear=False,
        ):
            return v2.DatabaseConnection(":memory:")

    def test_referral_bonus_keeps_senior_filter_enabled(self):
        db = self.make_db()
        old_threshold = core.Config.REF_REWARD_THRESHOLD
        try:
            core.Config.REF_REWARD_THRESHOLD = 3
            self.assertTrue(db.register_referral(101, 1))
            self.assertTrue(db.register_referral(102, 1))
            self.assertTrue(db.register_referral(103, 1))
            self.assertTrue(db.maybe_unlock_premium(1))
            settings = db.get_user_settings(1)
            self.assertTrue(settings["premium_unlocked"])
            self.assertTrue(settings["hide_senior"])
        finally:
            core.Config.REF_REWARD_THRESHOLD = old_threshold
            db.close()

    def test_require_durable_growth_fails_without_dsn(self):
        with patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "REQUIRE_DURABLE_GROWTH": "true"},
            clear=False,
        ):
            with self.assertRaises(Exception):
                v2.DatabaseConnection(":memory:")


class GrowthUXTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        env = patch.dict(
            os.environ,
            {"GROWTH_DATABASE_URL": "", "DATABASE_URL": "", "REQUIRE_DURABLE_GROWTH": "false"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)
        self.db = v2.DatabaseConnection(":memory:")
        self.addCleanup(self.db.close)
        self.bot = v2.JobBot(SimpleNamespace(bot=AsyncMock()), self.db)

    async def test_admin_is_fail_closed_when_admin_id_missing(self):
        message = FakeMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=777),
            effective_message=message,
        )
        old_admin = core.Config.ADMIN_USER_ID
        core.Config.ADMIN_USER_ID = None
        try:
            self.assertFalse(await self.bot.check_admin(update))
            self.assertIn("ADMIN_USER_ID", message.calls[0][0])
        finally:
            core.Config.ADMIN_USER_ID = old_admin

    async def test_start_is_action_first(self):
        message = FakeMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=10),
            message=message,
        )
        context = SimpleNamespace(args=[], bot=AsyncMock())
        old_channel = core.Config.CHANNEL_ID
        old_username = core.Config.BOT_USERNAME
        core.Config.CHANNEL_ID = "@junior_test"
        core.Config.BOT_USERNAME = "junior_test_bot"
        try:
            await self.bot.cmd_start(update, context)
        finally:
            core.Config.CHANNEL_ID = old_channel
            core.Config.BOT_USERNAME = old_username
        self.assertEqual(len(message.calls), 1)
        text, kwargs = message.calls[0]
        self.assertIn("30 секунд", text)
        markup = kwargs["reply_markup"]
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("🎯 Подобрать вакансии", labels)
        self.assertIn("🆕 Показать свежие", labels)

    async def test_onboarding_completion_immediately_delivers_matches(self):
        message = FakeMessage()
        query = FakeQuery("setup_digest_on", message)
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=20),
        )
        context = SimpleNamespace(bot=AsyncMock())
        self.bot.send_personal_digest = AsyncMock(return_value=3)

        await self.bot.handle_callback(update, context)

        settings = self.db.get_user_settings(20)
        self.assertTrue(settings["onboarding_done"])
        self.assertTrue(settings["digest_enabled"])
        self.bot.send_personal_digest.assert_awaited_once_with(20)
        event = self.db.fetchone(
            "SELECT COUNT(*) FROM events WHERE user_id = ? AND name = 'first_value_delivered'",
            (20,),
        )
        self.assertEqual(event[0], 1)


if __name__ == "__main__":
    unittest.main()
