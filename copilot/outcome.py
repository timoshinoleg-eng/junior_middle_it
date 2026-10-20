"""Application tracker: did the reply lead to anything?

The tracker is deliberately small: a user marks that they sent an application,
the scheduler asks about the result after a week, and the answer closes the
loop. The module holds the state machine and the wording; the storage lives in
``copilot/store.py`` and the Telegram glue in the webhook runtime.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

STAGE_SENT = "sent"
STAGE_INTERVIEW = "interview"
STAGE_NO_RESPONSE = "no_response"
STAGE_REJECTED = "rejected"

OUTCOME_STAGES = (STAGE_INTERVIEW, STAGE_NO_RESPONSE, STAGE_REJECTED)
ALL_STAGES = (STAGE_SENT,) + OUTCOME_STAGES

STAGE_LABELS = {
    STAGE_SENT: "Отклик отправлен",
    STAGE_INTERVIEW: "Позвали на интервью",
    STAGE_NO_RESPONSE: "Тишина",
    STAGE_REJECTED: "Отказ",
}

OUTCOME_BUTTONS = (
    (STAGE_INTERVIEW, "🎉 Позвали на интервью"),
    (STAGE_NO_RESPONSE, "🤫 Тишина"),
    (STAGE_REJECTED, "🙅 Отказ"),
)

DEFAULT_FOLLOWUP_DAYS = 7


def normalize_stage(value: str) -> str:
    """Map an arbitrary value to a known stage."""
    stage = str(value or "").strip().lower()
    return stage if stage in ALL_STAGES else STAGE_SENT


def is_due(
    sent_at: Optional[datetime],
    *,
    days: int = DEFAULT_FOLLOWUP_DAYS,
    now: Optional[datetime] = None,
) -> bool:
    """Whether an application is old enough to ask about the result."""
    if sent_at is None:
        return False
    wait = max(1, int(days))
    current = now or datetime.now(timezone.utc)
    stamp = sent_at
    if getattr(stamp, "tzinfo", None) is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return current - stamp >= timedelta(days=wait)


def reminder_text(*, title: str = "", company: str = "", days: int = DEFAULT_FOLLOWUP_DAYS) -> str:
    """Wording of the follow-up message."""
    heading = str(title or "вакансию").strip()
    if company:
        heading = f"{heading} · {company}"
    wait = max(1, int(days))
    return (
        f"⏰ Ты откликался на «{heading}» {wait} дн. назад — что с ответом?\n\n"
        "Отметь, и я перестану напоминать."
    )


def outcome_callback(stage: str, job_hash: str) -> str:
    """Callback payload of an outcome button."""
    return f"copilot_outcome:{normalize_stage(stage)}:{job_hash}"


def stage_from_callback(callback_data: str) -> Tuple[str, str]:
    """Parse a callback payload into a stage and a job hash."""
    raw = str(callback_data or "")
    parts = raw.split(":", 2)
    if len(parts) != 3 or parts[0] != "copilot_outcome":
        return "", ""
    return normalize_stage(parts[1]), parts[2]
