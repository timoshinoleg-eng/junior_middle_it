import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import copilot_webhook_runtime as copilot_runtime
import durable_product_state as durable
from message_formatter import build_copilot_url, copilot_enabled
from test_copilot_credit_operations import FakeGrowthStore


JOB = {
    "hash": "a" * 40,
    "title": "Junior Python Developer",
    "company": "Acme",
    "description": "Python, FastAPI, PostgreSQL. Опыт 1-2 года. Вопросы по вакансии пишите @hr_ivan",
    "url": "https://example.com/jobs/1",
    "source": "TG:@junior_dev",
}


class FakeDb:
    def __init__(self, job=None):
        self.job = job
        self.events = []
        self.saved = []
        # The credit store of the copilot. Without it the paid AI analysis
        # must not run at all.
        self._growth_store = None
        self.settings = {"skills": "python, fastapi", "enabled_categories": ["development"]}
        self.feed = [
            {
                "title": "Junior Python Developer",
                "description": "Python и FastAPI, удалённо",
                "salary_min_usd": 1200,
            },
            {
                "title": "DevOps Engineer",
                "description": "Kubernetes, Terraform, Docker",
            },
        ]

    def get_job_payload(self, job_hash):
        if self.job and str(job_hash) == str(self.job.get("hash")):
            return dict(self.job)
        return None

    def get_user_settings(self, user_id):
        return dict(self.settings)

    def save_user_settings(self, user_id, values):
        self.settings.update(values or {})

    def log_event(self, user_id, name, props=None):
        self.events.append((name, dict(props or {})))

    def create_saved_search(self, user_id, spec):
        self.saved.append(spec)
        return SimpleNamespace(id=len(self.saved))

    def recent_jobs_for_digest(self, hours=36, limit=80):
        return [dict(job) for job in self.feed[:limit]]


class FakeMessage:
    def __init__(self, text="", document=None, forward_date=None):
        self.replies = []
        self.text = text
        self.document = document
        self.forward_date = forward_date

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeQuery:
    def __init__(self, message):
        self.message = message
        self.data = ""
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)


class FakeUpdate:
    def __init__(self, message=None, query=None):
        self.effective_user = SimpleNamespace(id=4242)
        self.message = message
        self.callback_query = query


class FakeBot:
    def __init__(self, db):
        self.db = db


class CopilotRuntimeTests(unittest.TestCase):
    def test_module_installs_the_copilot_entry_points(self):
        self.assertTrue(hasattr(durable.JobBot, "cmd_copilot"))
        self.assertIsNot(durable.JobBot.cmd_start, copilot_runtime._original_cmd_start)
        self.assertIsNot(durable.JobBot.handle_callback, copilot_runtime._original_handle_callback)

    def test_deep_link_delivers_the_analysis(self):
        db = FakeDb(JOB)
        message = FakeMessage()
        update = FakeUpdate(message=message)
        context = SimpleNamespace(args=["copilot_" + JOB["hash"]])
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False):
            import asyncio

            asyncio.run(copilot_runtime._copilot_cmd_start(FakeBot(db), update, context))
        self.assertTrue(message.replies)
        text = message.replies[0][0]
        self.assertIn("fit", text.lower())
        self.assertTrue(any(name == "copilot_fit_viewed" for name, _props in db.events))

    def test_deep_link_is_ignored_when_the_feature_is_off(self):
        from unittest.mock import AsyncMock

        db = FakeDb(JOB)
        message = FakeMessage()
        update = FakeUpdate(message=message)
        context = SimpleNamespace(args=["copilot_" + JOB["hash"]])
        original = AsyncMock()
        with patch.dict(os.environ, {"COPILOT_ENABLED": "false"}, clear=False), patch.object(
            copilot_runtime, "_original_cmd_start", new=original
        ):
            import asyncio

            asyncio.run(copilot_runtime._copilot_cmd_start(FakeBot(db), update, context))
        self.assertEqual(message.replies, [])
        original.assert_awaited_once()

    def test_stale_card_is_reported_without_a_crash(self):
        db = FakeDb(None)
        message = FakeMessage()
        update = FakeUpdate(message=message)
        context = SimpleNamespace(args=["copilot_unknown"])
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False):
            import asyncio

            asyncio.run(copilot_runtime._copilot_cmd_start(FakeBot(db), update, context))
        self.assertTrue(message.replies)
        self.assertIn("устарела", message.replies[0][0])

    def test_hr_callback_sends_a_prefilled_link(self):
        import asyncio

        db = FakeDb(JOB)
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_hr:" + JOB["hash"]
        update = FakeUpdate(message=message, query=query)
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False):
            asyncio.run(copilot_runtime._copilot_handle_callback(FakeBot(db), update, SimpleNamespace()))
        self.assertTrue(message.replies)
        self.assertIn("t.me/hr_ivan", message.replies[0][0])
        self.assertTrue(any(name == "copilot_hr_link_clicked" for name, _props in db.events))

    def test_save_callback_creates_a_saved_search(self):
        import asyncio

        db = FakeDb(JOB)
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_save:" + JOB["hash"]
        update = FakeUpdate(message=message, query=query)
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False):
            asyncio.run(copilot_runtime._copilot_handle_callback(FakeBot(db), update, SimpleNamespace()))
        self.assertEqual(len(db.saved), 1)
        self.assertTrue(any(name == "copilot_search_saved" for name, _props in db.events))

    def test_unknown_copilot_callback_is_acknowledged(self):
        import asyncio

        db = FakeDb(JOB)
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_unknown"
        update = FakeUpdate(message=message, query=query)
        asyncio.run(copilot_runtime._copilot_handle_callback(FakeBot(db), update, SimpleNamespace()))
        self.assertTrue(query.answers)

    def test_events_never_carry_resume_content(self):
        import asyncio

        db = FakeDb(JOB)
        message = FakeMessage()
        update = FakeUpdate(message=message)
        context = SimpleNamespace(args=["copilot_" + JOB["hash"]])
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False):
            asyncio.run(copilot_runtime._copilot_cmd_start(FakeBot(db), update, context))
        for _name, props in db.events:
            for key in props:
                self.assertNotIn("resume", key.lower())
                self.assertNotIn("cv", key.lower())


class CopilotAIRuntimeTests(unittest.TestCase):
    """The AI layer only runs behind its own button."""

    def _result(self, ok=True, error=""):
        from copilot import llm as copilot_llm

        if not ok:
            return copilot_llm.LLMResult(ok=False, error=error)
        return copilot_llm.LLMResult(
            ok=True,
            summary="Нормальная junior-вакансия",
            sections={
                "strengths": ["Совпадает Python"],
                "gaps": [],
                "risks": [],
                "next_steps": [],
                "questions": [],
            },
            model="test-model",
        )

    def test_ai_callback_is_refused_while_the_layer_is_off(self):
        import asyncio

        db = FakeDb(JOB)
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_ai:" + JOB["hash"]
        update = FakeUpdate(message=message, query=query)
        env = {"COPILOT_ENABLED": "true", "COPILOT_LLM_ENABLED": "false"}
        with patch.dict(os.environ, env, clear=False):
            asyncio.run(copilot_runtime._copilot_handle_callback(FakeBot(db), update, SimpleNamespace()))
        self.assertEqual(message.replies, [])
        self.assertTrue(query.answers)

    def test_ai_callback_runs_the_model_and_answers(self):
        import asyncio

        db = FakeDb(JOB)
        db._growth_store = FakeGrowthStore()
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_ai:" + JOB["hash"]
        update = FakeUpdate(message=message, query=query)
        calls = []

        def fake_analyze(job, skills, deterministic, resume_text, **kwargs):
            calls.append(job)
            return self._result()

        env = {
            "COPILOT_ENABLED": "true",
            "COPILOT_LLM_ENABLED": "true",
            "COPILOT_LLM_API_KEY": "test-key",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            copilot_runtime.copilot_llm, "analyze", new=fake_analyze
        ):
            asyncio.run(copilot_runtime._copilot_handle_callback(FakeBot(db), update, SimpleNamespace()))
        self.assertTrue(calls)
        self.assertTrue(message.replies)
        self.assertIn("AI-разбор", message.replies[0][0])
        self.assertTrue(any(name == "copilot_ai_viewed" for name, _props in db.events))

    def test_without_a_credit_store_the_paid_model_is_not_called(self):
        """The credit store is missing: the model must not be called at all.

        A paid call that nobody is charged for is a free service for the
        operator, so the request is refused with the free report instead.
        """
        import asyncio

        db = FakeDb(JOB)
        db._growth_store = None
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_ai:" + JOB["hash"]
        update = FakeUpdate(message=message, query=query)
        calls = []

        def fake_analyze(job, skills, deterministic, resume_text, **kwargs):
            calls.append(job)
            return self._result()

        env = {
            "COPILOT_ENABLED": "true",
            "COPILOT_LLM_ENABLED": "true",
            "COPILOT_LLM_API_KEY": "test-key",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            copilot_runtime.copilot_llm, "analyze", new=fake_analyze
        ):
            asyncio.run(copilot_runtime._copilot_handle_callback(FakeBot(db), update, SimpleNamespace()))
        self.assertEqual(calls, [])
        self.assertTrue(message.replies)
        self.assertIn("без нейросети", message.replies[0][0])
        self.assertTrue(any(name == "copilot_ai_no_store" for name, _props in db.events))
        self.assertFalse(any(name == "copilot_ai_viewed" for name, _props in db.events))

    def test_ai_failure_falls_back_to_the_deterministic_report(self):
        import asyncio

        db = FakeDb(JOB)
        db._growth_store = FakeGrowthStore()
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_ai:" + JOB["hash"]
        update = FakeUpdate(message=message, query=query)

        def fake_analyze(job, skills, deterministic, resume_text, **kwargs):
            return self._result(ok=False, error="http_500")

        env = {
            "COPILOT_ENABLED": "true",
            "COPILOT_LLM_ENABLED": "true",
            "COPILOT_LLM_API_KEY": "test-key",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            copilot_runtime.copilot_llm, "analyze", new=fake_analyze
        ):
            asyncio.run(copilot_runtime._copilot_handle_callback(FakeBot(db), update, SimpleNamespace()))
        self.assertTrue(message.replies)
        self.assertIn("не получился", message.replies[0][0])
        self.assertIn("fit", message.replies[0][0].lower())
        self.assertTrue(any(name == "copilot_ai_failed" for name, _props in db.events))

    def test_ai_button_is_only_offered_when_the_layer_is_ready(self):
        with patch.dict(os.environ, {"COPILOT_LLM_ENABLED": "false"}, clear=False):
            self.assertFalse(copilot_runtime.ai_enabled())
        with patch.dict(
            os.environ,
            {"COPILOT_LLM_ENABLED": "true", "COPILOT_LLM_API_KEY": ""},
            clear=False,
        ):
            self.assertFalse(copilot_runtime.ai_enabled())
        with patch.dict(
            os.environ,
            {"COPILOT_LLM_ENABLED": "true", "COPILOT_LLM_API_KEY": "test-key"},
            clear=False,
        ):
            self.assertTrue(copilot_runtime.ai_enabled())

    def test_ai_callback_is_present_in_the_keyboard(self):
        markup = copilot_runtime.analysis_keyboard(
            JOB["hash"], {"hr_handle": None}, can_ai=True
        )
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn("copilot_ai:" + JOB["hash"], callbacks)

    def test_ai_events_never_carry_resume_content(self):
        import asyncio

        db = FakeDb(JOB)
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_ai:" + JOB["hash"]
        update = FakeUpdate(message=message, query=query)

        def fake_analyze(job, skills, deterministic, resume_text, **kwargs):
            return self._result()

        env = {
            "COPILOT_ENABLED": "true",
            "COPILOT_LLM_ENABLED": "true",
            "COPILOT_LLM_API_KEY": "test-key",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            copilot_runtime.copilot_llm, "analyze", new=fake_analyze
        ):
            asyncio.run(copilot_runtime._copilot_handle_callback(FakeBot(db), update, SimpleNamespace()))
        for _name, props in db.events:
            for key in props:
                self.assertNotIn("resume", key.lower())
                self.assertNotIn("cv", key.lower())


class CopilotForwardRuntimeTests(unittest.TestCase):
    """Forward-to-Analyze never swallows commands, documents or resumes."""

    VACANCY = (
        "Junior Python Developer\n"
        "Acme, remote\n"
        "Python, FastAPI, PostgreSQL. Опыт 1-2 года. "
        "Вопросы по вакансии пишите @hr_ivan https://example.com/jobs/1"
    )

    def _run(self, message, env=None, context=None):
        import asyncio
        from unittest.mock import AsyncMock

        db = FakeDb(JOB)
        update = FakeUpdate(message=message)
        original = AsyncMock()
        environment = {
            "COPILOT_ENABLED": "true",
            "COPILOT_FORWARD_ENABLED": "true",
        }
        environment.update(env or {})
        with patch.dict(os.environ, environment, clear=False), patch.object(
            copilot_runtime, "_original_handle_setup_text", new=original
        ):
            asyncio.run(
                copilot_runtime._copilot_handle_setup_text(
                    FakeBot(db), update, context or SimpleNamespace()
                )
            )
        return db, message, original
    def test_forwarded_vacancy_is_analyzed(self):
        from datetime import datetime, timezone

        message = FakeMessage(
            text=self.VACANCY, forward_date=datetime.now(timezone.utc)
        )
        db, message, original = self._run(message)
        self.assertTrue(message.replies)
        self.assertIn("Разбор вакансии", message.replies[0][0])
        self.assertTrue(any(name == "copilot_forward_analyzed" for name, _ in db.events))
        original.assert_not_awaited()

    def test_command_is_not_consumed(self):
        message = FakeMessage(text="/setup python fastapi")
        db, message, original = self._run(message)
        self.assertEqual(message.replies, [])
        original.assert_awaited_once()

    def test_document_is_not_consumed(self):
        message = FakeMessage(text="", document=object())
        db, message, original = self._run(message)
        self.assertEqual(message.replies, [])
        original.assert_awaited_once()

    def test_plain_resume_without_selected_vacancy_explains_next_step(self):
        import asyncio
        from unittest.mock import AsyncMock

        resume_text = (
            "Резюме: Python разработчик. Опыт работы 3 года, "
            "образование техническое, мои навыки Python и PostgreSQL. "
            "Оптимизировал обработку заказов в команде разработки."
        )
        message = FakeMessage(text=resume_text)
        bot = FakeBot(FakeDb(JOB))
        update = FakeUpdate(message=message)
        original = AsyncMock()
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False), patch.object(
            copilot_runtime, "_original_handle_setup_text", new=original
        ):
            asyncio.run(copilot_runtime._copilot_handle_setup_text(
                bot, update, SimpleNamespace()
            ))
        self.assertEqual(len(message.replies), 1)
        self.assertIn("вакансия не выбрана", message.replies[0][0])
        self.assertEqual(
            message.replies[0][1]["reply_markup"].inline_keyboard[0][0].callback_data,
            "growth_quick_fresh",
        )
        original.assert_not_awaited()
        self.assertFalse(bot.db.events)

    def test_resume_match_session_keeps_own_text_handler(self):
        import asyncio
        from unittest.mock import AsyncMock

        message = FakeMessage(
            text="Опыт работы 3 года. Образование, Python, FastAPI и PostgreSQL. "
                 "Резюме, опыт работы над проектами, мои навыки в тестировании."
        )
        bot = FakeBot(FakeDb(JOB))
        bot.resume_sessions = {4242: {"job_hash": JOB["hash"], "job": JOB}}
        update = FakeUpdate(message=message)
        original = AsyncMock()
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False), patch.object(
            copilot_runtime, "_original_handle_setup_text", new=original
        ):
            asyncio.run(copilot_runtime._copilot_handle_setup_text(
                bot, update, SimpleNamespace()
            ))
        original.assert_awaited_once()
        self.assertEqual(message.replies, [])

    def test_setup_wizard_keeps_own_text_handler(self):
        import asyncio
        from unittest.mock import AsyncMock

        message = FakeMessage(text="Резюме и опыт работы и обучение, профессиональный опыт.")
        bot = FakeBot(FakeDb(JOB))
        bot.setup_steps = {4242: "skills"}
        original = AsyncMock()
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False), patch.object(
            copilot_runtime, "_original_handle_setup_text", new=original
        ):
            asyncio.run(copilot_runtime._copilot_handle_setup_text(
                bot, FakeUpdate(message=message), SimpleNamespace()
            ))
        original.assert_awaited_once()

    def test_resume_forward_is_refused(self):
        from datetime import datetime, timezone

        message = FakeMessage(
            text="Резюме\nО себе: python, fastapi\nЗарплатные ожидания: 2000 USD",
            forward_date=datetime.now(timezone.utc),
        )
        db, message, original = self._run(message)
        self.assertTrue(message.replies)
        self.assertIn("резюме", message.replies[0][0].lower())
        self.assertFalse(any(name == "copilot_forward_analyzed" for name, _ in db.events))

    def test_forward_is_off_by_default(self):
        from datetime import datetime, timezone

        message = FakeMessage(
            text=self.VACANCY, forward_date=datetime.now(timezone.utc)
        )
        db, message, original = self._run(
            message, env={"COPILOT_FORWARD_ENABLED": "false"}
        )
        self.assertEqual(message.replies, [])
        original.assert_awaited_once()

    def test_the_same_post_gets_the_same_hash(self):
        from datetime import datetime, timezone

        first = FakeMessage(text=self.VACANCY, forward_date=datetime.now(timezone.utc))
        second = FakeMessage(text=self.VACANCY + " ", forward_date=datetime.now(timezone.utc))
        db_one, message_one, _ = self._run(first)
        db_two, message_two, _ = self._run(second)
        hash_one = db_one.events[-1][1]["hash"]
        hash_two = db_two.events[-1][1]["hash"]
        self.assertEqual(hash_one, hash_two)

    def test_setup_wizard_keeps_the_text_input(self):
        import asyncio
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock

        message = FakeMessage(
            text=self.VACANCY, forward_date=datetime.now(timezone.utc)
        )
        bot = FakeBot(FakeDb(JOB))
        bot.setup_steps = {4242: "skills"}
        db = bot.db
        update = FakeUpdate(message=message)
        original = AsyncMock()
        env = {"COPILOT_ENABLED": "true", "COPILOT_FORWARD_ENABLED": "true"}
        with patch.dict(os.environ, env, clear=False), patch.object(
            copilot_runtime, "_original_handle_setup_text", new=original
        ):
            asyncio.run(
                copilot_runtime._copilot_handle_setup_text(
                    bot, update, SimpleNamespace()
                )
            )
        self.assertEqual(message.replies, [])
        original.assert_awaited_once()
        self.assertFalse(any(name == "copilot_forward_analyzed" for name, _ in db.events))


class CopilotOutcomeRuntimeTests(unittest.TestCase):
    def test_applied_callback_without_a_database_is_honest(self):
        import asyncio

        db = FakeDb(JOB)
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_applied:" + JOB["hash"]
        update = FakeUpdate(message=message, query=query)
        env = {"COPILOT_ENABLED": "true", "COPILOT_OUTCOME_ENABLED": "true"}
        with patch.dict(os.environ, env, clear=False):
            asyncio.run(
                copilot_runtime._copilot_handle_callback(
                    FakeBot(db), update, SimpleNamespace()
                )
            )
        self.assertTrue(query.answers)
        self.assertIn("баз", " ".join(str(item) for item in query.answers).lower())

    def test_outcome_callback_is_ignored_while_the_tracker_is_off(self):
        import asyncio

        db = FakeDb(JOB)
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_outcome:interview:" + JOB["hash"]
        update = FakeUpdate(message=message, query=query)
        env = {"COPILOT_ENABLED": "true", "COPILOT_OUTCOME_ENABLED": "false"}
        with patch.dict(os.environ, env, clear=False):
            asyncio.run(
                copilot_runtime._copilot_handle_callback(
                    FakeBot(db), update, SimpleNamespace()
                )
            )
        self.assertTrue(query.answers)
        self.assertEqual(message.replies, [])

    def test_outcome_callback_is_parsed(self):
        from copilot import outcome

        stage, job_hash = outcome.stage_from_callback(
            "copilot_outcome:no_response:" + JOB["hash"]
        )
        self.assertEqual(stage, "no_response")
        self.assertEqual(job_hash, JOB["hash"])


class CopilotUpskillRuntimeTests(unittest.TestCase):
    def test_upskill_command_reports_the_feed(self):
        import asyncio

        db = FakeDb(JOB)
        message = FakeMessage()
        update = FakeUpdate(message=message)
        env = {"COPILOT_ENABLED": "true", "COPILOT_UPSKILL_ENABLED": "true"}
        with patch.dict(os.environ, env, clear=False):
            asyncio.run(
                copilot_runtime._cmd_upskill(FakeBot(db), update, SimpleNamespace())
            )
        self.assertTrue(message.replies)
        self.assertIn("upskill-радар", message.replies[0][0])
        self.assertTrue(any(name == "copilot_upskill_viewed" for name, _ in db.events))

    def test_upskill_command_is_off_by_default(self):
        import asyncio

        db = FakeDb(JOB)
        message = FakeMessage()
        update = FakeUpdate(message=message)
        env = {"COPILOT_ENABLED": "true", "COPILOT_UPSKILL_ENABLED": "false"}
        with patch.dict(os.environ, env, clear=False):
            asyncio.run(
                copilot_runtime._cmd_upskill(FakeBot(db), update, SimpleNamespace())
            )
        self.assertTrue(message.replies)
        self.assertIn("выключен", message.replies[0][0])

    def test_share_link_carries_the_campaign_payload(self):
        from urllib.parse import unquote

        env = {"BOT_USERNAME": "junior_jobs_channel_bot"}
        with patch.dict(os.environ, env, clear=False):
            url = copilot_runtime._upskill_share_url(4242)
        self.assertIn("t.me/share/url", url)
        self.assertIn("start=upskill_4242", unquote(url))

    def test_share_link_needs_a_bot_username(self):
        with patch.dict(os.environ, {"BOT_USERNAME": ""}, clear=False):
            self.assertEqual(copilot_runtime._upskill_share_url(4242), "")


class ChannelButtonTests(unittest.TestCase):
    def test_copilot_url_uses_the_deep_link(self):
        url = build_copilot_url(JOB, bot_username="junior_jobs_channel_bot")
        self.assertEqual(url, f"https://t.me/junior_jobs_channel_bot?start=copilot_{JOB['hash']}")

    def test_copilot_url_without_bot_or_hash_is_empty(self):
        self.assertEqual(build_copilot_url(JOB, bot_username=""), "")
        self.assertEqual(build_copilot_url({}, bot_username="bot"), "")

    def test_button_is_hidden_while_the_feature_is_off(self):
        with patch.dict(os.environ, {"COPILOT_ENABLED": "false"}, clear=False):
            self.assertFalse(copilot_enabled())
        with patch.dict(os.environ, {"COPILOT_ENABLED": "true"}, clear=False):
            self.assertTrue(copilot_enabled())


if __name__ == "__main__":
    unittest.main()
