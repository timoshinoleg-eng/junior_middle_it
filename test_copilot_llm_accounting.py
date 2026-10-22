"""The accounting of the model calls: the five cases of one LLM analysis.

The review asked for a model that tells the five cases of a paid analysis
apart:

* the call of the provider - it costs the operator, even when it fails;
* the charge of the user - it happens once, only for a shown answer;
* the answer from the cache - it costs nothing and calls nobody;
* the repeat after the cache expired - it calls the model again and charges
  the user nothing, because the vacancy was paid for already;
* the failed attempt - it costs the operator a call and charges the user
  nothing.

On top of that the slot of the global budget is taken before the call, so
parallel taps cannot pass the budget, and the analysis slot of the vacancy is
claimed, so two parallel taps of the same vacancy cannot call the model
twice and cannot charge the user twice.
"""
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import copilot_webhook_runtime as copilot_runtime
from copilot import credit_operations as credit_ops
from copilot import credits as copilot_credits

from test_copilot_credit_operations import FakeGrowthStore, JOB
from test_copilot_runtime import FakeBot, FakeDb, FakeMessage, FakeQuery, FakeUpdate


# The user of the runtime tests.
USER_ID = 4242


def _result(ok=True, error=""):
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


class LLMAccountingTests(unittest.TestCase):
    """One LLM analysis and its five cases."""

    def setUp(self):
        patcher = patch.dict(
            os.environ,
            {
                "COPILOT_ENABLED": "true",
                "COPILOT_LLM_ENABLED": "true",
                "COPILOT_LLM_API_KEY": "test-key",
                "COPILOT_FREE_PER_DAY": "0",
                "COPILOT_LLM_DAILY_BUDGET": "200",
            },
            clear=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.backend = FakeGrowthStore()
        self.backend.initial_balance = 3
        self.calls = []

    def _analyze(self, ok=True, error=""):
        def fake_analyze(job, skills, deterministic, resume_text, **kwargs):
            self.calls.append(job)
            return _result(ok=ok, error=error)

        return fake_analyze

    def _tap(self, *, ok=True, error="", backend=None, patched=True):
        """One tap on the AI button.

        The provider is patched by the caller, so two threads can tap at the
        same moment without patching the module twice.
        """
        import asyncio

        backend = backend or self.backend
        db = FakeDb(JOB)
        db._growth_store = backend
        message = FakeMessage()
        query = FakeQuery(message)
        query.data = "copilot_ai:" + JOB["hash"]
        update = FakeUpdate(message=message, query=query)
        if patched:
            with patch.object(
                copilot_runtime.copilot_llm,
                "analyze",
                new=self._analyze(ok=ok, error=error),
            ):
                asyncio.run(
                    copilot_runtime._copilot_handle_callback(
                        FakeBot(db), update, SimpleNamespace()
                    )
                )
        else:
            asyncio.run(
                copilot_runtime._copilot_handle_callback(
                    FakeBot(db), update, SimpleNamespace()
                )
            )
        return message, query, db

    def _reasons(self, backend=None):
        backend = backend or self.backend
        return [reason for _uid, _delta, reason, _ref in backend.events]

    def _text(self, message, query):
        """Everything the user got: the answer of the button and the text."""
        return " ".join(
            [text for text, _kwargs in message.replies] + list(query.answers)
        )

    # -- the call of the provider ----------------------------------
    def test_ai_proven_delivery_rejection_releases_credit(self):
        from telegram.error import BadRequest
        failure = BadRequest("message rejected")
        with patch.object(FakeMessage, "reply_text", side_effect=failure):
            with self.assertRaises(BadRequest) as caught:
                self._tap()
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.backend.ops[(USER_ID, JOB["hash"], "llm_call")]["state"], "released")
        self.assertEqual(self.backend._balance(USER_ID), 3)
        self.assertEqual(self._reasons().count(credit_ops.REASON_LLM_SPEND), 1)
        self.assertEqual(self._reasons().count(credit_ops.REASON_LLM_RELEASE), 1)
        self.assertNotIn(credit_ops.REASON_LLM_UNKNOWN, self._reasons())
        self.assertEqual(self.backend.ai_reports, {})

    def test_ai_ambiguous_delivery_keeps_credit_unknown(self):
        failure = TimeoutError("delivery timeout")
        with patch.object(FakeMessage, "reply_text", side_effect=failure):
            with self.assertRaises(TimeoutError) as caught:
                self._tap()
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.backend.ops[(USER_ID, JOB["hash"], "llm_call")]["state"], "unknown")
        self.assertEqual(self.backend._balance(USER_ID), 2)
        self.assertEqual(self._reasons().count(credit_ops.REASON_LLM_UNKNOWN), 1)
        self.assertNotIn(credit_ops.REASON_LLM_RELEASE, self._reasons())
        self.assertEqual(self.backend.ai_reports, {})

    def test_ai_delivered_answer_confirms_one_spend(self):
        message, query, db = self._tap()
        self.assertIn(_result().as_text(), self._text(message, query))
        self.assertEqual(self.backend.ops[(USER_ID, JOB["hash"], "llm_call")]["state"], "delivered")
        self.assertEqual(self.backend._balance(USER_ID), 2)
        self.assertEqual(self._reasons().count(credit_ops.REASON_LLM_SPEND), 1)
        self.assertEqual(self._reasons().count(credit_ops.REASON_LLM_DELIVERED), 1)
        self.assertNotIn(credit_ops.REASON_LLM_RELEASE, self._reasons())

    def test_retry_after_ai_refund_does_not_start_another_paid_attempt(self):
        from telegram.error import BadRequest
        with patch.object(FakeMessage, "reply_text", side_effect=BadRequest("message rejected")):
            with self.assertRaises(BadRequest):
                self._tap()
        before = list(self.backend.events)
        message, query, db = self._tap()
        self.assertIn("обычный разбор без нейросети", self._text(message, query))
        self.assertNotIn(_result().as_text(), self._text(message, query))
        self.assertEqual(self.backend.ops[(USER_ID, JOB["hash"], "llm_call")]["state"], "released")
        self.assertEqual(self.backend._balance(USER_ID), 3)
        self.assertEqual(self.backend.events, before)
        self.assertEqual(len(self.calls), 1)

    def test_takeover_after_reservation_sends_no_ai_answer_or_settlement(self):
        original = credit_ops.CostedOperation.reserve

        def reserve_then_takeover(operation):
            state = original(operation)
            self.backend.advance(minutes=20)
            operation._store.claim_ai_report(operation._user_id, JOB["hash"])
            return state

        with patch.object(credit_ops.CostedOperation, "reserve", reserve_then_takeover):
            message, _query, db = self._tap()
        self.assertEqual(len(message.replies), 1)
        self.assertEqual(message.replies[0][0],
            "AI-разбор уже выполняется или был прерван. Попробуй позже.\n\n"
            + copilot_runtime.format_analysis(JOB, copilot_runtime.analyse_job(
                JOB, copilot_runtime._profile_from_settings(db.get_user_settings(USER_ID))
            )))
        self.assertNotIn(_result().as_text(), message.replies[0][0])
        self.assertTrue(self.backend.ai_reports)
        self.assertNotIn(credit_ops.REASON_LLM_RELEASE, self._reasons())
        self.assertNotIn(credit_ops.REASON_LLM_DELIVERED, self._reasons())
        self.assertNotIn(credit_ops.REASON_LLM_UNKNOWN, self._reasons())
        self.assertFalse(any(name == "copilot_ai_viewed" for name, _props in db.events))

    def test_confirm_refusal_keeps_answer_cache_and_view_event(self):
        with patch.object(credit_ops.CostedOperation, "confirm",
                          side_effect=credit_ops.CreditStoreUnavailable("refused")), \
             self.assertLogs(credit_ops.logger, level="WARNING") as logs:
            message, query, db = self._tap()
        self.assertIn(_result().as_text(), self._text(message, query))
        self.assertTrue(any(row.get("answer") == _result().as_text()
                            for row in self.backend.ai_reports.values()))
        self.assertTrue(any(name == "copilot_ai_viewed" for name, _ in db.events))
        self.assertEqual(self.backend._balance(USER_ID), 2)
        self.assertIn("CreditStoreUnavailable", " ".join(logs.output))

    def test_unknown_refusal_preserves_delivery_exception_and_releases_claim(self):
        failure = RuntimeError("delivery failed")
        with patch.object(FakeMessage, "reply_text", side_effect=failure), \
             patch.object(credit_ops.CostedOperation, "mark_unknown",
                          side_effect=credit_ops.CreditStoreUnavailable("refused")):
            with self.assertRaises(RuntimeError) as caught:
                self._tap()
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.backend.ai_reports, {})
        self.assertEqual(self.backend._balance(USER_ID), 2)

    def test_failed_delivery_after_takeover_keeps_new_owners_claim(self):
        from copilot.store import CopilotStore
        failure = RuntimeError("delivery failed")
        replacement = []

        async def fail_after_takeover(message, text, **kwargs):
            self.backend.advance(minutes=20)
            db = FakeDb(JOB)
            db._growth_store = self.backend
            store = CopilotStore.from_database(db)
            replacement.append(store.claim_ai_report(USER_ID, JOB["hash"]))
            raise failure

        with patch.object(FakeMessage, "reply_text", new=fail_after_takeover):
            with self.assertRaises(RuntimeError) as caught:
                self._tap()
        self.assertIs(caught.exception, failure)
        self.assertTrue(replacement[0])
        self.assertEqual(self.backend.ai_reports[(USER_ID, JOB["hash"])]["claim"], replacement[0])
        self.assertEqual(self.backend._balance(USER_ID), 2)
        self.assertNotIn(credit_ops.REASON_LLM_RELEASE, self._reasons())

    def test_ownership_refusal_releases_own_claim_and_shows_fallback(self):
        with patch.object(credit_ops.CostedOperation, "can_deliver", return_value=False):
            message, query, db = self._tap()
        self.assertIn("AI-разбор уже выполняется или был прерван. Попробуй позже.",
                      self._text(message, query))
        self.assertNotIn(_result().as_text(), self._text(message, query))
        self.assertEqual(self.backend.ai_reports, {})
        self.assertNotIn(credit_ops.REASON_LLM_RELEASE, self._reasons())

    def test_paid_report_at_zero_balance_uses_provider_budget_without_spending(self):
        for state in ("delivered", "unknown"):
            with self.subTest(state=state):
                self.backend.seed_credits(USER_ID, 0)
                self.backend.ai_reports.clear()
                self.backend.ops[(USER_ID, JOB["hash"], "llm_call")] = {
                    "state": state, "attempt": 1, "amount": 1, "clock": self.backend.clock}
                before = self._reasons().count(copilot_credits.REASON_LLM_ATTEMPT)
                message, query, db = self._tap()
                self.assertIn(_result().as_text(), self._text(message, query))
                self.assertEqual(self.backend._balance(USER_ID), 0)
                self.assertEqual(self._reasons().count(copilot_credits.REASON_LLM_ATTEMPT), before + 1)
                self.assertNotIn(credit_ops.REASON_LLM_SPEND, self._reasons())

    def test_paid_report_at_zero_balance_with_exhausted_budget_shows_fallback(self):
        self.backend.seed_credits(USER_ID, 0)
        self.backend.ops[(USER_ID, JOB["hash"], "llm_call")] = {
            "state": "delivered", "attempt": 1, "amount": 1, "clock": 0}
        with patch.dict(os.environ, {"COPILOT_LLM_DAILY_BUDGET": "0"}):
            message, query, db = self._tap()
        self.assertEqual(self.calls, [])
        self.assertIn("обычный разбор без нейросети", self._text(message, query))
        self.assertEqual(self.backend._balance(USER_ID), 0)
        self.assertEqual(self.backend.events, [])

    def test_unknown_registry_state_does_not_call_provider(self):
        with patch.object(copilot_runtime.CopilotStore, "credit_operation_state", return_value="invalid"):
            message, query, db = self._tap()
        self.assertEqual(self.calls, [])
        self.assertIn("Не удалось проверить кредиты", self._text(message, query))
        self.assertEqual(self.backend.events, [])

    def test_unpaid_report_at_zero_balance_keeps_free_limit_message(self):
        self.backend.seed_credits(USER_ID, 0)
        message, query, db = self._tap()
        self.assertIn("Бесплатные AI-разборы на сегодня закончились", self._text(message, query))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.backend.events, [])

    def test_abandoned_paid_report_at_zero_balance_is_adopted(self):
        self.backend.seed_credits(USER_ID, 0)
        self.backend.ops[(USER_ID, JOB["hash"], "llm_call")] = {
            "state": "reserved", "attempt": 3, "amount": 1, "clock": 0}
        self.backend.advance(minutes=20)
        message, query, db = self._tap()
        self.assertIn(_result().as_text(), self._text(message, query))
        self.assertEqual(self.backend.ops[(USER_ID, JOB["hash"], "llm_call")]["state"], "delivered")
        self.assertEqual(self.backend._balance(USER_ID), 0)
        self.assertEqual(self._reasons().count(copilot_credits.REASON_LLM_ATTEMPT), 1)
        self.assertNotIn(credit_ops.REASON_LLM_SPEND, self._reasons())

    def test_the_call_of_the_model_is_counted_and_charged_once(self):
        message, _query, _db = self._tap()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("AI-разбор", self._text(message, _query))
        # The operator paid for one call and the user for one credit.
        self.assertEqual(self._reasons().count(copilot_credits.REASON_LLM_ATTEMPT), 1)
        self.assertEqual(self._reasons().count(credit_ops.REASON_LLM_SPEND), 1)
        self.assertEqual(self._reasons().count(credit_ops.REASON_LLM_DELIVERED), 1)
        self.assertEqual(self.backend._balance(USER_ID), 2)

    def test_a_failed_call_costs_the_operator_but_not_the_user(self):
        message, _query, db = self._tap(ok=False, error="http_500")
        self.assertEqual(len(self.calls), 1)
        self.assertIn("не получился", self._text(message, _query))
        # The call was made, so the budget paid for it.
        self.assertEqual(self._reasons().count(copilot_credits.REASON_LLM_ATTEMPT), 1)
        # The user gets nothing, so the user pays nothing.
        self.assertEqual(self._reasons().count(credit_ops.REASON_LLM_SPEND), 0)
        self.assertEqual(self.backend._balance(USER_ID), 3)
        self.assertTrue(any(name == "copilot_ai_failed" for name, _props in db.events))

    def test_a_failed_call_leaves_no_report_behind(self):
        self._tap(ok=False, error="http_500")
        self.assertEqual(self.backend.ai_reports, {})
        # The user may try again right away.
        message, _query, _db = self._tap()
        self.assertEqual(len(self.calls), 2)
        self.assertIn("AI-разбор", self._text(message, _query))

    # -- the answer from the cache ----------------------------------
    def test_the_second_tap_answers_from_the_cache_and_costs_nothing(self):
        self._tap()
        message, _query, db = self._tap()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("AI-разбор", self._text(message, _query))
        self.assertTrue(any(name == "copilot_ai_cached" for name, _props in db.events))
        self.assertEqual(self._reasons().count(copilot_credits.REASON_LLM_ATTEMPT), 1)
        self.assertEqual(self._reasons().count(credit_ops.REASON_LLM_SPEND), 1)
        self.assertEqual(self.backend._balance(USER_ID), 2)

    # -- the repeat after the cache expired -------------------------
    def test_the_repeat_after_the_cache_expired_calls_the_model_again(self):
        self._tap()
        # The cached answer expires.
        self.backend.advance(minutes=13 * 60)
        message, _query, _db = self._tap()
        # The operator paid for the second call.
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self._reasons().count(copilot_credits.REASON_LLM_ATTEMPT), 2)
        # The vacancy was paid for already, so the user pays nothing more.
        self.assertEqual(self._reasons().count(credit_ops.REASON_LLM_SPEND), 1)
        self.assertEqual(self.backend._balance(USER_ID), 2)
        self.assertIn("AI-разбор", self._text(message, _query))

    # -- the budget -------------------------------------------------
    def test_the_budget_stops_the_call_when_it_is_exhausted(self):
        with patch.dict(os.environ, {"COPILOT_LLM_DAILY_BUDGET": "1"}, clear=False):
            self._tap()
            # The only slot of the day is used.
            self.backend.advance(minutes=13 * 60)
            message, _query, _db = self._tap()
        # The second call does not reach the model at all.
        self.assertEqual(len(self.calls), 1)
        self.assertIn("лимит AI-разборов исчерпан", self._text(message, _query))
        self.assertEqual(self._reasons().count(copilot_credits.REASON_LLM_ATTEMPT), 1)
        self.assertEqual(self.backend._balance(USER_ID), 2)

    def test_a_budget_of_zero_calls_nobody(self):
        with patch.dict(os.environ, {"COPILOT_LLM_DAILY_BUDGET": "0"}, clear=False):
            message, _query, _db = self._tap()
        self.assertEqual(self.calls, [])
        self.assertIn("лимит AI-разборов исчерпан", self._text(message, _query))
        self.assertEqual(self.backend._balance(USER_ID), 3)

    def test_the_budget_counter_is_a_counter_of_its_own(self):
        """The counter of the budget is not the counter of the requests.

        The rate limit of the Mini App counts the requests of a user, the
        budget counts the calls of the model of everybody. They live in
        different rows, so one cannot reset the other.
        """
        manager = copilot_credits.CreditManager(_store(self.backend))
        self.assertTrue(manager.reserve_llm_call(USER_ID, "job-a"))
        self.assertTrue(manager.reserve_llm_call(USER_ID + 1, "job-b"))
        self.assertEqual(self.backend.rate_limits.get(0), 2)

    # -- parallel taps ----------------------------------------------
    def test_two_parallel_taps_call_the_model_once(self):
        """The analysis slot of the vacancy is the mutex of the flow.

        Without the claim two parallel taps would call the model twice, burn
        two slots of the budget and could charge the user twice, because the
        reservation of the credit cannot see the parallel transaction yet.
        """
        results = []

        def worker():
            try:
                self._tap(patched=False)
                results.append("ok")
            except Exception as exc:  # pragma: no cover - reported below
                results.append("error: %s" % exc)

        with patch.object(
            copilot_runtime.copilot_llm, "analyze", new=self._analyze()
        ):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(results, ["ok", "ok"])
        # One call of the model, one slot of the budget, one charge.
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self._reasons().count(copilot_credits.REASON_LLM_ATTEMPT), 1)
        self.assertEqual(self._reasons().count(credit_ops.REASON_LLM_SPEND), 1)
        self.assertEqual(self.backend._balance(USER_ID), 2)

    def test_the_second_tap_is_told_that_the_analysis_runs(self):
        """The tap that loses the race gets an answer, not a second analysis."""
        claim = self.backend._claim_ai_report((USER_ID, JOB["hash"], "claim:other", False, 15))
        self.assertIsNotNone(claim)
        message, _query, _db = self._tap()
        self.assertEqual(self.calls, [])
        self.assertIn("уже выполняется", self._text(message, _query))
        self.assertEqual(self.backend._balance(USER_ID), 3)
        # The claim of the other attempt is still there.
        self.assertIn((USER_ID, JOB["hash"]), self.backend.ai_reports)

    def test_a_stale_analysis_slot_is_taken_over(self):
        self.backend._claim_ai_report((USER_ID, JOB["hash"], "claim:old", False, 15))
        self.backend.advance(minutes=20)
        message, _query, _db = self._tap()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("AI-разбор", self._text(message, _query))


def _store(backend):
    from copilot.store import CopilotStore

    return CopilotStore(backend)


if __name__ == "__main__":
    unittest.main()
