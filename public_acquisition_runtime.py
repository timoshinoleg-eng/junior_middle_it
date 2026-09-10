"""P7 production runtime: attribute public-site traffic and preserve deep-link intent.

The public landing is read-only; attribution happens only after a user explicitly
opens the Telegram bot. This final runtime layer turns ``web_*`` and
``resume_*`` start payloads into useful in-bot continuation without changing the
existing P1-P6 behavior.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import channel_bot as core
import referral_runtime as referral
from p7_growth_metrics import compute_growth_stats


class DatabaseConnection(referral.DatabaseConnection):
    """P7 storage plus cohort-correct production growth metrics."""

    def growth_stats(self, days: int = 7):
        if self._growth_store is not None:
            return compute_growth_stats(self._growth_store, days)
        return super().growth_stats(days)


class JobBot(referral.JobBot):
    """Continue landing-page intent after Telegram /start deep links."""

    @staticmethod
    def _safe_apply_url(value: str) -> str:
        raw = str(value or "").strip()
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return ""
        return raw if parsed.scheme in {"http", "https"} and parsed.netloc else ""

    async def cmd_start(self, update, context):
        user = update.effective_user
        user_id = user.id if user else None
        payload = context.args[0] if context.args else ""
        payload = str(payload or "")[:64]

        # P5 already owns the complete resume_* deep-link lifecycle: canonical
        # start analytics, resume attribution, session creation, stale-card UX,
        # PDF/DOCX-aware prompt, and resume_match_started. P7 must only add the
        # public-site attribution and delegate once. Calling super() first and
        # then handling resume_* again caused duplicate prompts and session init.
        if user_id and payload.startswith("resume_"):
            job_hash = payload[7:]
            if job_hash:
                self.db.log_event(
                    user_id,
                    "web_resume_start",
                    {"surface": "public_site", "hash": job_hash},
                )
            return await super().cmd_start(update, context)

        # Keep canonical P1-P6 registration/onboarding semantics for all other
        # start payloads before adding P7 landing-page continuation.
        await super().cmd_start(update, context)

        if not user_id or not payload:
            return

        if payload == "web_home":
            self.db.log_event(user_id, "web_landing_start", {"surface": "public_site"})
            return

        if payload.startswith("web_"):
            job_hash = payload[4:]
            if not job_hash:
                return
            self.db.log_event(
                user_id,
                "web_job_start",
                {"surface": "public_site", "hash": job_hash},
            )
            job = self.db.get_job_payload(job_hash)
            if not job:
                await update.message.reply_text(
                    "Эта веб-карточка уже устарела. Покажу свежие вакансии вместо неё — нажми «Показать свежие»."
                )
                return

            title = str(job.get("title") or "Вакансия")[:180]
            company = str(job.get("company") or "")[:120]
            location = str(job.get("location") or "")[:100]
            salary = str(job.get("salary") or "")[:80]
            lines = [f"🔗 Вакансия с сайта\n\n{title}"]
            if company:
                lines.append(company)
            if location:
                lines.append(f"🌍 {location}")
            if salary and salary.lower() not in {"не указана", "none", "not specified"}:
                lines.append(f"💰 {salary}")

            buttons = []
            apply_url = self._safe_apply_url(job.get("url") or "")
            if apply_url:
                buttons.append([core.InlineKeyboardButton("🚀 Откликнуться", url=apply_url)])
            buttons.append([
                core.InlineKeyboardButton(
                    "📄 Проверить резюме",
                    callback_data=f"resume_match:{job_hash}",
                )
            ])
            buttons.append([
                core.InlineKeyboardButton("🎯 Настроить подборку", callback_data="growth_quick_setup")
            ])
            await update.message.reply_text(
                "\n".join(lines),
                reply_markup=core.InlineKeyboardMarkup(buttons),
                disable_web_page_preview=True,
            )
            return


core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot

Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = referral.main
