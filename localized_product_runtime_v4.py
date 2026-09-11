"""Hotfix localized Telegram UX for callback latency and direct language switching.

This layer keeps the stable P8/P9 product behavior while making the request-driven
Vercel webhook practical for Telegram callbacks: language is cached per update,
the main hub avoids duplicate durable reads, and language can be changed in one
tap from every /start hub.
"""
from __future__ import annotations

from types import SimpleNamespace

import channel_bot as core
import localized_product_runtime_v3 as base
from product_i18n import t


DatabaseConnection = base.DatabaseConnection


class JobBot(base.JobBot):
    def _lang(self, user_id: int) -> str:
        cache = getattr(self, "_language_cache", None)
        if cache is None:
            cache = {}
            self._language_cache = cache
        uid = int(user_id)
        if uid not in cache:
            cache[uid] = self.db.get_user_language(uid) or "ru"
        return cache[uid]

    def _set_cached_language(self, user_id: int, language: str) -> None:
        cache = getattr(self, "_language_cache", None)
        if cache is None:
            cache = {}
            self._language_cache = cache
        cache[int(user_id)] = language

    @staticmethod
    def _language_keyboard(payload: str = ""):
        suffix = str(payload or "")[:46]
        ru_data = "p10_lang_ru" + (":" + suffix if suffix else "")
        en_data = "p10_lang_en" + (":" + suffix if suffix else "")
        return core.InlineKeyboardMarkup([[
            core.InlineKeyboardButton("🇷🇺 Русский", callback_data=ru_data),
            core.InlineKeyboardButton("🇬🇧 English", callback_data=en_data),
        ]])

    def _retention_keyboard_from_state(self, lang: str, settings: dict, searches: list):
        rows = [
            [core.InlineKeyboardButton(t(lang, "find3"), callback_data="growth_quick_fresh")],
            [
                core.InlineKeyboardButton(
                    t(lang, "alerts_on" if settings.get("alerts_enabled") else "alerts_off"),
                    callback_data="p8_alerts_toggle",
                ),
                core.InlineKeyboardButton(
                    t(lang, "daily_on" if settings.get("digest_enabled") else "daily_off"),
                    callback_data="p8_digest_toggle",
                ),
            ],
            [
                core.InlineKeyboardButton(
                    t(lang, "my_searches", count=len(searches)),
                    callback_data="p8_saved_searches",
                ),
                core.InlineKeyboardButton(t(lang, "preferences"), callback_data="growth_quick_setup"),
            ],
            [
                core.InlineKeyboardButton(
                    ("✓ " if lang == "ru" else "") + "🇷🇺 Русский",
                    callback_data="p10_lang_ru",
                ),
                core.InlineKeyboardButton(
                    ("✓ " if lang == "en" else "") + "🇬🇧 English",
                    callback_data="p10_lang_en",
                ),
            ],
        ]
        channel = str(core.Config.CHANNEL_ID or "")
        if channel.startswith("@"):
            rows.append([
                core.InlineKeyboardButton(
                    t(lang, "channel"),
                    url=f"https://t.me/{channel.lstrip('@')}",
                )
            ])
        return core.InlineKeyboardMarkup(rows)

    def _retention_keyboard(self, user_id: int):
        lang = self._lang(user_id)
        settings = self.db.get_user_settings(user_id)
        searches = self.db.list_saved_searches(user_id)
        return self._retention_keyboard_from_state(lang, settings, searches)

    async def _send_retention_hub(self, message, user_id: int, *, title: str = ""):
        # One language lookup (cached) + one settings read + one searches read.
        # P9 performed those reads twice per hub render, which made a serverless
        # /start take 15-25 seconds through the HTTP durable-store transport.
        lang = self._lang(user_id)
        settings = self.db.get_user_settings(user_id)
        searches = self.db.list_saved_searches(user_id)
        onboarding = bool(settings.get("onboarding_done"))
        intro = title or t(lang, "welcome_ready" if onboarding else "welcome_new")
        status = t(
            lang,
            "status",
            profile=t(lang, "ready" if onboarding else "not_ready"),
            alerts=t(lang, "on" if settings.get("alerts_enabled") else "off"),
            daily=t(lang, "on" if settings.get("digest_enabled") else "off"),
            searches=len(searches),
        )
        explainer = t(lang, "alerts_explain") + "\n\n" + t(lang, "daily_explain")
        await message.reply_text(
            f"{intro}\n\n{status}\n\n{explainer}",
            reply_markup=self._retention_keyboard_from_state(lang, settings, searches),
            disable_web_page_preview=True,
        )
        self.db.log_event(
            user_id,
            "p10_hub_view",
            {"language": lang, "searches": len(searches)},
        )

    async def cmd_language(self, update, context):
        user_id = update.effective_user.id if update.effective_user else None
        if not user_id:
            return
        await update.message.reply_text(
            t(self._lang(user_id), "choose_language"),
            reply_markup=self._language_keyboard(),
        )

    async def _ack_callback(self, query, text: str | None = None, **kwargs) -> None:
        # Callback acknowledgement is cosmetic (it clears Telegram's spinner).
        # A stale/duplicated callback must never prevent the actual user action.
        try:
            await query.answer(text, **kwargs)
        except Exception:
            return

    async def handle_callback(self, update, context):
        query = update.callback_query
        data = (query.data or "") if query else ""
        user_id = update.effective_user.id if update.effective_user else None
        if not query or not user_id:
            return await super().handle_callback(update, context)

        language_data = (
            data.startswith("p10_lang_ru")
            or data.startswith("p10_lang_en")
            or data.startswith("p9_lang_ru")
            or data.startswith("p9_lang_en")
        )
        if language_data:
            lang = "ru" if (data.startswith("p10_lang_ru") or data.startswith("p9_lang_ru")) else "en"
            parts = data.split(":", 1)
            payload = parts[1] if len(parts) > 1 else ""

            # Acknowledge first, before any network DB work. This is essential on
            # request-driven serverless runtimes where durable calls can be slow.
            await self._ack_callback(query, "Русский" if lang == "ru" else "English")
            self.db.set_user_language(user_id, lang)
            self._set_cached_language(user_id, lang)

            if payload:
                old_args = list(getattr(context, "args", []) or [])
                try:
                    context.args = [payload]
                    proxy = SimpleNamespace(
                        effective_user=update.effective_user,
                        message=query.message,
                    )
                    return await self.cmd_start(proxy, context)
                finally:
                    context.args = old_args

            await self._send_retention_hub(query.message, user_id)
            return

        if data == "p9_switch_language":
            await self._ack_callback(query)
            await query.message.reply_text(
                t(self._lang(user_id), "choose_language"),
                reply_markup=self._language_keyboard(),
            )
            return

        return await super().handle_callback(update, context)


core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot
Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = base.main
