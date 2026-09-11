import unittest
from types import SimpleNamespace

from telegram.error import BadRequest

import localized_product_runtime_v8 as runtime
from skill_normalization import normalize_skills_input, normalize_skill_token


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=()):
        text = " ".join(str(query).split())
        self.store.calls.append((text, tuple(params or ())))
        if "FROM settings_write" in text and "enabled_categories" in text:
            self.row = ("development,qa", "ru")
        elif "growth_saved_searches" in text and "setup_done" in text:
            self.row = ("ru", 0)
        elif "FROM state_write" in text and "language_selected" in text:
            self.row = ("ru",)
        else:
            self.row = None
        return self

    def fetchone(self):
        row = self.row
        self.row = None
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


class SkillNormalizationTests(unittest.TestCase):
    def test_common_typos_are_canonicalized(self):
        skills = normalize_skills_input("pyton, post gres, react js")
        self.assertEqual(skills.stored, "python,postgres,react")
        self.assertEqual(skills.display, "Python, PostgreSQL, React")
        self.assertGreaterEqual(len(skills.corrections), 3)

    def test_postgresql_alias_maps_to_postgres(self):
        self.assertEqual(normalize_skill_token("PostgreSQL")[0], "postgres")

    def test_unknown_skill_is_preserved_not_invented(self):
        value, corrected = normalize_skill_token("my-internal-tool")
        self.assertEqual(value, "my-internal-tool")
        self.assertFalse(corrected)


class DatabaseFastPathTests(unittest.TestCase):
    def make_db(self):
        db = object.__new__(runtime.DatabaseConnection)
        db._growth_store = FakeStore()
        return db

    def test_category_toggle_is_one_round_trip(self):
        db = self.make_db()
        lang, enabled = db.fast_toggle_category(77, "qa")
        self.assertEqual(lang, "ru")
        self.assertEqual(enabled, ["development", "qa"])
        self.assertEqual(len(db._growth_store.calls), 1)
        sql, _params = db._growth_store.calls[0]
        self.assertIn("string_to_array", sql)
        self.assertIn("settings_write", sql)
        self.assertIn("language_selected", sql)

    def test_advance_setup_is_one_round_trip(self):
        db = self.make_db()
        lang = db.fast_advance_setup(77, "salary")
        self.assertEqual(lang, "ru")
        self.assertEqual(len(db._growth_store.calls), 1)
        sql, _params = db._growth_store.calls[0]
        self.assertIn("growth_runtime_state", sql)
        self.assertIn("language_selected", sql)

    def test_none_delivery_disables_both_automatic_modes(self):
        db = self.make_db()
        snapshot = db.fast_finish_setup_delivery(77, "none")
        self.assertFalse(snapshot["alerts_enabled"])
        self.assertFalse(snapshot["digest_enabled"])
        self.assertEqual(len(db._growth_store.calls), 1)
        sql, params = db._growth_store.calls[0]
        self.assertIn("alerts_enabled", sql)
        self.assertIn("digest_enabled", sql)
        self.assertFalse(params[1])
        self.assertFalse(params[2])


class SetupBotTests(unittest.IsolatedAsyncioTestCase):
    async def test_skill_step_stores_normalized_values_and_offers_three_modes(self):
        class DB:
            def __init__(self):
                self.saved = None

            def fast_setup_context(self, _uid):
                return "ru", "skills"

            def fast_setup_value_and_step(self, uid, *, field, value, next_step):
                self.saved = (uid, field, value, next_step)

        class Message:
            text = "pyton, post gres"

            def __init__(self):
                self.reply = None

            async def reply_text(self, text, reply_markup=None, **_kwargs):
                self.reply = (text, reply_markup)

        bot = object.__new__(runtime.JobBot)
        bot.db = DB()
        bot._language_cache = {}
        message = Message()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=77),
            message=message,
        )

        await bot.handle_setup_text(update, SimpleNamespace())

        self.assertEqual(bot.db.saved, (77, "skills", "python,postgres", "digest"))
        text, markup = message.reply
        self.assertIn("Python, PostgreSQL", text)
        callbacks = [row[0].callback_data for row in markup.inline_keyboard]
        self.assertEqual(
            callbacks,
            ["setup_delivery_instant", "setup_delivery_daily", "setup_delivery_none"],
        )

    async def test_not_modified_category_edit_is_treated_as_success(self):
        class DB:
            def fast_toggle_category(self, _uid, _category):
                return "ru", ["development", "qa"]

        class Query:
            data = "toggle_cat:qa"

            async def answer(self, *_args, **_kwargs):
                return True

            async def edit_message_reply_markup(self, **_kwargs):
                raise BadRequest("Message is not modified: specified new message content and reply markup are exactly the same")

        bot = object.__new__(runtime.JobBot)
        bot.db = DB()
        bot._language_cache = {}
        update = SimpleNamespace(
            callback_query=Query(),
            effective_user=SimpleNamespace(id=77),
        )

        await bot.handle_callback(update, SimpleNamespace())


if __name__ == "__main__":
    unittest.main()
