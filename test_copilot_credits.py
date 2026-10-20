import os
import unittest
from unittest.mock import patch

from copilot import credits as copilot_credits
from copilot.credits import CreditManager


class FakeStore:
    """In-memory stand-in for the durable credit columns."""

    def __init__(self):
        self.balances = {}
        self.granted_today = set()
        self.events = []

    def ensure_credit_row(self, user_id):
        self.balances.setdefault(int(user_id), 0)

    def get_balance(self, user_id):
        return int(self.balances.get(int(user_id), 0))

    def grant_daily_allowance(self, user_id, amount):
        uid = int(user_id)
        if uid in self.granted_today:
            return None
        self.granted_today.add(uid)
        self.balances[uid] = self.get_balance(uid) + int(amount)
        return self.balances[uid]

    def grant_daily_allowance_with_event(self, user_id, amount, reason, ref=""):
        uid = int(user_id)
        if uid in self.granted_today:
            return False
        self.granted_today.add(uid)
        self.balances[uid] = self.get_balance(uid) + int(amount)
        self.events.append((uid, int(amount), reason, ref))
        return True

    def grant_credits(self, user_id, amount):
        uid = int(user_id)
        self.balances[uid] = self.get_balance(uid) + int(amount)
        return self.balances[uid]

    def spend_credits(self, user_id, amount):
        uid = int(user_id)
        if self.get_balance(uid) < int(amount):
            return None
        self.balances[uid] = self.get_balance(uid) - int(amount)
        return self.balances[uid]

    def _count(self, user_id, reason, ref):
        return sum(
            1
            for uid, _delta, item_reason, item_ref in self.events
            if int(uid) == int(user_id)
            and str(item_reason) == str(reason)
            and str(item_ref) == str(ref)
        )

    def reserve_credit(self, user_id, amount, reason, ref, *, release_reason="spend_release"):
        uid = int(user_id)
        if self._count(uid, reason, ref) > self._count(uid, release_reason, ref):
            return False
        if self.get_balance(uid) < int(amount):
            return False
        self.balances[uid] = self.get_balance(uid) - int(amount)
        self.events.append((uid, -int(amount), reason, ref))
        return True

    def release_credit(self, user_id, amount, reason, ref, *, reserved_reason):
        uid = int(user_id)
        if self._count(uid, reserved_reason, ref) <= self._count(uid, reason, ref):
            return False
        self.balances[uid] = self.get_balance(uid) + int(amount)
        self.events.append((uid, int(amount), reason, ref))
        return True

    def credit_event_counts(self, user_id, reasons, ref):
        counts = {}
        for uid, _delta, reason, item_ref in self.events:
            if int(uid) != int(user_id) or str(item_ref) != str(ref):
                continue
            if reason in reasons:
                counts[reason] = counts.get(reason, 0) + 1
        return counts

    def log_credit_event(self, user_id, delta, reason, ref=""):
        self.events.append((int(user_id), int(delta), reason, ref))
        return True

    def count_credit_events(self, reason, today_only=False):
        return sum(1 for _uid, _delta, item_reason, _ref in self.events if item_reason == reason)


class CreditManagerTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.manager = CreditManager(self.store)

    def test_daily_allowance_is_granted_once(self):
        with patch.dict(os.environ, {"COPILOT_FREE_PER_DAY": "3"}, clear=False):
            first = self.manager.ensure_daily_allowance(1)
            second = self.manager.ensure_daily_allowance(1)
        self.assertEqual(first, 3)
        self.assertEqual(second, 3)
        self.assertEqual(self.store.get_balance(1), 3)

    def test_spend_reduces_the_balance(self):
        self.manager.ensure_daily_allowance(1)
        self.assertTrue(self.manager.spend(1, 1, copilot_credits.REASON_SPEND))
        self.assertEqual(self.manager.balance(1), 2)

    def test_spend_without_balance_is_rejected(self):
        self.assertFalse(self.manager.spend(2, 1))
        self.assertEqual(self.manager.balance(2), 0)

    def test_spend_of_zero_is_a_noop(self):
        self.assertTrue(self.manager.spend(1, 0))

    def test_spend_writes_the_journal_entry_with_the_debit(self):
        self.manager.ensure_daily_allowance(1)
        self.assertTrue(self.manager.spend(1, 1, copilot_credits.REASON_SPEND, "job-1"))
        spends = [event for event in self.store.events if event[2] == copilot_credits.REASON_SPEND]
        self.assertEqual(len(spends), 1)
        self.assertEqual(spends[0][1], -1)
        self.assertEqual(spends[0][3], "job-1")
        self.assertEqual(self.store.get_balance(1), 2)

    def test_the_same_reference_is_charged_once(self):
        self.manager.ensure_daily_allowance(1)
        self.assertTrue(self.manager.spend(1, 1, copilot_credits.REASON_SPEND, "job-1"))
        self.assertFalse(self.manager.spend(1, 1, copilot_credits.REASON_SPEND, "job-1"))
        self.assertEqual(self.store.get_balance(1), 2)

    def test_referral_bonus_is_granted_and_logged(self):
        with patch.dict(os.environ, {"COPILOT_REFERRAL_BONUS": "5"}, clear=False):
            balance = self.manager.grant_referral(1)
        self.assertEqual(balance, 5)
        self.assertTrue(any(event[2] == copilot_credits.REASON_REFERRAL for event in self.store.events))

    def test_llm_budget_is_global_and_bounded(self):
        with patch.dict(os.environ, {"COPILOT_LLM_DAILY_BUDGET": "2"}, clear=False):
            self.assertTrue(self.manager.llm_allowed())
            self.store.log_credit_event(1, -1, copilot_credits.REASON_LLM_CALL)
            self.assertTrue(self.manager.llm_allowed())
            self.store.log_credit_event(2, -1, copilot_credits.REASON_LLM_CALL)
            self.assertFalse(self.manager.llm_allowed())
            self.assertEqual(self.manager.llm_budget_left(), 0)

    def test_free_allowance_is_configurable(self):
        with patch.dict(os.environ, {"COPILOT_FREE_PER_DAY": "7"}, clear=False):
            self.assertEqual(copilot_credits.daily_free_credits(), 7)

    def test_invalid_flag_falls_back_to_the_default(self):
        with patch.dict(os.environ, {"COPILOT_FREE_PER_DAY": "many"}, clear=False):
            self.assertEqual(copilot_credits.daily_free_credits(), copilot_credits.FREE_PER_DAY_DEFAULT)


if __name__ == "__main__":
    unittest.main()
