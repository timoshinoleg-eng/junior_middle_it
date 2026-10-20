"""Credit accounting for costed Copilot actions.

Free deterministic analysis never costs credits. Credits are spent only by the
costed actions of the later stages (PDF export, LLM analysis) and they are
always granted first, so a user never sees a paid wall for a free feature.
"""
from __future__ import annotations

import logging
from typing import Optional

from copilot import flag_int
from copilot.store import CopilotStore

logger = logging.getLogger(__name__)

FREE_PER_DAY_DEFAULT = 3
REFERRAL_BONUS_DEFAULT = 5
LLM_DAILY_BUDGET_DEFAULT = 200

REASON_DAILY_GRANT = "daily_grant"
REASON_REFERRAL = "referral"
REASON_SPEND = "spend"
REASON_LLM_CALL = "llm_call"


def daily_free_credits() -> int:
    """Free costed actions per user per day."""
    return flag_int("COPILOT_FREE_PER_DAY", FREE_PER_DAY_DEFAULT, minimum=0, maximum=100)


def referral_bonus() -> int:
    """Credits granted to the referrer for an accepted invitation."""
    return flag_int("COPILOT_REFERRAL_BONUS", REFERRAL_BONUS_DEFAULT, minimum=0, maximum=100)


def llm_daily_budget() -> int:
    """Global daily ceiling for LLM calls across all users."""
    return flag_int("COPILOT_LLM_DAILY_BUDGET", LLM_DAILY_BUDGET_DEFAULT, minimum=0, maximum=100_000)


class CreditManager:
    """Thin, testable wrapper around the credit columns of the Copilot tables."""

    def __init__(self, store: CopilotStore):
        self._store = store

    def balance(self, user_id: int) -> int:
        return self._store.get_balance(user_id)

    def ensure_daily_allowance(self, user_id: int) -> int:
        """Grant the daily free allowance once and return the balance."""
        allowance = daily_free_credits()
        self._store.ensure_credit_row(user_id)
        granted = self._store.grant_daily_allowance(user_id, allowance)
        if granted is not None and allowance:
            self._safe_event(user_id, allowance, REASON_DAILY_GRANT)
        return self._store.get_balance(user_id)

    def grant_referral(self, user_id: int, amount: Optional[int] = None) -> int:
        """Credit a referral reward and return the new balance."""
        bonus = referral_bonus() if amount is None else int(amount)
        self._store.ensure_credit_row(user_id)
        balance = self._store.grant_credits(user_id, bonus)
        if bonus:
            self._safe_event(user_id, bonus, REASON_REFERRAL)
        return self._store.get_balance(user_id)

    def spend(self, user_id: int, amount: int = 1, reason: str = REASON_SPEND, ref: str = "") -> bool:
        """Atomically spend credits; ``False`` means the balance was too low."""
        if amount <= 0:
            return True
        remaining = self._store.spend_credits(user_id, amount)
        if remaining is None:
            return False
        self._safe_event(user_id, -int(amount), reason, ref)
        return True

    def llm_calls_today(self) -> int:
        try:
            return self._store.count_credit_events(REASON_LLM_CALL, today_only=True)
        except Exception as exc:
            logger.info("LLM budget read failed: %s", type(exc).__name__)
            return 0

    def llm_budget_left(self) -> int:
        """Remaining global LLM calls for today."""
        return max(0, llm_daily_budget() - self.llm_calls_today())

    def llm_allowed(self) -> bool:
        """Whether the global LLM budget still allows one more call."""
        return self.llm_budget_left() > 0

    def _safe_event(self, user_id: int, delta: int, reason: str, ref: str = "") -> None:
        try:
            self._store.log_credit_event(user_id, delta, reason, ref)
        except Exception as exc:
            logger.info("Credit event write failed: %s", type(exc).__name__)
