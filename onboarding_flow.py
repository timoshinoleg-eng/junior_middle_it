"""Onboarding extras: channel deep links, lead magnet, Telegram Stars payments.

Integrated into the webhook runtime through the same monkey-patch chain as the
Copilot surfaces (see ``copilot_webhook_runtime``). New callback_data values
use the ``obn:`` prefix; existing payloads (``ref_``, ``copilot_``, ``resume_``,
``share_``, ``upskill_``, ``web_home``) are never intercepted here.

Product invariants respected:

- Resume content is never persisted; the vacancy-first flow of the Copilot
  stays the only resume workflow.
- Credits are granted only through the existing ``CopilotStore`` primitives
  and are idempotent per user (settings flag for the magnet, Telegram charge
  id for Stars payments).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import channel_bot as core
from copilot.store import CopilotStore
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice

logger = logging.getLogger(__name__)

DEEPLINK_FAV_PREFIX = "fav_"
DEEPLINK_MAGNET = "magnet"
DEEPLINK_STARS = "stars"

CB_CLAIM_MAGNET = "obn:claim_magnet"
CB_BUY_CREDITS = "obn:buy_credits"

MAGNET_BONUS_CREDITS = 3
STARS_CREDIT_PACK = 10
STARS_PRICE_XTR = 50
STARS_PAYLOAD_PREFIX = "stars_topup_"

SETTINGS_MAGNET_CLAIMED = "magnet_claimed"
SETTINGS_STARS_CHARGES = "stars_charge_ids"

_GUIDE_TEXT = (
    "📘 <b>Чек-лист: как проходить ATS-скрининг в 2026 году:</b>\n"
    "1. <b>Точное совпадение ключевых навыков:</b> парсеры ищут названия "
    "фреймворков из текста вакансии в верхнем блоке резюме.\n"
    "2. <b>Проверка реального грейда:</b> часто «Junior» вакансии содержат "
    "требования уровня Middle+ — бот подсвечивает это в разборе вакансии.\n"
    "3. <b>Прямой контакт с HR:</b> отклик в личку даёт заметно больше "
    "ответов, чем отклик на агрегаторе.\n\n"
    "👇 Открой вакансию в канале и нажми «✨ Разбор вакансии» — покажу "
    "совпадение стека, реальный уровень и контакт HR."
)


def _user_id(update) -> Optional[int]:
    user = getattr(update, "effective_user", None)
    return getattr(user, "id", None)


def _bot_username(context) -> str:
    username = str(getattr(getattr(context, "bot", None), "username", "") or "").strip()
    return username or os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")


def _channel_link() -> str:
    channel = str(getattr(core.Config, "CHANNEL_ID", "") or "").strip()
    if channel.startswith("@"):
        return f"https://t.me/{channel.lstrip('@')}"
    return channel


def grant_credits_once(db, user_id: int, amount: int, reason: str) -> bool:
    """Grant ``amount`` credits via the real CopilotStore; False when unavailable.

    Idempotency is the caller's responsibility (settings flag or charge id);
    the ``ref`` here is only a journal marker.
    """
    store = CopilotStore.from_database(db)
    if store is None:
        logger.info("Credits grant skipped: copilot store unavailable (%s)", reason)
        return False
    try:
        store.ensure_credit_row(int(user_id))
        return bool(
            store.grant_credits_with_event(int(user_id), int(amount), str(reason), ref=f"{reason}:{user_id}")
        )
    except Exception as exc:  # pragma: no cover - defensive, store failures are logged
        logger.info("Credits grant failed (%s): %s", reason, type(exc).__name__)
        return False


def _resolve_job(job_bot, job_hash: str) -> Optional[Dict]:
    """Resolve a vacancy card: channel payloads first, then the forward cache."""
    job = job_bot.db.get_job_payload(job_hash)
    if job:
        return job
    store = CopilotStore.from_database(job_bot.db)
    if store is None:
        return None
    try:
        row = store.get_forwarded_job(job_hash)
    except Exception as exc:
        logger.info("Onboarding forwarded job read failed: %s", type(exc).__name__)
        return None
    if not row:
        return None
    from copilot import forward as copilot_forward

    return copilot_forward.job_payload_from_row(row)


async def _check_subscription(bot, user_id: int) -> bool:
    """True when the user is a channel member; API errors never block the flow."""
    channel = str(getattr(core.Config, "CHANNEL_ID", "") or "").strip()
    if not channel:
        return True
    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
        return getattr(member, "status", "") in ("member", "administrator", "creator")
    except Exception as exc:
        logger.info("get_chat_member failed: %s", type(exc).__name__)
        return True


async def _deliver_magnet(job_bot, update, context) -> None:
    user_id = _user_id(update)
    if not user_id:
        await update.message.reply_text("Не удалось определить пользователя.")
        return
    settings = job_bot.db.get_user_settings(user_id) or {}

    if not settings.get(SETTINGS_MAGNET_CLAIMED):
        if not await _check_subscription(context.bot, user_id):
            await update.message.reply_text(
                "🔒 <b>Гайд и бонусные AI-кредиты — для подписчиков канала.</b>\n\n"
                "Подпишись и нажми кнопку проверки ниже:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📢 Подписаться на канал", url=_channel_link()),
                    InlineKeyboardButton("✅ Я подписался, забрать бонус!", callback_data=CB_CLAIM_MAGNET),
                ]]),
            )
            return
        granted = grant_credits_once(job_bot.db, user_id, MAGNET_BONUS_CREDITS, "magnet_bonus")
        job_bot.db.save_user_settings(user_id, {SETTINGS_MAGNET_CLAIMED: True})
        job_bot.db.log_event(user_id, "magnet_claimed", {"credits": MAGNET_BONUS_CREDITS if granted else 0})
        header = (
            f"🎉 <b>Подписка подтверждена!</b> "
            f"Начислено +{MAGNET_BONUS_CREDITS} AI-кредита.\n\n"
            if granted
            else "🎉 <b>Подписка подтверждена!</b>\n\n"
        )
    else:
        header = "👋 Гайд у тебя уже был — держи ещё раз:\n\n"

    await update.message.reply_text(header + _GUIDE_TEXT, parse_mode="HTML")


async def _send_invoice(update, context) -> None:
    user_id = _user_id(update)
    chat = getattr(update, "effective_chat", None)
    if not user_id or chat is None:
        return
    await context.bot.send_invoice(
        chat_id=chat.id,
        title=f"Пакет AI-кредитов ({STARS_CREDIT_PACK} шт.)",
        description=(
            "Кредиты Копилота: разбор вакансии с AI, PDF-отчёты и "
            "сопроводительные письма. Расходуются только на платные действия."
        ),
        payload=f"{STARS_PAYLOAD_PREFIX}{user_id}",
        currency="XTR",
        prices=[LabeledPrice(f"{STARS_CREDIT_PACK} AI-кредитов", STARS_PRICE_XTR)],
        provider_token="",  # пустая строка — обязательна для Telegram Stars (XTR)
    )


async def cmd_stars(job_bot, update, context) -> None:
    """``/stars`` — показать инвойс Telegram Stars."""
    await _send_invoice(update, context)


async def precheckout_handler(update, context) -> None:
    """Подтверждаем только свои инвойсы (payload начинается с stars_topup_)."""
    query = update.pre_checkout_query
    if query is None:
        return
    payload = str(getattr(query, "invoice_payload", "") or "")
    if payload.startswith(STARS_PAYLOAD_PREFIX):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Неизвестный платёж.")


async def successful_payment_handler(job_bot, update, context) -> None:
    """Зачисляем кредиты за Stars; идемпотентно по telegram_payment_charge_id."""
    message = getattr(update, "message", None)
    payment = getattr(message, "successful_payment", None)
    user_id = _user_id(update)
    if payment is None or not user_id:
        return
    charge_id = str(getattr(payment, "telegram_payment_charge_id", "") or "")
    settings = job_bot.db.get_user_settings(user_id) or {}
    charges = list(settings.get(SETTINGS_STARS_CHARGES) or [])
    if charge_id and charge_id in charges:
        await message.reply_text("Эти кредиты уже зачислены на твой баланс. /profile")
        return
    granted = grant_credits_once(job_bot.db, user_id, STARS_CREDIT_PACK, "stars_topup")
    if charge_id:
        charges.append(charge_id)
        job_bot.db.save_user_settings(user_id, {SETTINGS_STARS_CHARGES: charges})
    job_bot.db.log_event(user_id, "stars_credits_granted", {"credits": STARS_CREDIT_PACK if granted else 0})
    if granted:
        await message.reply_text(
            f"🌟 <b>Оплата прошла!</b> На баланс зачислено +{STARS_CREDIT_PACK} AI-кредитов.",
            parse_mode="HTML",
        )
    else:
        await message.reply_text(
            "🌟 Оплата прошла, но зачислить кредиты не получилось — напиши /start, мы разберёмся."
        )


async def handle_start_extras(job_bot, update, context) -> bool:
    """Обработка новых deep link payload'ей из канала. True — обработано.

    Чужие payload'и (ref_, copilot_, resume_, share_, upskill_, web_home)
    возвращают False и уходят в штатные обработчики.
    """
    payload = context.args[0] if getattr(context, "args", None) else None
    payload = str(payload or "").strip()
    if not payload:
        return False

    if payload.startswith(DEEPLINK_FAV_PREFIX):
        await _handle_favorite_deep_link(job_bot, update, context, payload[len(DEEPLINK_FAV_PREFIX):])
        return True
    if payload == DEEPLINK_MAGNET:
        await _deliver_magnet(job_bot, update, context)
        return True
    if payload == DEEPLINK_STARS:
        await _send_invoice(update, context)
        return True
    return False


async def _handle_favorite_deep_link(job_bot, update, context, job_hash: str) -> None:
    user_id = _user_id(update)
    job_hash = str(job_hash or "").strip()[:64]
    message = getattr(update, "message", None)
    if not user_id:
        if message is not None:
            await message.reply_text("Не удалось определить пользователя.")
        return
    job = _resolve_job(job_bot, job_hash) if job_hash else None
    if not job:
        if message is not None:
            await message.reply_text("Карточка вакансии устарела. Открой свежий пост в канале.")
        return
    job_bot.db.add_favorite(user_id, job_hash)
    job_bot.db.log_event(user_id, "onboarding_favorite_added", {"hash": job_hash})
    title = str(job.get("title") or "вакансия")
    buttons = []
    username = _bot_username(context)
    if username:
        buttons.append([
            InlineKeyboardButton("✨ Разбор вакансии", url=f"https://t.me/{username}?start=copilot_{job_hash}")
        ])
    await message.reply_text(
        f"⭐ <b>«{title}» сохранена в Избранное.</b>\n\nВсе избранные вакансии — через /favorites.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


async def handle_callback_extras(job_bot, update, context) -> bool:
    """Обработка callback'ов с префиксом ``obn:``. True — обработано."""
    query = getattr(update, "callback_query", None)
    data = str(getattr(query, "data", "") or "") if query else ""
    if data == CB_CLAIM_MAGNET:
        if query is not None and hasattr(query, "answer"):
            try:
                await query.answer()
            except Exception:  # pragma: no cover - acknowledge best effort
                pass
        await _deliver_magnet(job_bot, update, context)
        return True
    if data == CB_BUY_CREDITS:
        if query is not None and hasattr(query, "answer"):
            try:
                await query.answer()
            except Exception:  # pragma: no cover
                pass
        await _send_invoice(update, context)
        return True
    return False


def register_handlers(application, job_bot) -> None:
    """Register /stars, pre-checkout and successful-payment handlers."""
    from telegram.ext import CommandHandler, MessageHandler, PreCheckoutQueryHandler, filters

    application.add_handler(CommandHandler("stars", job_bot.cmd_stars))
    application.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
