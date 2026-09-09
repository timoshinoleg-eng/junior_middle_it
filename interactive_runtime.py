"""P5 interactive runtime: private Resume Match files + actionable growth metrics.

This layer is used only by the long-polling interactive bot.  Vercel collection
keeps importing ``content_runtime`` and therefore does not gain upload handling
or any new ingestion behavior.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict

import channel_bot as core
import content_runtime as runtime
from resume_documents import MAX_FILE_BYTES, ResumeDocumentError, extract_resume_document
from resume_matcher import analyze_resume_match, format_resume_match_report
from saved_searches import suggested_search_from_resume

logger = logging.getLogger(__name__)


class DatabaseConnection(runtime.DatabaseConnection):
    """P4 database plus product-utility metrics derived from existing events."""

    def utility_stats(self, days: int = 7) -> Dict:
        days = max(1, min(int(days), 90))
        cutoff_aware = datetime.now(timezone.utc) - timedelta(days=days)

        if self._growth_store is not None:
            with self._growth_store._ensure_conn().cursor() as cur:
                cur.execute(
                    "SELECT user_id, name, props FROM growth_events WHERE ts >= %s",
                    (cutoff_aware,),
                )
                rows = cur.fetchall()
                cur.execute(
                    """
                    SELECT COUNT(*), COUNT(DISTINCT user_id)
                    FROM growth_saved_searches
                    WHERE enabled = TRUE
                    """
                )
                saved_counts = cur.fetchone() or (0, 0)
        else:
            cutoff_sqlite = cutoff_aware.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
            rows = self.fetchall(
                "SELECT user_id, name, props FROM events WHERE ts >= ?",
                (cutoff_sqlite,),
            )
            saved_counts = self.fetchone(
                "SELECT COUNT(*), COUNT(DISTINCT user_id) FROM saved_searches WHERE enabled = 1"
            ) or (0, 0)

        resume_started = set()
        resume_completed = set()
        resume_document = set()
        saved_any = set()
        saved_from_resume = set()
        saved_from_profile = set()
        alert_users = set()
        magnet_users = set()
        scores = []
        magnet_payloads: Dict[str, int] = {}
        salary_posts = 0
        picks_posts = 0

        for raw_uid, name, raw_props in rows:
            uid = int(raw_uid) if raw_uid is not None else None
            if isinstance(raw_props, dict):
                props = raw_props
            else:
                try:
                    props = json.loads(raw_props or "{}")
                except Exception:
                    props = {}

            if name == "resume_match_started" and uid is not None:
                resume_started.add(uid)
            elif name == "resume_match_completed" and uid is not None:
                resume_completed.add(uid)
                try:
                    scores.append(int(props.get("score")))
                except (TypeError, ValueError):
                    pass
            elif name == "resume_match_document_completed" and uid is not None:
                resume_document.add(uid)
            elif name == "saved_search_created" and uid is not None:
                saved_any.add(uid)
                source = str(props.get("source") or "")
                if source == "resume_match":
                    saved_from_resume.add(uid)
                elif source == "profile":
                    saved_from_profile.add(uid)
            elif name == "realtime_alert_sent" and uid is not None:
                alert_users.add(uid)
            elif name == "start" and uid is not None:
                payload = str(props.get("payload") or "")
                if payload.startswith("magnet_"):
                    magnet_users.add(uid)
                    magnet_payloads[payload] = magnet_payloads.get(payload, 0) + 1
            elif name == "salary_magnet_posted":
                salary_posts += 1
            elif name == "weekly_picks_posted":
                picks_posts += 1

        completion_pct = (
            round(100.0 * len(resume_completed) / len(resume_started), 1)
            if resume_started else 0.0
        )
        resume_saved_users = resume_completed & saved_from_resume
        resume_to_search_pct = (
            round(100.0 * len(resume_saved_users) / len(resume_completed), 1)
            if resume_completed else 0.0
        )
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0

        return {
            "days": days,
            "resume_started_users": len(resume_started),
            "resume_completed_users": len(resume_completed),
            "resume_completion_pct": completion_pct,
            "resume_document_users": len(resume_document),
            "resume_avg_score": avg_score,
            "saved_search_users": len(saved_any),
            "saved_from_resume_users": len(saved_from_resume),
            "saved_from_profile_users": len(saved_from_profile),
            "resume_to_search_pct": resume_to_search_pct,
            "active_saved_searches": int(saved_counts[0] or 0),
            "active_saved_search_users": int(saved_counts[1] or 0),
            "alert_users": len(alert_users),
            "magnet_start_users": len(magnet_users),
            "magnet_payloads": sorted(magnet_payloads.items(), key=lambda item: (-item[1], item[0])),
            "salary_posts": salary_posts,
            "weekly_picks_posts": picks_posts,
        }


class JobBot(runtime.JobBot):
    """Interactive product UX for deep-linked and file-based Resume Match."""

    @staticmethod
    def _resume_prompt(job: Dict) -> str:
        title = str(job.get("title") or "вакансия")
        company = str(job.get("company") or "").strip()
        target = f"{title} @ {company}" if company else title
        return (
            f"📄 Resume Match · {target}\n\n"
            "Пришли резюме одним сообщением: текстом, PDF или DOCX.\n"
            "Я сравню навыки, ключевые слова и роль только с этой вакансией.\n\n"
            "Файл и текст резюме не записываются в БД. Для PDF/DOCX обработка идёт в памяти; "
            "после расчёта содержимое удаляется из сессии.\n\n"
            "Важно: это эвристический помощник, а не официальный ATS-score."
        )

    async def cmd_start(self, update, context):
        user = update.effective_user
        user_id = user.id if user else None
        payload = context.args[0] if context.args else None

        if user_id and payload and str(payload).startswith("resume_"):
            job_hash = str(payload)[7:47]
            self.db.log_event(user_id, "start", {"payload": payload})
            self.db.log_event(user_id, "resume_attributed_start", {"hash": job_hash})
            self.db.maybe_unlock_premium(user_id)
            job = self.db.get_job_payload(job_hash)
            if not job:
                await update.message.reply_text(
                    "Эта карточка вакансии уже устарела. Открой свежую вакансию в канале и снова нажми «Проверить резюме»."
                )
                return
            self.resume_sessions[user_id] = {"job_hash": job_hash, "job": job}
            self.db.log_event(
                user_id,
                "resume_match_started",
                {"hash": job_hash, "source": "deeplink"},
            )
            await update.message.reply_text(
                self._resume_prompt(job),
                reply_markup=core.InlineKeyboardMarkup([[
                    core.InlineKeyboardButton("❌ Отмена", callback_data="resume_cancel")
                ]]),
                disable_web_page_preview=True,
            )
            return

        return await super().cmd_start(update, context)

    async def _finish_document_resume_match(self, update, user_id: int, text: str, kind: str) -> None:
        session = self.resume_sessions.pop(user_id)
        job = session["job"]
        report = analyze_resume_match(text, job)
        self.pending_saved_searches[user_id] = suggested_search_from_resume(job, report.matched_skills)
        event_props = {
            "hash": session["job_hash"],
            "score": report.score,
            "required_skills": len(report.required_skills),
            "matched_skills": len(report.matched_skills),
            "input_type": kind,
        }
        self.db.log_event(user_id, "resume_match_completed", event_props)
        self.db.log_event(
            user_id,
            "resume_match_document_completed",
            {"hash": session["job_hash"], "input_type": kind},
        )
        await update.message.reply_text(
            format_resume_match_report(report, job),
            reply_markup=core.InlineKeyboardMarkup([
                [core.InlineKeyboardButton("🔔 Сохранить такой поиск", callback_data="saved_search_from_resume")],
                [core.InlineKeyboardButton("🎯 Уточнить профиль", callback_data="growth_quick_setup")],
            ]),
            disable_web_page_preview=True,
        )

    async def handle_setup_text(self, update, context):
        message = update.message
        document = getattr(message, "document", None) if message else None
        user_id = update.effective_user.id if update.effective_user else None

        if document is not None:
            if not user_id or user_id not in self.resume_sessions:
                await message.reply_text(
                    "PDF/DOCX принимаю после выбора конкретной вакансии: нажми «📄 Проверить резюме» под её карточкой."
                )
                return
            if document.file_size and int(document.file_size) > MAX_FILE_BYTES:
                await message.reply_text("Файл слишком большой: максимум 5 МБ.")
                return

            try:
                tg_file = await context.bot.get_file(document.file_id)
                payload = await tg_file.download_as_bytearray()
                parsed = extract_resume_document(
                    payload,
                    filename=document.file_name or "",
                    mime_type=document.mime_type or "",
                )
            except ResumeDocumentError as exc:
                await message.reply_text(str(exc))
                return
            except Exception as exc:
                # Do not log filename, file bytes or extracted resume text.
                logger.warning("Resume document processing failed: %s", type(exc).__name__)
                await message.reply_text(
                    "Не удалось обработать файл. Попробуй ещё раз или вставь текст резюме сообщением."
                )
                return

            await self._finish_document_resume_match(update, user_id, parsed.text, parsed.kind)
            return

        return await super().handle_setup_text(update, context)

    async def cmd_stats_growth(self, update, context):
        # The parent prints the acquisition/activation/retention funnel.  This
        # extension adds only metrics that answer whether the new utilities are
        # becoming real retention loops.
        if not await self.check_admin(update):
            return
        await super().cmd_stats_growth(update, context)

        days = core.Config.GROWTH_STATS_DAYS
        if context.args:
            try:
                days = max(1, min(int(context.args[0]), 90))
            except ValueError:
                pass
        stats = self.db.utility_stats(days)
        lines = [
            f"🧲 Product utility · {days}d",
            f"Resume Match: {stats['resume_started_users']} start → {stats['resume_completed_users']} complete "
            f"({stats['resume_completion_pct']}%)",
            f"PDF/DOCX users: {stats['resume_document_users']} · avg score: {stats['resume_avg_score']}",
            f"Resume → Saved Search: {stats['resume_to_search_pct']}%",
            f"Saved-search creators: {stats['saved_search_users']} "
            f"(resume:{stats['saved_from_resume_users']} · profile:{stats['saved_from_profile_users']})",
            f"Active searches: {stats['active_saved_searches']} / {stats['active_saved_search_users']} users",
            f"Realtime alert recipients: {stats['alert_users']}",
            f"Content-magnet starts: {stats['magnet_start_users']}",
            f"Magnet posts: salary {stats['salary_posts']} · picks {stats['weekly_picks_posts']}",
        ]
        if stats["magnet_payloads"]:
            lines.append("Top magnet campaigns:")
            for payload, count in stats["magnet_payloads"][:6]:
                lines.append(f"  {payload}: {count}")
        await update.message.reply_text("\n".join(lines)[:4000])


# Install P5 classes after all previous runtime layers have been imported.
core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot

Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY


async def main():
    """Run core.main with PDF/DOCX routed into the existing text/setup handler."""
    from telegram.ext import filters as tg_filters

    original_text_filter = tg_filters.TEXT
    document_filter = (
        tg_filters.Document.PDF
        | tg_filters.Document.DOCX
        | tg_filters.Document.FileExtension("pdf")
        | tg_filters.Document.FileExtension("docx")
    )
    tg_filters.TEXT = original_text_filter | document_filter
    try:
        await core.main()
    finally:
        tg_filters.TEXT = original_text_filter
