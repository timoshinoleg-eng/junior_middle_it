"""Small hardening layer for language switching and localized saved-search callbacks."""
from __future__ import annotations

from types import SimpleNamespace

import channel_bot as core
import localized_product_runtime as base
from product_i18n import t
from saved_searches import build_search_name


DatabaseConnection = base.DatabaseConnection


class JobBot(base.JobBot):
    async def handle_callback(self, update, context):
        query = update.callback_query
        data = (query.data or "") if query else ""
        user_id = update.effective_user.id if update.effective_user else None
        if not query or not user_id:
            return await super().handle_callback(update, context)

        if data.startswith("p9_lang_ru") or data.startswith("p9_lang_en"):
            lang = "ru" if data.startswith("p9_lang_ru") else "en"
            parts = data.split(":", 1)
            payload = parts[1] if len(parts) > 1 else ""
            self.db.set_user_language(user_id, lang)
            await query.answer("Русский" if lang == "ru" else "English")
            if payload:
                old_args = list(getattr(context, "args", []) or [])
                try:
                    context.args = [payload]
                    proxy = SimpleNamespace(effective_user=update.effective_user, message=query.message)
                    return await self.cmd_start(proxy, context)
                finally:
                    context.args = old_args
            await self._send_retention_hub(query.message, user_id)
            return

        lang = self._lang(user_id)
        if data == "resume_cancel":
            self.resume_sessions.pop(user_id, None)
            await query.answer("Отменено" if lang == "ru" else "Cancelled")
            return

        if data == "saved_search_from_resume":
            spec = self.pending_saved_searches.get(user_id)
            if not spec:
                await query.answer(
                    "Сначала проверь резюме для свежей вакансии" if lang == "ru" else "Check your resume against a fresh job first",
                    show_alert=True,
                )
                return
            search = self.db.create_saved_search(user_id, spec)
            self.db.save_user_settings(user_id, {"alerts_enabled": True})
            self.db.log_event(user_id, "saved_search_created", {"search_id": search.id, "source": "resume_match"})
            await query.answer(
                "Подборка сохранена. Новые подходящие вакансии будут приходить сразу." if lang == "ru"
                else "Search saved. You will be notified when new matching jobs appear.",
                show_alert=True,
            )
            await self._send_retention_hub(query.message, user_id)
            return

        if data == "saved_search_from_profile":
            settings = self.db.get_user_settings(user_id)
            spec = {
                "categories": settings.get("enabled_categories") or [],
                "skills": settings.get("skills") or "",
                "min_salary_filter": settings.get("min_salary_filter") or 0,
                "hide_senior": settings.get("hide_senior", True),
            }
            spec["name"] = build_search_name(
                spec["categories"],
                spec["skills"],
                "Мои настройки" if lang == "ru" else "My preferences",
            )
            search = self.db.create_saved_search(user_id, spec)
            self.db.save_user_settings(user_id, {"alerts_enabled": True})
            self.db.log_event(user_id, "saved_search_created", {"search_id": search.id, "source": "profile"})
            await query.answer(
                "Настройки сохранены как подборка" if lang == "ru" else "Preferences saved as a search",
                show_alert=True,
            )
            await self._send_retention_hub(query.message, user_id)
            return

        return await super().handle_callback(update, context)


core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot
Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = base.main
