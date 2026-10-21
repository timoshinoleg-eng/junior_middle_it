"""Credit accounting for costed Copilot actions.

Free deterministic analysis never costs credits. Credits are spent only by the
costed actions of the later stages (PDF export, LLM analysis) and they are
always granted first, so a user never sees a paid wall for a free feature.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from copilot import flag_int
from copilot.credit_operations import (
    REASON_LLM_RELEASE,
    REASON_LLM_SPEND as REASON_LLM_CALL,
    REASON_RELEASE,
    REASON_SPEND,
)
from copilot.store import CopilotStore

logger = logging.getLogger(__name__)

FREE_PER_DAY_DEFAULT = 3
REFERRAL_BONUS_DEFAULT = 5
LLM_DAILY_BUDGET_DEFAULT = 200

REASON_DAILY_GRANT = "daily_grant"
REASON_REFERRAL = "referral"
# Every real call of the model is written with this reason, so the global
# budget counts the calls and not only the charged ones: a repeated analysis
# of the same vacancy is free for the user but still costs the operator.
REASON_LLM_ATTEMPT = "llm_attempt"


def release_reason_for(reason: str) -> str:
    """The journal reason that gives a reservation of this kind back."""
    if str(reason or "") == REASON_LLM_CALL:
        return REASON_LLM_RELEASE
    return REASON_RELEASE


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
        if allowance:
            # The grant and the journal entry are one statement, so the balance
            # and the journal can never disagree about the daily allowance.
            self._store.grant_daily_allowance_with_event(
                int(user_id), int(allowance), REASON_DAILY_GRANT, ""
            )
        return self._store.get_balance(user_id)

    def grant_referral(self, user_id: int, amount: Optional[int] = None) -> int:
        """Credit a referral reward and return the new balance.

        The balance and the journal entry are written by one statement, so a
        failed write can no longer leave a credited balance without a journal
        entry. The rules of the bonus itself are unchanged.
        """
        bonus = referral_bonus() if amount is None else int(amount)
        if bonus:
            self._store.ensure_credit_row(user_id)
            self._store.grant_credits_with_event(
                int(user_id), int(bonus), REASON_REFERRAL, ""
            )
        else:
            self._store.ensure_credit_row(user_id)
        return self._store.get_balance(user_id)

    def spend(self, user_id: int, amount: int = 1, reason: str = REASON_SPEND, ref: str = "") -> bool:
        """Atomically spend credits; ``False`` means the balance was too low.

        The debit and the journal entry are written by one statement, so a
        failed journal write can no longer leave a changed balance behind. A
        store failure is raised, never swallowed: the caller must not hand out
        a paid action it could not charge for.

        A reference makes the spend idempotent: a repeated spend of the same
        reference charges once. A spend without a reference gets a private
        one, because such a spend is not part of a costed operation.
        """
        if amount <= 0:
            return True
        operation_ref = str(ref or "").strip() or ("op:%s" % uuid.uuid4().hex)
        return bool(
            self._store.reserve_credit(
                int(user_id),
                int(amount),
                str(reason or ""),
                operation_ref,
                release_reason=release_reason_for(reason),
            )
        )

    def llm_calls_today(self) -> int:
        """How many model calls were made today.

        The attempts are counted, not the charges: a repeated analysis of the
        same vacancy costs the operator a call even when the user is not
        charged for it again.
        """
        try:
            return self._store.count_credit_events(REASON_LLM_ATTEMPT, today_only=True)
        except Exception as exc:
            logger.info("LLM budget read failed: %s", type(exc).__name__)
            return 0

    def llm_budget_left(self) -> int:
        """Remaining global LLM calls for today."""
        return max(0, llm_daily_budget() - self.llm_calls_today())

    def llm_allowed(self) -> bool:
        """Whether the global LLM budget still allows one more call."""
        return self.llm_budget_left() > 0

    def reserve_llm_call(self, user_id: int, ref: str = "") -> bool:
        """Take one slot of the global daily model budget.

        The slot is taken and the attempt is recorded by one statement, so
        the call of the model is counted exactly once and cannot slip past
        the budget. ``False`` means the budget is exhausted and the model
        must not be called.
        """
        return bool(
            self._store.reserve_llm_attempt(
                int(user_id), llm_daily_budget(), REASON_LLM_ATTEMPT, str(ref or "")
            )
        )

    def _safe_event(self, user_id: int, delta: int, reason: str, ref: str = "") -> None:
        try:
            self._store.log_credit_event(user_id, delta, reason, ref)
        except Exception as exc:
            logger.info("Credit event write failed: %s", type(exc).__name__)
