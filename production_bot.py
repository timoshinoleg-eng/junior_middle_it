"""Canonical production runtime: growth foundation, Resume Match, Saved Searches."""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List

import channel_bot as core
import channel_bot_v2 as growth
from resume_matcher import analyze_resume_match, format_resume_match_report
from saved_searches import (
    SavedSearch,
    build_search_name,
    normalize_categories,
    normalize_skills,
    saved_search_from_row,
    search_fingerprint,
    suggested_search_from_resume,
)

try:
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover
    Jsonb = None

logger = logging.getLogger(__name__)


class DatabaseConnection(growth.DatabaseConnection):
    """P1 storage plus durable vacancy payloads and saved searches."""

    def __init__(self, db_path: str = "jobs.db"):
        super().__init__(db_path)
        # SQLite fallback remains useful for local/dev and existing deployments.
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                categories TEXT NOT NULL DEFAULT '',
                skills TEXT NOT NULL DEFAULT '',
                min_salary_filter INTEGER NOT NULL DEFAULT 0,
                hide_senior INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1,
                fingerprint TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, fingerprint)
            )
            """
        )
        self.execute("CREATE INDEX IF NOT EXISTS idx_saved_searches_user ON saved_searches(user_id, enabled)")

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
                    "CREATE INDEX IF NOT EXISTS idx_growth_job_payloads_updated ON growth_job_payloads(updated_at)"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS growth_saved_searches (
                        id BIGSERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        name TEXT NOT NULL,
                        categories TEXT NOT NULL DEFAULT '',
                        skills TEXT NOT NULL DEFAULT '',
                        min_salary_filter INTEGER NOT NULL DEFAULT 0,
                        hide_senior BOOLEAN NOT NULL DEFAULT TRUE,
                        enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        fingerprint TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE(user_id, fingerprint)
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_growth_saved_searches_user "
                    "ON growth_saved_searches(user_id, enabled)"
                )

    def save_job_payload(self, job_hash: str, job: Dict) -> None:
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
                    ON CONFLICT (hash) DO UPDATE SET payload=EXCLUDED.payload, updated_at=NOW()
                    """,
                    (str(job_hash), Jsonb(payload)),
                )
                cur.execute("DELETE FROM growth_job_payloads WHERE updated_at < NOW() - INTERVAL '45 days'")
        except Exception as exc:
            logger.error("Durable job payload write failed: %s", exc)
            if os.getenv("REQUIRE_DURABLE_GROWTH", "false").lower() == "true":
                raise

    def get_job_payload(self, job_hash: str):
        local = super().get_job_payload(job_hash)
        if local:
            return local
        if not job_hash or self._growth_store is None:
            return None
        try:
            with self._growth_store._ensure_conn().cursor() as cur:
                cur.execute("SELECT payload FROM growth_job_payloads WHERE hash = %s", (str(job_hash),))
                row = cur.fetchone()
            if not row:
                return None
            payload = row[0]
            return payload if isinstance(payload, dict) else json.loads(payload)
        except Exception as exc:
            logger.error("Durable job payload read failed: %s", exc)
            return None

    @staticmethod
    def _normalize_search_spec(spec: Dict) -> Dict:
        categories = normalize_categories(spec.get("categories") or [])
        skills = normalize_skills(spec.get("skills") or "")
        min_salary = int(spec.get("min_salary_filter") or 0)
        hide_senior = bool(spec.get("hide_senior", True))
        name = str(spec.get("name") or build_search_name(categories, skills))[:80]
        return {
            "name": name,
            "categories": categories,
            "skills": skills,
            "min_salary_filter": min_salary,
            "hide_senior": hide_senior,
            "fingerprint": search_fingerprint(categories, skills, min_salary, hide_senior),
        }

    def create_saved_search(self, user_id: int, spec: Dict) -> SavedSearch:
        s = self._normalize_search_spec(spec)
        cats = ",".join(s["categories"])
        if self._growth_store is not None:
            with self._growth_store._ensure_conn().cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO growth_saved_searches (
                        user_id, name, categories, skills, min_salary_filter,
                        hide_senior, enabled, fingerprint, created_at, updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,TRUE,%s,NOW(),NOW())
                    ON CONFLICT (user_id, fingerprint) DO UPDATE SET
                        name=EXCLUDED.name, enabled=TRUE, updated_at=NOW()
                    RETURNING id,user_id,name,categories,skills,min_salary_filter,hide_senior,enabled
                    """,
                    (
                        int(user_id), s["name"], cats, s["skills"], s["min_salary_filter"],
                        s["hide_senior"], s["fingerprint"],
                    ),
                )
                row = cur.fetchone()
            return saved_search_from_row(row)

        self.execute(
            """
            INSERT INTO saved_searches (
                user_id,name,categories,skills,min_salary_filter,hide_senior,
                enabled,fingerprint,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            ON CONFLICT(user_id,fingerprint) DO UPDATE SET
                name=excluded.name, enabled=1, updated_at=CURRENT_TIMESTAMP
            """,
            (
                int(user_id), s["name"], cats, s["skills"], s["min_salary_filter"],
                1 if s["hide_senior"] else 0, s["fingerprint"],
            ),
        )
        row = self.fetchone(
            """
            SELECT id,user_id,name,categories,skills,min_salary_filter,hide_senior,enabled
            FROM saved_searches WHERE user_id=? AND fingerprint=?
            """,
            (int(user_id), s["fingerprint"]),
        )
        return saved_search_from_row(row)

    def list_saved_searches(self, user_id: int, enabled_only: bool = True) -> List[SavedSearch]:
        if self._growth_store is not None:
            sql = (
                "SELECT id,user_id,name,categories,skills,min_salary_filter,hide_senior,enabled "
                "FROM growth_saved_searches WHERE user_id=%s"
            )
            params = [int(user_id)]
            if enabled_only:
                sql += " AND enabled=TRUE"
            sql += " ORDER BY updated_at DESC, id DESC"
            with self._growth_store._ensure_conn().cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        else:
            sql = (
                "SELECT id,user_id,name,categories,skills,min_salary_filter,hide_senior,enabled "
                "FROM saved_searches WHERE user_id=?"
            )
            params = [int(user_id)]
            if enabled_only:
                sql += " AND enabled=1"
            sql += " ORDER BY updated_at DESC, id DESC"
            rows = self.fetchall(sql, tuple(params))
        return [saved_search_from_row(row) for row in rows]

    def delete_saved_search(self, user_id: int, search_id: int) -> bool:
        if self._growth_store is not None:
            with self._growth_store._ensure_conn().cursor() as cur:
                cur.execute(
                    "DELETE FROM growth_saved_searches WHERE id=%s AND user_id=%s RETURNING id",
                    (int(search_id), int(user_id)),
                )
                return bool(cur.fetchone())
        cur = self.execute(
            "DELETE FROM saved_searches WHERE id=? AND user_id=?",
            (int(search_id), int(user_id)),
        )
        return bool(cur.rowcount)


class JobBot(growth.JobBot):
    """Resume Match plus durable saved-search subscriptions."""

    def __init__(self, application, db):
        super().__init__(application, db)
        self.resume_sessions: Dict[int, Dict] = {}
        self.pending_saved_searches: Dict[int, Dict] = {}

    async def cmd_profile(self, update, context):
        await super().cmd_profile(update, context)
        user_id = update.effective_user.id
        searches = self.db.list_saved_searches(user_id)
        lines = ["🔔 Сохранённые поиски:"]
        if searches:
            for search in searches[:8]:
                details = search.skills or ",".join(search.categories) or "все направления"
                lines.append(f"• {search.name} — {details}")
        else:
            lines.append("Пока нет. Можно сохранить текущий профиль или поиск после Resume Match.")
        buttons = [[core.InlineKeyboardButton("💾 Сохранить текущий профиль", callback_data="saved_search_from_profile")]]
        for search in searches[:6]:
            buttons.append([
                core.InlineKeyboardButton(
                    f"🗑 {search.name[:28]}", callback_data=f"saved_search_delete:{search.id}"
                )
            ])
        await update.message.reply_text("\n".join(lines), reply_markup=core.InlineKeyboardMarkup(buttons))

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

        if data == "saved_search_from_resume":
            spec = self.pending_saved_searches.get(user_id)
            if not spec:
                await query.answer("Сначала сделай свежий Resume Match", show_alert=True)
                return
            search = self.db.create_saved_search(user_id, spec)
            self.db.save_user_settings(user_id, {"alerts_enabled": True})
            self.db.log_event(
                user_id,
                "saved_search_created",
                {"search_id": search.id, "source": "resume_match"},
            )
            await query.answer("Поиск сохранён, алерты включены", show_alert=True)
            return

        if data == "saved_search_from_profile":
            settings = self.db.get_user_settings(user_id)
            spec = {
                "categories": settings.get("enabled_categories") or [],
                "skills": settings.get("skills") or "",
                "min_salary_filter": settings.get("min_salary_filter") or 0,
                "hide_senior": settings.get("hide_senior", True),
            }
            spec["name"] = build_search_name(spec["categories"], spec["skills"], "Мой профиль")
            search = self.db.create_saved_search(user_id, spec)
            self.db.save_user_settings(user_id, {"alerts_enabled": True})
            self.db.log_event(
                user_id,
                "saved_search_created",
                {"search_id": search.id, "source": "profile"},
            )
            await query.answer("Профиль сохранён как поиск, алерты включены", show_alert=True)
            return

        if data.startswith("saved_search_delete:"):
            try:
                search_id = int(data.split(":", 1)[1])
            except ValueError:
                await query.answer("Некорректный поиск", show_alert=True)
                return
            deleted = self.db.delete_saved_search(user_id, search_id)
            if deleted:
                self.db.log_event(user_id, "saved_search_deleted", {"search_id": search_id})
            await query.answer("Поиск удалён" if deleted else "Поиск уже удалён", show_alert=True)
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
            self.pending_saved_searches[user_id] = suggested_search_from_resume(job, report.matched_skills)
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
                    [core.InlineKeyboardButton("🔔 Сохранить такой поиск", callback_data="saved_search_from_resume")],
                    [core.InlineKeyboardButton("🎯 Уточнить профиль", callback_data="growth_quick_setup")],
                ]),
                disable_web_page_preview=True,
            )
            return

        return await super().handle_setup_text(update, context)

    async def push_realtime_alerts(self, new_jobs: List[Dict]) -> int:
        """Use saved searches when present; otherwise preserve profile alerts."""
        if not core.Config.ENABLE_REALTIME_ALERTS or not new_jobs:
            return 0
        total = 0
        for uid in self.db.list_alert_subscribers():
            searches = self.db.list_saved_searches(uid)
            settings = self._effective_profile(uid)
            matched = []
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
            try:
                label = "по сохранённым поискам" if searches else "по профилю"
                await self.application.bot.send_message(
                    chat_id=uid,
                    text=f"⚡ {len(matched)} новых вакансий {label}:",
                )
            except Exception as exc:
                logger.debug("alert header fail %s: %s", uid, exc)
                continue
            sent = 0
            for job in matched:
                try:
                    if core.Config.ENABLE_MARKDOWN_V2 and self.formatter:
                        formatted = self.formatter.format_job(
                            job, view_mode="compact", bot_username=core.Config.BOT_USERNAME
                        )
                        await self.application.bot.send_message(
                            chat_id=uid,
                            text=formatted.text,
                            parse_mode=core.ParseMode.MARKDOWN_V2,
                            reply_markup=core.InlineKeyboardMarkup(formatted.reply_markup["inline_keyboard"]),
                            disable_web_page_preview=True,
                        )
                    else:
                        await self.application.bot.send_message(
                            chat_id=uid,
                            text=core.format_job_message_legacy(job),
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )
                    sent += 1
                    total += 1
                    await core.asyncio.sleep(0.35)
                except Exception as exc:
                    logger.debug("alert job fail %s: %s", uid, exc)
            self.db.log_event(
                uid,
                "realtime_alert_sent",
                {"count": sent, "saved_search": bool(searches)},
            )
        return total


core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot

Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = core.main
