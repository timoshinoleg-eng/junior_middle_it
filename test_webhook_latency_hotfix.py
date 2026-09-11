import unittest
from types import SimpleNamespace

import interactive_webhook_runtime as webhook_base
import localized_webhook_runtime  # noqa: F401 - installs the optimized ledger claim
import localized_product_runtime_v6 as runtime


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
        if "SELECT state FROM growth_telegram_updates" in text:
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


if __name__ == "__main__":
    unittest.main()
