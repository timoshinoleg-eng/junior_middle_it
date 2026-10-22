import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import localized_product_runtime as localized_product
import interactive_webhook_runtime as webhook_base
import localized_webhook_runtime  # noqa: F401 - installs the optimized ledger claim
import localized_product_runtime_v6 as runtime
import localized_product_runtime_v8 as runtime_v8


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self.current_row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=()):
        text = " ".join(str(query).split())
        self.store.calls.append((text, tuple(params or ())))
        if "FROM (SELECT %s::bigint AS user_id) incoming" in text:
            self.current_row = ("ru", True, True, False, 3)
        elif "SELECT state FROM growth_telegram_updates" in text:
            self.current_row = ("processed",)
        elif "RETURNING update_id" in text and "INSERT INTO growth_telegram_updates" in text:
            self.current_row = (int(params[0]),)
        elif "SELECT COALESCE((" in text and "language_selected" in text:
            self.current_row = ("ru", "salary")
        elif "SELECT onboarding_done, alerts_enabled" in text:
            self.current_row = (True, False, True, 2)
        elif text.endswith("SELECT 1"):
            self.current_row = (1,)
        else:
            self.current_row = None
        return self

    def fetchone(self):
        row = self.current_row
        self.current_row = None
        return row


class FakeConnection:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return FakeCursor(self.store)


class FakeStore:
    def __init__(self):
        self.calls = []
        self.all_categories = ["development", "qa", "data"]
        self.conn = FakeConnection(self)

    def _ensure_conn(self):
        return self.conn


class LatencyDatabaseTests(unittest.TestCase):
    def make_db(self):
        db = object.__new__(runtime.DatabaseConnection)
        db._growth_store = FakeStore()
        return db

    def test_partial_settings_update_is_one_round_trip(self):
        db = self.make_db()
        ok = db.save_user_settings(77, {"skills": "python", "digest_enabled": True})
        self.assertTrue(ok)
        self.assertEqual(len(db._growth_store.calls), 1)
        sql, params = db._growth_store.calls[0]
        self.assertIn("INSERT INTO growth_user_settings", sql)
        self.assertIn("skills=EXCLUDED.skills", sql)
        self.assertIn("digest_enabled=EXCLUDED.digest_enabled", sql)
        self.assertEqual(params[0], 77)

    def test_runtime_state_cleanup_and_write_share_one_round_trip(self):
        db = self.make_db()
        db.set_runtime_state(77, "setup_step", {"step": "salary"}, ttl_seconds=3600)
        self.assertEqual(len(db._growth_store.calls), 1)
        sql, _params = db._growth_store.calls[0]
        self.assertIn("WITH cleanup AS", sql)
        self.assertIn("DELETE FROM growth_runtime_state", sql)
        self.assertIn("INSERT INTO growth_runtime_state", sql)

    def test_salary_write_and_next_step_are_atomic(self):
        db = self.make_db()
        db.fast_setup_value_and_step(
            77,
            field="min_salary_filter",
            value=2000,
            next_step="skills",
        )
        self.assertEqual(len(db._growth_store.calls), 1)
        sql, params = db._growth_store.calls[0]
        self.assertIn("INSERT INTO growth_user_settings", sql)
        self.assertIn("INSERT INTO growth_runtime_state", sql)
        self.assertEqual(params[0], 77)
        self.assertEqual(params[1], 2000)

    def test_setup_context_combines_language_and_step_read(self):
        db = self.make_db()
        language, step = db.fast_setup_context(77)
        self.assertEqual((language, step), ("ru", "salary"))
        self.assertEqual(len(db._growth_store.calls), 1)
        sql, _params = db._growth_store.calls[0]
        self.assertIn("growth_events", sql)
        self.assertIn("growth_runtime_state", sql)

    def test_finish_setup_is_one_round_trip(self):
        db = self.make_db()
        db.fast_finish_setup(77, True, "ru")
        self.assertEqual(len(db._growth_store.calls), 1)
        sql, _params = db._growth_store.calls[0]
        self.assertIn("growth_user_settings", sql)
        self.assertIn("growth_runtime_state", sql)
        self.assertIn("growth_events", sql)


class LedgerLatencyTests(unittest.TestCase):
    def test_new_update_claim_and_cleanup_share_one_round_trip(self):
        store = FakeStore()
        ledger = webhook_base.TelegramUpdateLedger(SimpleNamespace(_growth_store=store))
        state = ledger.claim(814766999)
        self.assertEqual(state, "claimed")
        self.assertEqual(len(store.calls), 1)
        sql, _params = store.calls[0]
        self.assertIn("WITH cleanup AS", sql)
        self.assertIn("INSERT INTO growth_telegram_updates", sql)
        self.assertIn("DELETE FROM growth_telegram_updates", sql)


class CallbackLatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_callback_ack_happens_before_database_reads(self):
        order = []

        class DB:
            def get_user_language(self, _uid):
                order.append("language")
                return "ru"

            def get_user_settings(self, _uid):
                order.append("settings")
                return {
                    "enabled_categories": ["development"],
                    "alerts_enabled": False,
                    "digest_enabled": False,
                    "onboarding_done": False,
                }

            def save_user_settings(self, _uid, _patch):
                order.append("save")
                return True

        class Query:
            data = "toggle_cat:qa"

            def __init__(self):
                self.message = SimpleNamespace()

            async def answer(self, *_args, **_kwargs):
                order.append("ack")
                return True

            async def edit_message_reply_markup(self, **_kwargs):
                order.append("edit")

        bot = object.__new__(runtime.JobBot)
        bot.db = DB()
        bot._language_cache = {}
        query = Query()
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=77),
        )

        await bot.handle_callback(update, SimpleNamespace())

        self.assertEqual(order[0], "ack")
        self.assertLess(order.index("ack"), order.index("language"))
        self.assertLess(order.index("ack"), order.index("settings"))



class FastStartAndSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_user_start_replies_before_noncritical_db_writes(self):
        sequence = []

        class DB:
            def get_user_language(self, _uid):
                sequence.append("language")
                return "ru"

            def log_event(self, *_args):
                sequence.append("event")

            def maybe_unlock_premium(self, _uid):
                sequence.append("premium")

        bot = object.__new__(localized_product.JobBot)
        bot.db = DB()

        async def send_hub(_message, _uid):
            sequence.append("reply")

        bot._send_retention_hub = send_hub
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=77),
            message=SimpleNamespace(),
        )
        await bot.cmd_start(update, SimpleNamespace(args=[]))
        self.assertEqual(sequence, ["language", "reply", "event", "premium"])

    async def test_start_reuses_language_hint_from_entrypoint(self):
        sequence = []

        class DB:
            def get_user_language(self, _uid):
                raise AssertionError("language already fetched by entrypoint")

            def log_event(self, *_args):
                sequence.append("event")

            def maybe_unlock_premium(self, _uid):
                sequence.append("premium")

        bot = object.__new__(localized_product.JobBot)
        bot.db = DB()
        bot._start_language_hint = (77, "ru")

        async def send_hub(_message, _uid):
            sequence.append("reply")

        bot._send_retention_hub = send_hub
        await bot.cmd_start(
            SimpleNamespace(effective_user=SimpleNamespace(id=77), message=SimpleNamespace()),
            SimpleNamespace(args=[]),
        )
        self.assertEqual(sequence, ["reply", "event", "premium"])

    async def test_failed_start_analytics_does_not_replay_delivered_welcome(self):
        replies = []

        class DB:
            def get_user_language(self, _uid):
                return "ru"

            def log_event(self, *_args):
                raise ConnectionError("analytics backend unavailable")

            def maybe_unlock_premium(self, _uid):
                raise AssertionError("should not run after event failed")

        bot = object.__new__(localized_product.JobBot)
        bot.db = DB()

        async def send_hub(_message, _uid):
            replies.append("sent")

        bot._send_retention_hub = send_hub
        await bot.cmd_start(
            SimpleNamespace(effective_user=SimpleNamespace(id=77), message=SimpleNamespace()),
            SimpleNamespace(args=[]),
        )
        self.assertEqual(replies, ["sent"])

    async def test_no_recent_profile_matches_shows_explicitly_unmatched_examples(self):
        examples = [
            {"hash": str(i) * 40, "title": f"Job {i}", "category": "development"}
            for i in range(1, 5)
        ]
        sent = AsyncMock()
        events = []

        class DB:
            def recent_jobs_for_digest(self, *, hours, limit):
                self_args.append((hours, limit))
                return examples

            def log_event(self, *args):
                events.append(args)

        self_args = []
        bot = object.__new__(localized_product.JobBot)
        bot.db = DB()
        bot.application = SimpleNamespace(bot=SimpleNamespace(send_message=sent))
        bot._select_first_value_jobs = lambda _uid: ({"enabled_categories": ["security"]}, [])
        bot._lang = lambda _uid: "ru"
        bot._job_text = lambda job, lang: job["title"]
        bot._job_keyboard = lambda job, lang: None
        with patch.object(localized_product.core, "GROWTH_UTILS_AVAILABLE", True), patch.object(
            localized_product.core, "job_matches_profile", return_value=False
        ):
            count = await bot._send_first_value_preview(77, source="test")
        self.assertEqual(count, 3)
        self.assertEqual(self_args, [(168, 80)])
        self.assertIn("НЕ персональные совпадения", sent.await_args_list[0].kwargs["text"])
        self.assertEqual(len(sent.await_args_list), 4)
        for call in sent.await_args_list[1:]:
            self.assertIn("ПРИМЕР ВНЕ ТВОИХ ФИЛЬТРОВ", call.kwargs["text"])
            self.assertIn("не персональное совпадение", call.kwargs["text"])
            self.assertIn("Категория:", call.kwargs["text"])
        self.assertEqual(events[-1][1], "first_value_preview_sent")

    async def test_week_long_strict_matches_precede_market_examples(self):
        sent = AsyncMock()
        jobs = [
            {"hash": "a" * 40, "title": "Security", "category": "security"},
            {"hash": "b" * 40, "title": "Development", "category": "development"},
        ]

        class DB:
            def recent_jobs_for_digest(self, **_kwargs):
                return jobs

            def log_event(self, *_args):
                pass

        bot = object.__new__(localized_product.JobBot)
        bot.db = DB()
        bot.application = SimpleNamespace(bot=SimpleNamespace(send_message=sent))
        bot._select_first_value_jobs = lambda _uid: ({"enabled_categories": ["security"]}, [])
        bot._lang = lambda _uid: "ru"
        bot._job_text = lambda job, lang: job["title"]
        bot._job_keyboard = lambda job, lang: None
        with patch.object(localized_product.core, "GROWTH_UTILS_AVAILABLE", True), patch.object(
            localized_product.core, "job_matches_profile",
            side_effect=lambda job, _profile: job["category"] == "security",
        ):
            count = await bot._send_first_value_preview(77, source="test")
        self.assertEqual(count, 1)
        self.assertIn("за неделю", sent.await_args_list[0].kwargs["text"])
        self.assertEqual(sent.await_args_list[1].kwargs["text"], "Security")


class SingleReadStartTests(unittest.TestCase):
    def test_fast_start_snapshot_uses_one_read_only_round_trip(self):
        db = object.__new__(runtime_v8.DatabaseConnection)
        db._growth_store = FakeStore()
        snapshot = db.fast_start_snapshot(77)
        self.assertEqual(snapshot, {
            "language": "ru",
            "onboarding_done": True,
            "alerts_enabled": True,
            "digest_enabled": False,
            "search_count": 3,
        })
        self.assertEqual(len(db._growth_store.calls), 1)
        sql, params = db._growth_store.calls[0]
        self.assertIn("FROM growth_events", sql)
        self.assertIn("FROM growth_saved_searches", sql)
        self.assertIn("JOIN growth_user_settings", sql)
        self.assertEqual(params, (77, 77, 77))
        self.assertNotIn("INSERT", sql)
        self.assertNotIn("UPDATE", sql)

    def test_fast_start_does_not_mutate_profile(self):
        db = object.__new__(runtime_v8.DatabaseConnection)
        db._growth_store = FakeStore()
        db.fast_start_snapshot(77)
        self.assertEqual(len(db._growth_store.calls), 1)
        self.assertEqual(
            db._growth_store.calls[0][0].split()[0], "SELECT"
        )


class OneRoundTripStartUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_user_one_read_then_reply_before_analytics(self):
        sequence = []

        class DB:
            def fast_start_snapshot(self, uid):
                sequence.append("snapshot")
                return {
                    "language": "ru", "onboarding_done": True,
                    "alerts_enabled": True, "digest_enabled": False,
                    "search_count": 3,
                }

            def log_event(self, *_args):
                sequence.append("analytics")

            def maybe_unlock_premium(self, uid):
                sequence.append("premium")

            def get_user_language(self, _uid):
                raise AssertionError("duplicate language query")

            def get_user_settings(self, _uid):
                raise AssertionError("duplicate settings query")

            def list_saved_searches(self, _uid):
                raise AssertionError("duplicate saved search query")

        bot = object.__new__(runtime_v8.JobBot)
        bot.db = DB()
        bot._language_cache = {}
        bot._retention_keyboard_from_state = lambda *_a: None

        async def reply(*_args, **_kwargs):
            sequence.append("reply")

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=77),
            message=SimpleNamespace(reply_text=AsyncMock(side_effect=reply)),
        )
        await bot.cmd_start(update, SimpleNamespace(args=[]))
        self.assertEqual(sequence, ["snapshot", "reply", "analytics", "analytics", "premium"])
        self.assertEqual(bot._language_cache[77], "ru")

    async def test_new_user_receives_language_selector_before_event(self):
        sequence = []

        class DB:
            def fast_start_snapshot(self, uid):
                sequence.append("snapshot")
                return {"language": "", "search_count": 0}

            def log_event(self, *_args):
                sequence.append("analytics")

        bot = object.__new__(runtime_v8.JobBot)
        bot.db = DB()

        async def reply(*_args, **_kwargs):
            sequence.append("reply")

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=77),
            message=SimpleNamespace(reply_text=AsyncMock(side_effect=reply)),
        )
        await bot.cmd_start(update, SimpleNamespace(args=[]))
        self.assertEqual(sequence, ["snapshot", "reply", "analytics"])

    async def test_referral_deeplink_delegates_unchanged(self):
        bot = object.__new__(runtime_v8.JobBot)
        update = SimpleNamespace(effective_user=SimpleNamespace(id=77))
        context = SimpleNamespace(args=["ref_123"])
        with patch.object(runtime_v8.base.JobBot, "cmd_start", new_callable=AsyncMock) as parent:
            await bot.cmd_start(update, context)
        parent.assert_awaited_once_with(update, context)

if __name__ == "__main__":
    unittest.main()
