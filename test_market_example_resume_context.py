"""Issue #82: market fallback provenance must reach Resume Match without leaking CV data."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import localized_product_runtime as product
import public_acquisition_runtime as acquisition


HASH = "a" * 40
JOB = {"hash": HASH, "title": "Example Security Engineer", "company": "Example",
       "category": "security", "url": "https://example.com/jobs/demo"}


class FakeDurableDB:
    def __init__(self):
        self.data = {}

    def set_runtime_state(self, user_id, key, state, *, ttl_seconds):
        self.data[(user_id, key)] = dict(state)

    def get_runtime_state(self, user_id, key):
        return self.data.get((user_id, key))

    def delete_runtime_state(self, user_id, key):
        self.data.pop((user_id, key), None)

    def get_job_payload(self, job_hash):
        return dict(JOB) if job_hash == HASH else None

    def log_event(self, *_args):
        pass


class MarketResumeTests(unittest.IsolatedAsyncioTestCase):
    def test_market_card_keyboard_separates_examples_from_matches(self):
        bot = object.__new__(product.JobBot)
        market = bot._job_keyboard(dict(JOB), "ru", market_example=True)
        actions = [b.callback_data for row in market.inline_keyboard for b in row if b.callback_data]
        self.assertIn("resume_market:" + HASH, actions)
        self.assertIn("growth_quick_setup", actions)
        self.assertLessEqual(len("resume_market:" + HASH), 64)

        regular = bot._job_keyboard(dict(JOB), "ru")
        regular_actions = [b.callback_data for row in regular.inline_keyboard for b in row if b.callback_data]
        self.assertIn("resume_match:" + HASH, regular_actions)
        self.assertNotIn("resume_market:" + HASH, regular_actions)
        self.assertNotIn("growth_quick_setup", regular_actions)

    async def test_market_callback_warns_and_sets_provenance(self):
        bot = object.__new__(product.JobBot)
        bot.db = FakeDurableDB()
        bot.resume_sessions = {}
        bot._lang = lambda _uid: "ru"
        bot._resume_prompt_for_user = lambda _uid, _job: "SEND YOUR RESUME"
        q = SimpleNamespace(
            data="resume_market:" + HASH,
            answer=AsyncMock(),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        update = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=77))
        await bot.handle_callback(update, SimpleNamespace())
        self.assertTrue(bot.resume_sessions[77]["market_example"])
        self.assertIn("вне твоих фильтров", q.message.reply_text.await_args.args[0])
        q.answer.assert_awaited_once()

    async def test_regular_callback_is_not_falsely_marked(self):
        bot = object.__new__(product.JobBot)
        bot.db = FakeDurableDB()
        bot.resume_sessions = {}
        bot._lang = lambda _uid: "ru"
        bot._resume_prompt_for_user = lambda _uid, _job: "SEND YOUR RESUME"
        q = SimpleNamespace(data="resume_match:" + HASH, answer=AsyncMock(),
                            message=SimpleNamespace(reply_text=AsyncMock()))
        await bot.handle_callback(
            SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=77)),
            SimpleNamespace(),
        )
        self.assertFalse(bot.resume_sessions[77]["market_example"])
        self.assertEqual(q.message.reply_text.await_args.args[0], "SEND YOUR RESUME")

    async def test_market_report_warns_without_changing_fit_score(self):
        bot = object.__new__(product.JobBot)
        bot.db = FakeDurableDB()
        bot.resume_sessions = {77: {"job_hash": HASH, "job": dict(JOB), "market_example": True}}
        bot.pending_saved_searches = {}
        bot._lang = lambda _uid: "ru"
        bot._format_resume_report = lambda *_args: "Resume Match: 0%"
        update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
        report = SimpleNamespace(matched_skills=[], required_skills=[], score=0)
        with patch.object(product, "analyze_resume_match", return_value=report), patch.object(
            product, "suggested_search_from_resume", return_value={"name": "test"}
        ):
            await bot._complete_resume(update, 77, "synthetic text", "text")
        result = update.message.reply_text.await_args.args[0]
        self.assertIn("вне твоих фильтров", result)
        self.assertIn("Resume Match: 0%", result)
        self.assertNotIn(77, bot.resume_sessions)

    def test_market_flag_survives_durable_session_round_trip(self):
        db = FakeDurableDB()
        with patch.object(acquisition.referral.JobBot, "__init__", return_value=None):
            bot = acquisition.JobBot(None, db)
        bot.resume_sessions[77] = {"job_hash": HASH, "job": dict(JOB),
                                   "market_example": True}
        raw = db.data[(77, "resume_session")]
        self.assertEqual(raw, {"job_hash": HASH, "market_example": True})
        restored = bot.resume_sessions[77]
        self.assertTrue(restored["market_example"])
        self.assertEqual(restored["job"]["hash"], HASH)


if __name__ == "__main__":
    unittest.main()
