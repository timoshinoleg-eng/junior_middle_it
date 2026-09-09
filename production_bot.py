"""Canonical production runtime: growth foundation + Resume Match MVP.

Importing ``channel_bot_v2`` installs the P1 growth layer. This module adds the
P2 resume utility and a durable PostgreSQL job-payload cache, then re-exports the
same Config/main/cron surface used by deployment entrypoints.
"""
from __future__ import annotations

import json
import logging
from typing import Dict

import channel_bot as core
import channel_bot_v2 as growth
from resume_matcher import analyze_resume_match, format_resume_match_report

try:
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover
    Jsonb = None

logger = logging.getLogger(__name__)


class DatabaseConnection(growth.DatabaseConnection):
    """P1 DB plus durable job payloads required by long-lived vacancy CTAs."""

    def __init__(self, db_path: str = "jobs.db"):
        super().__init__(db_path)
        if self._growth_store is not None:
            with self._growth_store._ensure_conn().cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS growth_job_payloads (
                        hash TEXT PRIMARY KEY,
                        payload JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_growth_job_payloads_updated "
                    "ON growth_job_payloads(updated_at)"
                )

    def save_job_payload(self, job_hash: str, job: Dict) -> None:
        # Preserve the local cache for backward compatibility and fast access.
        super().save_job_payload(job_hash, job)
        if not job_hash or self._growth_store is None or Jsonb is None:
            return
        try:
            payload = core.serialize_job_payload(job) if core.GROWTH_UTILS_AVAILABLE else dict(job)
            with self._growth_store._ensure_conn().cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO growth_job_payloads (hash, payload, created_at, updated_at)
                    VALUES (%s, %s, NOW(), NOW())
                    ON CONFLICT (hash) DO UPDATE SET
                        payload=EXCLUDED.payload,
                        updated_at=NOW()
                    """,
                    (str(job_hash), Jsonb(payload)),
                )
                # Old callback payloads have little product value after 45 days.
                cur.execute(
                    "DELETE FROM growth_job_payloads WHERE updated_at < NOW() - INTERVAL '45 days'"
                )
        except Exception as exc:
            logger.error("Durable job payload write failed: %s", exc)
            if core.os.getenv("REQUIRE_DURABLE_GROWTH", "false").lower() == "true":
                raise

    def get_job_payload(self, job_hash: str):
        local = super().get_job_payload(job_hash)
        if local:
            return local
        if not job_hash or self._growth_store is None:
            return None
        try:
            with self._growth_store._ensure_conn().cursor() as cur:
                cur.execute(
                    "SELECT payload FROM growth_job_payloads WHERE hash = %s",
                    (str(job_hash),),
                )
                row = cur.fetchone()
            if not row:
                return None
            payload = row[0]
            return payload if isinstance(payload, dict) else json.loads(payload)
        except Exception as exc:
            logger.error("Durable job payload read failed: %s", exc)
            return None


class JobBot(growth.JobBot):
    """Adds an in-memory, privacy-first resume matching conversation."""

    def __init__(self, application, db):
        super().__init__(application, db)
        self.resume_sessions: Dict[int, Dict] = {}

    async def handle_callback(self, update, context):
        query = update.callback_query
        data = query.data or ""
        user_id = update.effective_user.id

        if data.startswith("resume_match:"):
            job_hash = data.split(":", 1)[1]
            job = self.db.get_job_payload(job_hash)
            if not job:
                await query.answer("Карточка вакансии устарела. Открой более свежую вакансию.", show_alert=True)
                return
            self.resume_sessions[user_id] = {"job_hash": job_hash, "job": job}
            self.db.log_event(user_id, "resume_match_started", {"hash": job_hash})
            await query.answer()
            await query.message.reply_text(
                "📄 Пришли текст резюме одним сообщением.\n\n"
                "Я сравню его только с этой вакансией: навыки, ключевые слова и роль. "
                "Текст резюме не записывается в БД и удаляется из сессии после расчёта.\n\n"
                "Важно: это эвристический помощник, а не официальный ATS-score.",
                reply_markup=core.InlineKeyboardMarkup([[
                    core.InlineKeyboardButton("❌ Отмена", callback_data="resume_cancel")
                ]]),
            )
            return

        if data == "resume_cancel":
            self.resume_sessions.pop(user_id, None)
            await query.answer("Отменено")
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            return

        if data == "resume_alerts_on":
            self.db.save_user_settings(user_id, {"alerts_enabled": True})
            self.db.log_event(user_id, "alerts_on", {"source": "resume_match"})
            await query.answer("Алерты включены", show_alert=True)
            return

        return await super().handle_callback(update, context)

    async def handle_setup_text(self, update, context):
        user_id = update.effective_user.id if update.effective_user else None
        if user_id and user_id in self.resume_sessions:
            text = (update.message.text or "").strip()
            if len(text) < 120:
                await update.message.reply_text(
                    "Нужно чуть больше текста — вставь основные разделы резюме целиком "
                    "(опыт, проекты, навыки)."
                )
                return
            if len(text) > 30000:
                text = text[:30000]

            session = self.resume_sessions.pop(user_id)
            job = session["job"]
            report = analyze_resume_match(text, job)
            # Do not put resume text or extracted personal data into analytics.
            self.db.log_event(
                user_id,
                "resume_match_completed",
                {
                    "hash": session["job_hash"],
                    "score": report.score,
                    "required_skills": len(report.required_skills),
                    "matched_skills": len(report.matched_skills),
                },
            )
            await update.message.reply_text(
                format_resume_match_report(report, job),
                reply_markup=core.InlineKeyboardMarkup([
                    [core.InlineKeyboardButton("⚡ Включить алерты", callback_data="resume_alerts_on")],
                    [core.InlineKeyboardButton("🎯 Уточнить профиль", callback_data="growth_quick_setup")],
                ]),
                disable_web_page_preview=True,
            )
            return

        return await super().handle_setup_text(update, context)


# Install the final runtime classes into channel_bot's global extension points.
core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot

Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = core.main
