"""Durable and idempotent credit accounting for a costed Copilot action.

A costed action such as the CV export spans three steps: the credit is
reserved, the document is produced and the document is delivered. The credit
must not be lost when the production or the delivery fails, and it must not be
spent twice when the user repeats the request.

The state of one operation is the row of the operation registry of the store,
``growth_copilot_credit_ops``. One row per user, reference and kind of
operation holds the state of the operation and is the gate of the reservation:
the database refuses a second reservation of the same attempt, so two parallel
requests of one vacancy cannot both charge the user. The rule used to be
derived from the journal, and that rule cannot be made safe: under READ
COMMITTED a parallel transaction does not see the event of the other one yet,
so both of them counted the same number of reservations.

The journal stays the history of the credits:

* ``growth_copilot_credit_events`` - the journal. A ``spend`` entry marks the
  reservation, a ``spend_release`` entry the refund, ``cv_delivered`` a
  confirmed delivery and ``cv_delivery_unknown`` an outcome that must not be
  refunded automatically.
* ``growth_copilot_documents`` - the document cache. Its primary key
  ``(user_id, job_id)`` is the mutex of one build: only one request may
  assemble the same document at the same time.

States of one operation:

``new``        nothing happened yet;
``reserved``   the credit is held, the document is not delivered yet;
``delivered``  the document was delivered and confirmed;
``released``   the reservation was given back, the user was not charged;
``unknown``    the delivery outcome is ambiguous - the credit stays spent and
               a later request must not charge again.

The ambiguity is deliberate. A Telegram timeout does not prove that the
document was not delivered, so the credit is not returned automatically; the
state records that the operation was already paid for.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

STATE_NEW = "new"
STATE_RESERVED = "reserved"
STATE_DELIVERED = "delivered"
STATE_RELEASED = "released"
STATE_UNKNOWN = "unknown"

REASON_SPEND = "spend"
REASON_RELEASE = "spend_release"
REASON_DELIVERED = "cv_delivered"
REASON_UNKNOWN = "cv_delivery_unknown"

REASON_LLM_SPEND = "llm_call"
REASON_LLM_RELEASE = "llm_call_release"
REASON_LLM_DELIVERED = "llm_delivered"
REASON_LLM_UNKNOWN = "llm_delivery_unknown"

DELIVERY_DELIVERED = "delivered"
DELIVERY_REJECTED = "rejected"
DELIVERY_UNKNOWN = "unknown"

# The states of the operation registry of the store and the states of this
# module are the same, they are only named differently here.
_OP_STATES = {
    "reserved": STATE_RESERVED,
    "released": STATE_RELEASED,
    "delivered": STATE_DELIVERED,
    "unknown": STATE_UNKNOWN,
}

# A claim of the document slot that is older than this is taken over, so a
# build that died in the middle does not block the user forever.
STALE_CLAIM_MINUTES = 15


class CreditOperationError(Exception):
    """Base class of the credit errors of a costed operation."""


class InsufficientCredits(CreditOperationError):
    """The user has no credit left for the action."""


class CreditStoreUnavailable(CreditOperationError):
    """The credit store could not confirm the operation."""


class CostedOperation:
    """One costed action, for example the CV export of one vacancy.

    The operation is identified by the user and the reference, which is the
    hash of the vacancy. The same reference is used by the Telegram bot and by
    the Mini App, so a user cannot be charged twice for the same vacancy
    through two channels.
    """

    def __init__(
        self,
        store,
        user_id: int,
        ref: str,
        *,
        amount: int = 1,
        spend_reason: str = REASON_SPEND,
        release_reason: str = REASON_RELEASE,
        delivered_reason: str = REASON_DELIVERED,
        unknown_reason: str = REASON_UNKNOWN,
    ):
        self._store = store
        self._user_id = int(user_id)
        self._ref = str(ref or "")
        self._amount = int(amount)
        self._spend_reason = str(spend_reason or REASON_SPEND)
        self._release_reason = str(release_reason or REASON_RELEASE)
        self._delivered_reason = str(delivered_reason or REASON_DELIVERED)
        self._unknown_reason = str(unknown_reason or REASON_UNKNOWN)
        self._state: Optional[str] = None
        self.attempt: Optional[int] = None
        self._reserved_here = False
        self._paid_replay = False
        self._claim: Optional[str] = None

    def bind_claim(self, claim: str):
        """Bind this operation to the build slot before reserving."""
        self._claim = str(claim)
        return self

    def can_deliver(self) -> bool:
        """Refuse stale builds before crossing the delivery boundary."""
        if not self._reserved_here and not self._paid_replay:
            return False
        try:
            return (
                (not self._claim or self._store.owns_claim(
                    self._user_id, self._ref, self._claim, self._spend_reason
                ))
                and (self._paid_replay or self._store.owns_credit_attempt(
                    self._user_id, self._ref, self._spend_reason, self.attempt
                ))
            )
        except Exception as exc:
            logger.warning("Credit ownership check failed: %s", type(exc).__name__)
            return False

    def _require_owner(self):
        if not self._reserved_here:
            raise CreditStoreUnavailable("This object does not own a reservation")
        if self._claim and not self._store.owns_claim(
            self._user_id, self._ref, self._claim, self._spend_reason
        ):
            raise CreditStoreUnavailable("The build claim was taken over")

    @property
    def ref(self) -> str:
        return self._ref

    @property
    def amount(self) -> int:
        return self._amount

    @property
    def paid(self) -> bool:
        """Whether the user already paid for this operation."""
        return self.state() in (STATE_RESERVED, STATE_DELIVERED, STATE_UNKNOWN)

    def state(self) -> str:
        """The current state of the operation."""
        if self._state is None:
            self._state = self._read_state()
        return self._state

    def _event_counts(self) -> Dict[str, int]:
        try:
            return self._store.credit_event_counts(
                self._user_id,
                (
                    self._spend_reason,
                    self._release_reason,
                    self._delivered_reason,
                    self._unknown_reason,
                ),
                self._ref,
            )
        except Exception as exc:
            raise CreditStoreUnavailable(
                "credit journal is unavailable: %s" % type(exc).__name__
            ) from exc

    def _read_state(self) -> str:
        """The state of the operation.

        The state is the row of the operation registry of the store, because
        that row is the gate of the reservation: the state that is read here
        is the state that the database used to refuse a second charge. A
        production without the registry falls back to the journal.
        """
        try:
            registered = self._store.credit_operation_state(
                self._user_id, self._ref, self._spend_reason
            )
        except Exception as exc:
            raise CreditStoreUnavailable(
                "credit operation is unavailable: %s" % type(exc).__name__
            ) from exc
        if registered:
            return _OP_STATES.get(str(registered), STATE_NEW)
        counts = self._event_counts()
        spends = int(counts.get(self._spend_reason, 0))
        releases = int(counts.get(self._release_reason, 0))
        if counts.get(self._delivered_reason):
            return STATE_DELIVERED
        if counts.get(self._unknown_reason):
            return STATE_UNKNOWN
        if spends > releases:
            return STATE_RESERVED
        if spends:
            return STATE_RELEASED
        return STATE_NEW

    def reserve(self) -> str:
        """Reserve the credit of this operation.

        The reservation happens at most once per attempt. Only the object
        that acquired it may settle it; another object observing an open
        reservation is refused. A paid terminal result may be replayed without
        settlement rights. A request after a refund acquires a new attempt.

        Raises :class:`InsufficientCredits` when the balance is too low and
        :class:`CreditStoreUnavailable` when the store cannot confirm it.
        """
        if self._claim and not self._store.owns_claim(
            self._user_id, self._ref, self._claim, self._spend_reason
        ):
            raise CreditStoreUnavailable("The build claim was taken over")
        state = self.state()
        if state in (STATE_DELIVERED, STATE_UNKNOWN):
            self._paid_replay = not self._reserved_here
            return state
        if state == STATE_RESERVED:
            if self._reserved_here:
                return state
            raise CreditStoreUnavailable("Reservation belongs to another operation")
        try:
            reserved = self._store.reserve_credit(
                self._user_id,
                self._amount,
                self._spend_reason,
                self._ref,
                release_reason=self._release_reason,
            )
        except Exception as exc:
            raise CreditStoreUnavailable(
                "credit reservation failed: %s" % type(exc).__name__
            ) from exc
        if reserved:
            self.attempt = None if reserved is True else int(reserved)
            self._reserved_here = True
            self._state = STATE_RESERVED
            return self._state
        # The store refused. Either the balance was too low or the operation
        # is already paid for; the journal tells the two apart.
        self._state = self._read_state()
        if self._state in (STATE_RESERVED, STATE_DELIVERED, STATE_UNKNOWN):
            raise CreditStoreUnavailable("Reservation belongs to another operation")
        raise InsufficientCredits("No credits available")

    def release(self) -> str:
        """Give the reservation back.

        Only a reservation can be released: a delivered or already released
        operation keeps its state, so a repeated release cannot credit the
        user twice.
        """
        self._require_owner()
        state = self.state()
        if state != STATE_RESERVED:
            return state
        try:
            released = self._store.release_credit(
                self._user_id,
                self._amount,
                self._release_reason,
                self._ref,
                reserved_reason=self._spend_reason,
                attempt=self.attempt, claim=self._claim,
            )
        except Exception as exc:
            logger.warning("Credit release failed: %s", type(exc).__name__)
            return state
        if released:
            self._state = STATE_RELEASED
        else:
            raise CreditStoreUnavailable("Settlement refused for this attempt")
        return self._state

    def confirm(self) -> str:
        """Mark the operation as delivered."""
        self._require_owner()
        state = self.state()
        if state in (STATE_DELIVERED, STATE_RELEASED):
            return state
        try:
            confirmed = self._store.confirm_credit_operation(
                self._user_id, self._ref, self._spend_reason, self._delivered_reason,
                attempt=self.attempt, claim=self._claim
            )
        except Exception as exc:
            logger.warning("Credit confirmation failed: %s", type(exc).__name__)
            return state
        if confirmed:
            self._state = STATE_DELIVERED
        else:
            raise CreditStoreUnavailable("Settlement refused for this attempt")
        return self._state

    def mark_unknown(self) -> str:
        """Mark the delivery outcome as ambiguous.

        The credit stays spent: Telegram may have delivered the document even
        though the answer was lost. The state makes sure that a later request
        does not charge the user again.
        """
        self._require_owner()
        state = self.state()
        if state != STATE_RESERVED:
            return state
        try:
            marked = self._store.mark_credit_operation_unknown(
                self._user_id, self._ref, self._spend_reason, self._unknown_reason,
                attempt=self.attempt, claim=self._claim
            )
        except Exception as exc:
            logger.warning("Credit state update failed: %s" % type(exc).__name__)
            return state
        if marked:
            self._state = STATE_UNKNOWN
        else:
            raise CreditStoreUnavailable("Settlement refused for this attempt")
        return self._state


def settle_delivery(operation: CostedOperation, outcome: str) -> str:
    """Bring the credit of an operation in line with the delivery outcome.

    A delivered document is confirmed, a document that Telegram rejected is
    paid back and an open outcome keeps the credit spent, because a lost
    answer does not prove that the document never arrived.
    """
    if operation._paid_replay:
        # An already paid replay owns no reservation and must never settle it.
        return operation.state()
    if outcome == DELIVERY_DELIVERED:
        return operation.confirm()
    if outcome == DELIVERY_REJECTED:
        return operation.release()
    return operation.mark_unknown()


def cv_operation(store, user_id: int, job_hash: str) -> CostedOperation:
    """The CV export of one vacancy, shared by the bot and the Mini App."""
    return CostedOperation(
        store,
        user_id,
        job_hash,
        spend_reason=REASON_SPEND,
        release_reason=REASON_RELEASE,
        delivered_reason=REASON_DELIVERED,
        unknown_reason=REASON_UNKNOWN,
    )


def llm_operation(store, user_id: int, job_hash: str) -> CostedOperation:
    """The AI analysis of one vacancy."""
    return CostedOperation(
        store,
        user_id,
        job_hash,
        spend_reason=REASON_LLM_SPEND,
        release_reason=REASON_LLM_RELEASE,
        delivered_reason=REASON_LLM_DELIVERED,
        unknown_reason=REASON_LLM_UNKNOWN,
    )
