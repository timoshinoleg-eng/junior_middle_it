"""Unit tests for the saved-resume profile flow.

Same hermetic style as test_onboarding_flow: CopilotStore.from_database and
the Telegram file download are patched out; no real Postgres is required.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import resume_profile_flow as flow

USER_ID = 4242

RESUME_TEXT = (
    "Иван Иванов, Python-разработчик. Опыт 4 года. "
    "Стек: Python, Django, FastAPI, PostgreSQL, Redis, Docker. "
    "Занимался разработкой backend-сервисов, unit-тестированием (pytest), "
    " code review и наставничеством джунов. Уровень middle."
)


class FakeDb:
    def __init__(self):
        self.settings = {}
        self.events = []

    def get_user_settings(self, user_id):
        return dict(self.settings)

    def save_user_settings(self, user_id, values):
        self.settings.update(values or {})

    def log_event(self, user_id, name, props=None):
        self.events.append((name, dict(props or {})))


class FakeJobBot:
    def __init__(self, store):
        self.db = FakeDb()
        self._store = store


class FakeStore:
    def __init__(self):
        self.resumes = {}
        self.profiles = []

    def save_resume(self, user_id, text, *, skills="", grade=""):
        self.resumes[int(user_id)] = {"text": text, "skills": skills, "grade": grade}
        return True

    def get_resume(self, user_id):
        row = self.resumes.get(int(user_id))
        if not row:
            return None
        return {
            "resume_text": row.get("text", ""),
            "skills": row.get("skills", ""),
            "grade": row.get("grade", ""),
            "updated_at": None,
        }

    def delete_resume(self, user_id):
        return self.resumes.pop(int(user_id), None) is not None

    def save_profile(self, user_id, *, grade="", skills="", source=""):
        self.profiles.append((int(user_id), grade, skills, source))
        return True


class FakeMessage:
    def __init__(self, text="", document=None):
        self.text = text
        self.document = document
        self.caption = None
        self.forward_date = None
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeTgFile:
    def __init__(self, payload):
        self._payload = payload

    async def download_as_bytearray(self):
        return bytearray(self._payload)


class FakeBotApi:
    def __init__(self, payload=b"%PDF-fake"):
        self._payload = payload
        self.requests = []

    async def get_file(self, file_id):
        self.requests.append(file_id)
        return FakeTgFile(self._payload)


def make_update(message=None, query=None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
        message=message or FakeMessage(),
        callback_query=query,
    )


def run(coro):
    import asyncio

    return asyncio.run(coro)


class ParseResumeTests(unittest.TestCase):
    def test_parse_extracts_skills_grade_and_search(self):
        parsed = flow.parse_resume(RESUME_TEXT)
        self.assertIn("python", parsed["skills"])
        self.assertIn("django", parsed["skills"])
        self.assertEqual(parsed["grade"], "Middle")
        self.assertEqual(parsed["category"], "development")
        self.assertTrue(parsed["search"].get("name"))

    def test_detect_grade_keywords(self):
        self.assertEqual(flow.detect_resume_grade("senior java dev", []), "Senior")
        self.assertEqual(flow.detect_resume_grade("junior qa", []), "Junior")

    def test_detect_grade_years_fallback(self):
        self.assertEqual(flow.detect_resume_grade("Опыт 5 лет. Разработка.", []), "Middle")
        self.assertEqual(flow.detect_resume_grade("Опыт 7 лет. Разработка.", []), "Senior")
        self.assertEqual(flow.detect_resume_grade("Опыт 1 год. Разработка.", []), "Junior+")
        self.assertEqual(flow.detect_resume_grade("Опыт 6 месяцев. Стажировка.", []), "Junior")

    def test_report_mentions_saved_and_stack(self):
        parsed = flow.parse_resume(RESUME_TEXT)
        report = flow.format_saved_report(parsed)
        self.assertIn("сохранено", report.lower())
        self.assertIn("Middle", report)
        self.assertIn("python", report)


class SaveAndReportTests(unittest.TestCase):
    def test_save_persists_resume_enriches_settings_and_replies(self):
        store = FakeStore()
        bot = FakeJobBot(store)
        message = FakeMessage()
        with patch.object(flow.CopilotStore, "from_database", return_value=store):
            handled = run(flow.save_and_report(bot, make_update(message), RESUME_TEXT))
        self.assertTrue(handled)
        self.assertIn(USER_ID, store.resumes)
        self.assertIn("python", store.resumes[USER_ID]["skills"])
        self.assertEqual(store.resumes[USER_ID]["grade"], "Middle")
        self.assertTrue(any(p[3] == "resume_saved" for p in store.profiles))
        self.assertIn("python", bot.db.settings.get("skills", ""))
        self.assertIn("resume_saved", [name for name, _ in bot.db.events])
        self.assertTrue(message.replies)
        reply_text, kwargs = message.replies[0]
        self.assertIn("сохранено", reply_text.lower())
        markup = kwargs.get("reply_markup")
        self.assertIsNotNone(markup)
        buttons = [b for row in markup.inline_keyboard for b in row]
        self.assertTrue(any(flow.CB_RESUME_DELETE in (b.callback_data or "") for b in buttons))

    def test_short_text_is_not_saved(self):
        store = FakeStore()
        bot = FakeJobBot(store)
        message = FakeMessage()
        with patch.object(flow.CopilotStore, "from_database", return_value=store):
            handled = run(flow.save_and_report(bot, make_update(message), "коротко"))
        self.assertFalse(handled)
        self.assertEqual(store.resumes, {})

    def test_store_unavailable_reports_and_handles(self):
        bot = FakeJobBot(None)
        message = FakeMessage()
        with patch.object(flow.CopilotStore, "from_database", return_value=None):
            handled = run(flow.save_and_report(bot, make_update(message), RESUME_TEXT))
        self.assertTrue(handled)
        self.assertIn("недоступно", message.replies[0][0].lower())


class DocumentTests(unittest.TestCase):
    def _doc(self):
        return SimpleNamespace(file_id="file_1", file_name="cv.pdf", mime_type="application/pdf", file_size=1024)

    def test_document_saved_after_extraction(self):
        store = FakeStore()
        bot = FakeJobBot(store)
        message = FakeMessage(document=self._doc())
        update = make_update(message)
        context = SimpleNamespace(bot=FakeBotApi())
        extracted = SimpleNamespace(text=RESUME_TEXT)
        with patch.object(flow.CopilotStore, "from_database", return_value=store), patch(
            "resume_documents.extract_resume_document", return_value=extracted
        ):
            handled = run(flow.handle_document(bot, update, context))
        self.assertTrue(handled)
        self.assertIn(USER_ID, store.resumes)

    def test_unextractable_document_gets_scan_hint(self):
        store = FakeStore()
        bot = FakeJobBot(store)
        message = FakeMessage(document=self._doc())
        context = SimpleNamespace(bot=FakeBotApi())
        extracted = SimpleNamespace(text="   ")
        with patch.object(flow.CopilotStore, "from_database", return_value=store), patch(
            "resume_documents.extract_resume_document", return_value=extracted
        ):
            handled = run(flow.handle_document(bot, make_update(message), context))
        self.assertTrue(handled)
        self.assertEqual(store.resumes, {})
        self.assertIn("скан", message.replies[0][0].lower())

    def test_caption_or_forward_documents_are_skipped(self):
        store = FakeStore()
        bot = FakeJobBot(store)
        message = FakeMessage(document=self._doc())
        message.caption = "посмотри вакансию"
        context = SimpleNamespace(bot=FakeBotApi())
        handled = run(flow.handle_document(bot, make_update(message), context))
        self.assertFalse(handled)

        message2 = FakeMessage(document=self._doc())
        message2.forward_date = 12345
        handled2 = run(flow.handle_document(bot, make_update(message2), context))
        self.assertFalse(handled2)


class CallbackAndCommandTests(unittest.TestCase):
    def test_delete_callback_removes_resume(self):
        store = FakeStore()
        store.resumes[USER_ID] = {"text": RESUME_TEXT, "skills": "python", "grade": "Middle"}
        bot = FakeJobBot(store)
        query = SimpleNamespace(data=flow.CB_RESUME_DELETE, answered=False, message=FakeMessage())

        async def answer():
            query.answered = True

        query.answer = answer
        with patch.object(flow.CopilotStore, "from_database", return_value=store):
            handled = run(flow.handle_callback_extras(bot, make_update(query=query), None))
        self.assertTrue(handled)
        self.assertTrue(query.answered)
        self.assertEqual(store.resumes, {})
        self.assertIn("resume_deleted", [name for name, _ in bot.db.events])
        self.assertTrue(query.message.replies)

    def test_unknown_callback_passes_through(self):
        store = FakeStore()
        bot = FakeJobBot(store)
        query = SimpleNamespace(data="copilot_ai:aa", message=None)
        handled = run(flow.handle_callback_extras(bot, make_update(query=query), None))
        self.assertFalse(handled)

    def test_cmd_resume_shows_prompt_when_nothing_saved(self):
        store = FakeStore()
        bot = FakeJobBot(store)
        message = FakeMessage()
        run(flow.cmd_resume(bot, make_update(message), None))
        self.assertIn("пока нет", message.replies[0][0].lower())

    def test_cmd_resume_shows_saved_profile(self):
        store = FakeStore()
        store.resumes[USER_ID] = {"text": RESUME_TEXT, "skills": "python, django", "grade": "Middle"}
        bot = FakeJobBot(store)
        message = FakeMessage()
        with patch.object(flow.CopilotStore, "from_database", return_value=store):
            run(flow.cmd_resume(bot, make_update(message), None))
        self.assertIn("Сохранённое резюме", message.replies[0][0])
        self.assertIn("Middle", message.replies[0][0])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# SQL-level tests: CopilotStore resume methods + growth extra-settings merge
# (stub connections, no real Postgres).
# ---------------------------------------------------------------------------


class _StubCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []
        self.index = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=()):
        self.conn.queries.append((" ".join(str(query).split()), tuple(params or ())))
        self.rows = self.conn.rows_for(" ".join(str(query).split()))
        self.index = 0

    def fetchone(self):
        if self.index >= len(self.rows):
            return None
        row = self.rows[self.index]
        self.index += 1
        return row

    def fetchall(self):
        rows = self.rows[self.index:]
        self.index = len(self.rows)
        return rows


class _StubConn:
    def __init__(self, resume_row=None, extra=None):
        self.queries = []
        self.resume_row = resume_row
        self.extra = extra if extra is not None else {}
        self.closed = False

    def cursor(self):
        return _StubCursor(self)

    def rows_for(self, query):
        if query.startswith("SELECT resume_text"):
            return [self.resume_row] if self.resume_row else []
        if query.startswith("SELECT enabled_categories"):
            return [("development,", True, 0, "python", False, True, False, False, self.extra)]
        if query.startswith("INSERT INTO growth_copilot_resumes"):
            return [(USER_ID,)]
        if query.startswith("DELETE FROM growth_copilot_resumes"):
            return [(USER_ID,)] if self.resume_row else []
        if query.startswith("INSERT INTO growth_user_settings"):
            return [(USER_ID,)]
        return []


class _StubGrowth:
    def __init__(self, resume_row=None, extra=None):
        self.conn = _StubConn(resume_row=resume_row, extra=extra)

    def _ensure_conn(self):
        return self.conn


class CopilotStoreResumeSqlTests(unittest.TestCase):
    def _store(self, **kwargs):
        from copilot.store import CopilotStore

        return CopilotStore(_StubGrowth(**kwargs))

    def test_save_get_delete_resume_roundtrip(self):
        store = self._store(resume_row=(RESUME_TEXT, "python", "Middle", None))
        self.assertTrue(store.save_resume(USER_ID, RESUME_TEXT, skills="python", grade="Middle"))
        row = store.get_resume(USER_ID)
        self.assertIsNotNone(row)
        self.assertIn("Python", row["resume_text"])
        self.assertEqual(row["grade"], "Middle")
        self.assertTrue(store.delete_resume(USER_ID))
        store2 = self._store()
        self.assertFalse(store2.delete_resume(USER_ID))
        self.assertIsNone(store2.get_resume(USER_ID))


class GrowthExtraSettingsTests(unittest.TestCase):
    def _store(self, extra=None):
        from growth_store import PostgresGrowthStore

        store = PostgresGrowthStore.__new__(PostgresGrowthStore)
        store.all_categories = ["development"]
        store.conn = _StubConn(extra=extra)
        return store

    def test_extra_keys_roundtrip_through_merge(self):
        store = self._store(extra={"magnet_claimed": True})
        settings = store.get_user_settings(USER_ID)
        self.assertTrue(settings["magnet_claimed"])
        self.assertEqual(settings["skills"], "python")
        store.save_user_settings(USER_ID, {"stars_charge_ids": ["chg_1"], "skills": "python, django"})
        insert = [q for q in store.conn.queries if q[0].startswith("INSERT INTO growth_user_settings")][-1]
        extra = insert[1][-1]
        payload = getattr(extra, "obj", extra)
        self.assertEqual(payload.get("stars_charge_ids"), ["chg_1"])
        self.assertTrue(payload.get("magnet_claimed"))
        self.assertNotIn("skills", payload)  # first-class column, not extra

    def test_defaults_without_row(self):
        store = self._store()
        # No row -> stub returns defaults row; with empty extra there is no extra data.
        settings = store.get_user_settings(999)
        self.assertEqual(settings["skills"], "python")


if __name__ == "__main__":
    unittest.main()
