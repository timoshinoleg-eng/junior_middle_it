"""Feedback flow: reviews, suggestions and problems go straight to the owner.

The user runs /feedback, the bot asks for one message and forwards it —
original text, file or photo — to the owner chat (@freeveol by default,
override with FEEDBACK_OWNER_CHAT). The pending state is workflow metadata
only (RuntimeStateMap): the message itself is never stored, it lives only in
the Telegram forward.

Toggle: FEEDBACK_ENABLED (on by default; set to "0"/"false" to disable).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import channel_bot as core
from copilot import flag_enabled
from durable_runtime_state import RuntimeStateMap

logger = logging.getLogger(__name__)

PENDING_TTL_SECONDS = 30 * 60
PROMPT_TEXT = (
    "Напиши отзыв, предложение или опиши проблему одним сообщением — "
    "текстом или файлом (PDF/DOCX, скриншот). "
    "Я передам его владельцу (@freeveol) от твоего имени.\n\n"
    "Отмена: /cancel_feedback"
)
CONFIRM_TEXT = "Передал твоё сообщение владельцу. Спасибо за обратную связь!"
CANCELLED_TEXT = "Ок, не отправляю. Если захочешь — /feedback."
DISABLED_TEXT = "Обратная связь временно недоступна. Попробуй позже."
DELIVERY_FAILED_TEXT = (
    "Не удалось передать сообщение (сбой доставки). "
    "Попробуй ещё раз чуть позже — я пока сохранил твоё обращение."
)


def feedback_enabled() -> bool:
    return flag_enabled("FEEDBACK_ENABLED", default=True)


def owner_chat() -> str:
    """Recipient of the feedback: env override, @freeveol by default."""
    return str(os.getenv("FEEDBACK_OWNER_CHAT") or "").strip() or "@freeveol"


def _pending(job_bot):
    """Restart-safe pending-feedback flag; fake/local DBs fall back to a dict."""
    pending = getattr(job_bot, "feedback_pending", None)
    if pending is None:
        db = job_bot.db
        if all(
            callable(getattr(db, method, None))
            for method in ("get_runtime_state", "set_runtime_state", "delete_runtime_state")
        ):
            pending = RuntimeStateMap(
                db,
                "feedback_pending",
                ttl_seconds=PENDING_TTL_SECONDS,
                encode=lambda state: {"v": 1},
                decode=lambda state: {"v": 1} if state else None,
            )
        else:
            pending = {}
        setattr(job_bot, "feedback_pending", pending)
    return pending


def _attribution(user) -> str:
    """Who sent the feedback, so the owner can reply."""
    if user is None:
        return "От: неизвестный пользователь"
    name = " ".join(
        part for part in (getattr(user, "first_name", ""), getattr(user, "last_name", "")) if part
    ).strip()
    username = str(getattr(user, "username", "") or "").strip()
    handle = f"@{username.lstrip('@')}" if username else "без username"
    return f"От: {name or 'пользователь'} ({handle}), id={user.id}"


async def cmd_feedback(job_bot, update, context) -> None:
    """``/feedback`` — arm the one-shot capture and prompt the user."""
    if not feedback_enabled():
        await update.message.reply_text(DISABLED_TEXT)
        return
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return
    _pending(job_bot)[user_id] = {"v": 1}
    await update.message.reply_text(PROMPT_TEXT)
    job_bot.db.log_event(user_id, "feedback_started", {})


async def cmd_cancel(job_bot, update, context) -> None:
    """``/cancel_feedback`` — disarm without sending anything."""
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return
    _pending(job_bot).pop(user_id, None)
    await update.message.reply_text(CANCELLED_TEXT)


async def capture_if_pending(job_bot, update, context) -> bool:
    """Forward the user's message to the owner when feedback mode is armed.

    Returns True when the message was consumed (pending or not, the caller
    must not fall through to other flows). Only messages from armed users are
    touched; everyone else falls through untouched.
    """
    message = getattr(update, "message", None)
    if message is None:
        return False
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return False
    pending = _pending(job_bot)
    if user_id not in pending:
        return False
    if not feedback_enabled():
        pending.pop(user_id, None)
        await message.reply_text(DISABLED_TEXT)
        return True

    owner = owner_chat()
    header = "\n".join(
        [
            "📬 Обратная связь от пользователя бота",
            _attribution(update.effective_user),
            "---",
        ]
    )
    forwarded = False
    try:
        await context.bot.send_message(chat_id=owner, text=header)
        await context.bot.forward_message(
            chat_id=owner,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )
        forwarded = True
    except Exception as exc:
        logger.warning("Feedback forward failed: %s", type(exc).__name__)
        # Fallback: plain-text copy of what we can read. Media content that
        # cannot be copied is dropped — nothing is ever stored.
        text = str(getattr(message, "text", "") or getattr(message, "caption", "") or "").strip()
        if text:
            try:
                await context.bot.send_message(
                    chat_id=owner, text=header + "\n" + text[:3000]
                )
                forwarded = True
            except Exception as exc2:
                logger.warning("Feedback text fallback failed: %s", type(exc2).__name__)

    if forwarded:
        pending.pop(user_id, None)
        await message.reply_text(CONFIRM_TEXT)
        job_bot.db.log_event(user_id, "feedback_sent", {"owner": owner})
    else:
        await message.reply_text(DELIVERY_FAILED_TEXT)
    return True


def register_handlers(application, job_bot) -> None:
    """Register the /feedback entry points (capture hooks live in the runtime)."""
    from telegram.ext import CommandHandler

    application.add_handler(CommandHandler("feedback", job_bot.cmd_feedback))
    application.add_handler(CommandHandler("cancel_feedback", job_bot.cmd_cancel))
