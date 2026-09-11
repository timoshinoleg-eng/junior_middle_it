"""Final localized product layer: recurring personal deliveries follow the chosen language."""
from __future__ import annotations

from typing import Dict, List

import channel_bot as core
import localized_product_runtime_v2 as base


DatabaseConnection = base.DatabaseConnection


class JobBot(base.JobBot):
    async def cmd_start(self, update, context):
        """Preserve referral first-touch before language selection creates user history."""
        user = update.effective_user
        user_id = user.id if user else None
        if not user_id:
            return await super().cmd_start(update, context)
        payload = str(context.args[0] if context.args else "")[:52]
        language = self.db.get_user_language(user_id)

        if not language and payload.startswith("ref_"):
            kind, referrer_id = core.parse_start_payload([payload]) if core.GROWTH_UTILS_AVAILABLE else (None, None)
            if kind == "ref" and referrer_id:
                self.db.register_referral(user_id, referrer_id)
            return await super().cmd_start(update, context)

        if language and payload.startswith("ref_"):
            self.db.log_event(user_id, "start", {"payload": payload, "language": language})
            self.db.maybe_unlock_premium(user_id)
            await self._send_retention_hub(update.message, user_id)
            return

        if language and payload == "invite":
            self.db.log_event(user_id, "start", {"payload": payload, "language": language})
            return await self.cmd_ref(update, context)

        return await super().cmd_start(update, context)

    async def send_personal_digest(self, user_id: int) -> int:
        """Send the once-a-day roundup using the user's selected language."""
        lang = self._lang(user_id)
        settings = self._effective_profile(user_id)
        limit = self._digest_limit(settings)
        jobs = self.db.recent_jobs_for_digest(
            hours=core.Config.PERSONAL_DIGEST_LOOKBACK_HOURS,
            limit=80,
        )
        matched: List[Dict] = []
        for job in jobs:
            if core.GROWTH_UTILS_AVAILABLE and not core.job_matches_profile(job, settings):
                continue
            matched.append(job)
            if len(matched) >= limit:
                break
        if not matched:
            return 0

        header = (
            f"📅 Твоя подборка на сегодня: {len(matched)} вакансий.\n"
            "Я выбрал их по твоим интересам и настройкам."
            if lang == "ru" else
            f"📅 Your daily roundup: {len(matched)} jobs.\n"
            "I selected them using your preferences."
        )
        try:
            await self.application.bot.send_message(chat_id=user_id, text=header)
        except Exception:
            return 0

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
        self.db.log_event(user_id, "personal_digest_sent", {
            "count": sent,
            "language": lang,
            "surface": "localized_daily_roundup",
        })
        return sent

    async def push_realtime_alerts(self, new_jobs: List[Dict]) -> int:
        """Keep saved-search matching semantics while localizing notifications and cards."""
        if not core.Config.ENABLE_REALTIME_ALERTS or not new_jobs:
            return 0
        total = 0
        for uid in self.db.list_alert_subscribers():
            lang = self._lang(uid)
            searches = self.db.list_saved_searches(uid)
            settings = self._effective_profile(uid)
            matched: List[Dict] = []
            for job in new_jobs:
                if searches:
                    fits = any(
                        core.job_matches_profile(job, search.as_profile())
                        for search in searches
                    ) if core.GROWTH_UTILS_AVAILABLE else True
                else:
                    fits = core.job_matches_profile(job, settings) if core.GROWTH_UTILS_AVAILABLE else True
                if not fits:
                    continue
                matched.append(job)
                if len(matched) >= core.Config.REALTIME_ALERTS_MAX:
                    break
            if not matched:
                continue

            if lang == "ru":
                basis = "по твоим сохранённым подборкам" if searches else "по твоим настройкам"
                header = f"🔔 Нашёл {len(matched)} новые вакансии {basis}."
            else:
                basis = "your saved searches" if searches else "your preferences"
                header = f"🔔 I found {len(matched)} new jobs matching {basis}."
            try:
                await self.application.bot.send_message(chat_id=uid, text=header)
            except Exception:
                continue

            sent = 0
            for job in matched:
                try:
                    await self.application.bot.send_message(
                        chat_id=uid,
                        text=self._job_text(job, lang),
                        reply_markup=self._job_keyboard(job, lang),
                        disable_web_page_preview=True,
                    )
                    sent += 1
                    total += 1
                except Exception:
                    continue
            self.db.log_event(uid, "realtime_alert_sent", {
                "count": sent,
                "saved_search": bool(searches),
                "language": lang,
            })
        return total

    async def cmd_ref(self, update, context):
        """Keep the optional referral feature understandable in both languages."""
        user_id = update.effective_user.id if update.effective_user else None
        if not user_id:
            return
        lang = self._lang(user_id)
        self.db.log_event(user_id, "ref_view", {"surface": "localized_referral", "language": lang})
        if not core.Config.BOT_USERNAME or not core.GROWTH_UTILS_AVAILABLE:
            await update.message.reply_text(
                "Приглашения временно недоступны." if lang == "ru" else "Invites are temporarily unavailable."
            )
            return
        link = core.build_referral_link(core.Config.BOT_USERNAME, user_id)
        stats = self.db.referral_progress(user_id, days=7)
        if lang == "ru":
            text = (
                "👥 Пригласить друга\n\n"
                f"Твоя ссылка:\n{link}\n\n"
                f"Приглашено: {stats['accepted_total']} · завершили настройку: {stats['activated_total']}.\n"
                "Приглашённый засчитывается после того, как настроит свой поиск вакансий."
            )
            button = "📤 Поделиться ссылкой"
        else:
            text = (
                "👥 Invite a friend\n\n"
                f"Your link:\n{link}\n\n"
                f"Invited: {stats['accepted_total']} · completed setup: {stats['activated_total']}.\n"
                "An invite counts after the new user finishes setting up their job search."
            )
            button = "📤 Share invite link"
        share_url = f"https://t.me/share/url?url={link}"
        await update.message.reply_text(
            text,
            reply_markup=core.InlineKeyboardMarkup([[core.InlineKeyboardButton(button, url=share_url)]]),
            disable_web_page_preview=True,
        )


core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot
Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = base.main
