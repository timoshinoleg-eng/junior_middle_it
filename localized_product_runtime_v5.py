"""Channel ↔ bot soft-linking and measurable membership UX.

This layer keeps the public channel open and the bot fully usable without a
subscription. It adds a measurable channel surface in the bot, optional
membership verification, a small subscriber bonus, and channel→bot attribution.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

import channel_bot as core
import localized_product_runtime_v4 as base
from product_i18n import t


DatabaseConnection = base.DatabaseConnection


def _public_channel_username() -> str:
    explicit = str(os.getenv("PUBLIC_CHANNEL_USERNAME") or "").strip().lstrip("@")
    if explicit:
        return explicit
    channel_id = str(core.Config.CHANNEL_ID or "").strip()
    if channel_id.startswith("@"):
        return channel_id.lstrip("@")
    return "junior_middle_it"


class JobBot(base.JobBot):
    def _channel_chat_id(self) -> str:
        return str(core.Config.CHANNEL_ID or f"@{_public_channel_username()}").strip()

    def _channel_url(self) -> str:
        return f"https://t.me/{_public_channel_username()}"

    def _retention_keyboard_from_state(self, lang: str, settings: dict, searches: list):
        markup = super()._retention_keyboard_from_state(lang, settings, searches)
        rows = [list(row) for row in markup.inline_keyboard]
        # v4 appended a direct URL button. Replace it with a callback so the bot
        # can measure intent and verify membership before showing the channel link.
        rows = [
            row for row in rows
            if not (
                len(row) == 1
                and getattr(row[0], "url", None)
                and str(getattr(row[0], "url", "")).startswith("https://t.me/")
            )
        ]
        rows.append([
            core.InlineKeyboardButton(
                "📢 Канал вакансий" if lang == "ru" else "📢 Jobs channel",
                callback_data="p11_channel",
            )
        ])
        return core.InlineKeyboardMarkup(rows)

    async def _channel_membership(self, user_id: int) -> Optional[bool]:
        """Return True/False when Telegram can verify membership, else None.

        getChatMember for another user may require channel-admin permissions.
        Verification failure must never block normal bot usage.
        """
        try:
            member = await self.application.bot.get_chat_member(
                chat_id=self._channel_chat_id(),
                user_id=int(user_id),
            )
        except Exception as exc:
            self.db.log_event(
                user_id,
                "channel_membership_check_failed",
                {"error_type": type(exc).__name__},
            )
            return None

        status = str(getattr(member, "status", "") or "").lower()
        subscribed = status in {"creator", "administrator", "member"}
        if status == "restricted":
            subscribed = bool(getattr(member, "is_member", False))
        self.db.log_event(
            user_id,
            "channel_membership_checked",
            {"status": status[:32], "subscribed": bool(subscribed)},
        )
        return bool(subscribed)

    def _channel_status_markup(self, lang: str, subscribed: Optional[bool]):
        rows = [[core.InlineKeyboardButton(
            "📢 Открыть канал" if lang == "ru" else "📢 Open channel",
            url=self._channel_url(),
        )]]
        if subscribed is True:
            rows.insert(0, [core.InlineKeyboardButton(
                "⭐ Показать 5 свежих вакансий" if lang == "ru" else "⭐ Show 5 fresh jobs",
                callback_data="p11_channel_bonus",
            )])
        else:
            rows.append([core.InlineKeyboardButton(
                "✅ Проверить подписку" if lang == "ru" else "✅ Check subscription",
                callback_data="p11_channel",
            )])
        rows.append([core.InlineKeyboardButton(
            "← Назад" if lang == "ru" else "← Back",
            callback_data="p8_retention_hub",
        )])
        return core.InlineKeyboardMarkup(rows)

    async def _send_channel_status(self, message, user_id: int, subscribed: Optional[bool]):
        lang = self._lang(user_id)
        if subscribed is True:
            text = (
                "✅ Подписка на канал подтверждена.\n\n"
                "Канал остаётся общей лентой свежих вакансий, а бот — твоим персональным режимом.\n"
                "⭐ Бонус подписчика: можно одним нажатием посмотреть 5 свежих вакансий в боте."
                if lang == "ru" else
                "✅ Your channel subscription is confirmed.\n\n"
                "The channel remains the public feed, while the bot is your personalized mode.\n"
                "⭐ Subscriber bonus: you can view 5 fresh jobs in the bot with one tap."
            )
        elif subscribed is False:
            text = (
                "📢 Ты пока не подписан на канал. Это не блокирует бот — все основные функции доступны.\n\n"
                "Подпишись, если хочешь общую ленту свежих Junior/Middle IT-вакансий. После подписки нажми «Проверить подписку»."
                if lang == "ru" else
                "📢 You are not subscribed to the channel yet. The bot stays fully usable — nothing is locked.\n\n"
                "Subscribe if you want the public feed of fresh Junior/Middle IT jobs, then tap “Check subscription”."
            )
        else:
            text = (
                "📢 Канал доступен без ограничений. Сейчас я не смог автоматически проверить подписку, но это никак не мешает пользоваться ботом."
                if lang == "ru" else
                "📢 The channel is available without restrictions. I could not verify your subscription automatically, but this does not affect bot access."
            )
        await message.reply_text(
            text,
            reply_markup=self._channel_status_markup(lang, subscribed),
            disable_web_page_preview=True,
        )

    async def _send_channel_bonus(self, user_id: int) -> int:
        """Small soft bonus: five source-fresh jobs, still using the user's profile."""
        lang = self._lang(user_id)
        settings = self._effective_profile(user_id)
        jobs = self.db.recent_jobs_for_digest(hours=48, limit=60)
        matched: List[Dict] = []
        for job in jobs:
            if core.GROWTH_UTILS_AVAILABLE and not core.job_matches_profile(job, settings):
                continue
            matched.append(job)
            if len(matched) >= 5:
                break
        if not matched:
            await self.application.bot.send_message(
                chat_id=user_id,
                text=(
                    "Пока нет свежих вакансий под твои настройки. Можно расширить интересы в профиле."
                    if lang == "ru" else
                    "There are no fresh jobs matching your preferences right now. You can broaden your preferences."
                ),
            )
            return 0

        await self.application.bot.send_message(
            chat_id=user_id,
            text=(
                f"⭐ Бонус подписчика: {len(matched)} свежих вакансий."
                if lang == "ru" else
                f"⭐ Subscriber bonus: {len(matched)} fresh jobs."
            ),
        )
        sent = 0
        for job in matched:
            try:
                await self.application.bot.send_message(
                    chat_id=user_id,
                    text=self._job_text(job, lang),
                    reply_markup=self._job_keyboard(job, lang),
                    disable_web_page_preview=True,
                )
                sent += 1
            except Exception:
                continue
        self.db.log_event(
            user_id,
            "channel_subscriber_bonus_sent",
            {"count": sent, "language": lang},
        )
        return sent

    async def cmd_channel(self, update, context):
        user_id = update.effective_user.id if update.effective_user else None
        if not user_id:
            return
        self.db.log_event(user_id, "bot_to_channel_intent", {"source": "command"})
        subscribed = await self._channel_membership(user_id)
        if subscribed is True:
            self.db.log_event(user_id, "channel_bot_both", {"source": "command"})
        await self._send_channel_status(update.message, user_id, subscribed)

    async def cmd_start(self, update, context):
        user = update.effective_user
        user_id = user.id if user else None
        payload = str(context.args[0] if context.args else "")[:52]
        if user_id and payload.startswith("channel_"):
            language = self.db.get_user_language(user_id)
            if language:
                self.db.log_event(
                    user_id,
                    "channel_to_bot",
                    {"payload": payload, "language": language},
                )
            else:
                self.db.log_event(
                    user_id,
                    "channel_to_bot_arrival",
                    {"payload": payload, "language_pending": True},
                )

            # The information post uses channel_resume. Give immediate value:
            # show fresh jobs so the user can choose one and run Resume Match.
            if language and payload == "channel_resume":
                self.db.log_event(user_id, "channel_resume_entry", {"language": language})
                await self._send_first_value_preview(user_id, source="channel_resume")
                await self._send_retention_hub(update.message, user_id)
                return

        return await super().cmd_start(update, context)

    async def handle_callback(self, update, context):
        query = update.callback_query
        data = (query.data or "") if query else ""
        user_id = update.effective_user.id if update.effective_user else None
        if not query or not user_id:
            return await super().handle_callback(update, context)

        if data == "p11_channel":
            await self._ack_callback(query)
            self.db.log_event(user_id, "bot_to_channel_intent", {"source": "hub"})
            subscribed = await self._channel_membership(user_id)
            if subscribed is True:
                self.db.log_event(user_id, "channel_bot_both", {"source": "hub"})
            await self._send_channel_status(query.message, user_id, subscribed)
            return

        if data == "p11_channel_bonus":
            await self._ack_callback(query)
            subscribed = await self._channel_membership(user_id)
            if subscribed is not True:
                await self._send_channel_status(query.message, user_id, subscribed)
                return
            self.db.log_event(user_id, "channel_bot_both", {"source": "bonus"})
            await self._send_channel_bonus(user_id)
            return

        return await super().handle_callback(update, context)


core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot
Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = base.main
