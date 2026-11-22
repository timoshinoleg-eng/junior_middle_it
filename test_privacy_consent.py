import unittest
from unittest.mock import patch
import resume_profile_flow as flow
from test_resume_profile_flow import FakeStore, FakeJobBot, FakeMessage, make_update, run, RESUME_TEXT

class PrivacyConsentTests(unittest.TestCase):
    def test_unrequested_text_never_persists_full_resume(self):
        store = FakeStore()
        bot = FakeJobBot(store)
        message = FakeMessage()
        with patch.object(flow.CopilotStore, 'from_database', return_value=store):
            run(flow.save_and_report(bot, make_update(message), RESUME_TEXT))
        self.assertEqual(store.resumes, {})
        self.assertEqual(store.profiles, [])
        self.assertEqual(bot.db.settings, {})

    def _delete(self, store, bot):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        query = SimpleNamespace(data=flow.CB_RESUME_DELETE, message=FakeMessage(), answer=AsyncMock())
        with patch.object(flow.CopilotStore, "from_database", return_value=store):
            self.assertTrue(run(flow.handle_callback_extras(bot, make_update(query=query), None)))
        query.answer.assert_awaited_once()
        return query.message.replies[0][0]

    def test_cleanup_failures_are_not_reported_as_success(self):
        for target, result in (("profile", False), ("profile", RuntimeError()),
                               ("settings", False), ("settings", RuntimeError())):
            with self.subTest(target=target, result=type(result).__name__):
                store = FakeStore()
                bot = FakeJobBot(store)
                obj, method = (store, "save_profile") if target == "profile" else (bot.db, "save_user_settings")
                kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
                with patch.object(obj, method, **kwargs):
                    reply = self._delete(store, bot)
                self.assertIn("Не удалось подтвердить", reply)
                self.assertEqual(bot.db.events, [])

    def test_missing_legacy_row_is_idempotent_success(self):
        store = FakeStore()
        bot = FakeJobBot(store)
        self.assertIn("удалены", self._delete(store, bot))
        self.assertEqual(bot.db.events, [("resume_deleted", {})])

    def test_analytics_failure_does_not_fail_deletion(self):
        store = FakeStore()
        bot = FakeJobBot(store)
        with patch.object(bot.db, "log_event", side_effect=RuntimeError("sensitive")):
            reply = self._delete(store, bot)
        self.assertIn("удалены", reply)

    def test_pdf_and_docx_are_extracted_without_persistence(self):
        from types import SimpleNamespace
        from test_resume_documents import build_docx, build_pdf
        from test_resume_profile_flow import FakeBotApi
        resume = ("Junior Python developer builds backend services with FastAPI, "
                  "PostgreSQL and Docker, writes tests and reviews code. " * 3)
        for extension, mime, payload in (
            ("pdf", "application/pdf", build_pdf(resume)),
            ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", build_docx(resume)),
        ):
            with self.subTest(extension=extension):
                store = FakeStore()
                bot = FakeJobBot(store)
                doc = SimpleNamespace(file_id="test", file_name="cv." + extension, mime_type=mime, file_size=len(payload))
                message = FakeMessage(document=doc)
                context = SimpleNamespace(bot=FakeBotApi(payload))
                with patch.object(flow.CopilotStore, "from_database", return_value=store):
                    self.assertTrue(run(flow.handle_document(bot, make_update(message), context)))
                self.assertIn("не сохранены", message.replies[0][0])
                self.assertIn("python", message.replies[0][0].lower())
                self.assertEqual((store.resumes, store.profiles, bot.db.settings), ({}, [], {}))
