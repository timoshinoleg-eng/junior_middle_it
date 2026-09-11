"""Final P7/P8 product-state layer shared by polling and webhook runtimes.

P7 removes user-visible dependence on ephemeral SQLite. P8 builds the retention
loop on top of that durable state: fast first value, Resume Match, saved
searches, alerts/digest controls and measurable product actions.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import channel_bot as core
import public_acquisition_runtime as p7
from serverless_publication_policy import parse_source_datetime


_MAX_REASONABLE_FUTURE_SKEW = timedelta(hours=6)


def _max_job_age_days() -> int:
    raw = str(os.getenv("SERVERLESS_MAX_JOB_AGE_DAYS", "7") or "7").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 7
    return max(1, min(value, 30))


class DatabaseConnection(p7.DatabaseConnection):
    """Durable favorites, fresh vacancy reads and P8 product metrics."""

    @staticmethod
    def _payload_dict(raw, job_hash: str = "") -> Dict:
        if isinstance(raw, dict):
            payload = dict(raw)
        else:
            try:
                payload = json.loads(raw or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                return {}
        if not isinstance(payload, dict):
            return {}
        if job_hash:
            payload.setdefault("hash", str(job_hash))
        return payload

    @staticmethod
    def _is_source_fresh(payload: Dict, *, now=None) -> bool:
        """Use the original source timestamp, never ledger write time, for freshness."""
        source_date = parse_source_datetime(payload)
        if source_date is None:
            return False
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        else:
            current = current.astimezone(timezone.utc)
        if source_date - current > _MAX_REASONABLE_FUTURE_SKEW:
            return False
        return current - source_date <= timedelta(days=_max_job_age_days())

    def add_favorite(self, user_id: int, job_hash: str) -> bool:
        if self._growth_store is None:
            return super().add_favorite(user_id, job_hash)
        with self._growth_store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_favorites (user_id, job_hash, saved_at)
                SELECT %s, hash, NOW()
                FROM growth_job_payloads
                WHERE hash = %s AND publication_state = 'published'
                ON CONFLICT (user_id, job_hash) DO UPDATE SET saved_at=EXCLUDED.saved_at
                RETURNING job_hash
                """,
                (int(user_id), str(job_hash)),
            )
            return bool(cur.fetchone())

    def remove_favorite(self, user_id: int, job_hash: str) -> bool:
        if self._growth_store is None:
            return super().remove_favorite(user_id, job_hash)
        with self._growth_store._ensure_conn().cursor() as cur:
            cur.execute(
                "DELETE FROM growth_favorites WHERE user_id=%s AND job_hash=%s RETURNING job_hash",
                (int(user_id), str(job_hash)),
            )
            return bool(cur.fetchone())

    def get_user_favorites(self, user_id: int) -> List[Dict]:
        if self._growth_store is None:
            return super().get_user_favorites(user_id)
        with self._growth_store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                SELECT f.job_hash, j.payload
                FROM growth_favorites f
                JOIN growth_job_payloads j ON j.hash = f.job_hash
                WHERE f.user_id = %s AND j.publication_state = 'published'
                ORDER BY f.saved_at DESC
                LIMIT 50
                """,
                (int(user_id),),
            )
            rows = cur.fetchall()
        out: List[Dict] = []
        for job_hash, raw in rows:
            payload = self._payload_dict(raw, str(job_hash))
            if payload:
                out.append(payload)
        return out

    def recent_jobs_for_digest(self, hours: int = 36, limit: int = 80) -> List[Dict]:
        """Read only published, source-fresh durable payloads for personal digests."""
        if self._growth_store is None:
            return super().recent_jobs_for_digest(hours=hours, limit=limit)
        hours = max(1, min(int(hours), 24 * 45))
        limit = max(1, min(int(limit), 500))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        query_limit = min(2000, max(limit, limit * 3))
        with self._growth_store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                SELECT hash, payload
                FROM growth_job_payloads
                WHERE publication_state = 'published'
                  AND COALESCE(published_at, updated_at) >= %s
                ORDER BY COALESCE(published_at, updated_at) DESC
                LIMIT %s
                """,
                (cutoff, query_limit),
            )
            rows = cur.fetchall()
        out: List[Dict] = []
        for job_hash, raw in rows:
            payload = self._payload_dict(raw, str(job_hash))
            if not payload or not self._is_source_fresh(payload):
                continue
            out.append(payload)
            if len(out) >= limit:
                break
        return out

    def jobs_with_salary_for_report(self, days: int = 14, limit: int = 500) -> List[Dict]:
        """Use published, source-fresh durable payloads for the weekly salary content magnet."""
        if self._growth_store is None:
            return super().jobs_with_salary_for_report(days=days, limit=limit)
        days = max(1, min(int(days), 45))
        limit = max(1, min(int(limit), 1000))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self._growth_store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                SELECT hash, payload
                FROM growth_job_payloads
                WHERE publication_state = 'published'
                  AND COALESCE(published_at, updated_at) >= %s
                ORDER BY COALESCE(published_at, updated_at) DESC
                LIMIT %s
                """,
                (cutoff, min(2000, max(limit, limit * 3))),
            )
            rows = cur.fetchall()
        jobs: List[Dict] = []
        for job_hash, raw in rows:
            payload = self._payload_dict(raw, str(job_hash))
            if not payload or not self._is_source_fresh(payload):
                continue
            salary = payload.get("salary_min_usd")
            if isinstance(salary, bool) or not isinstance(salary, (int, float)) or salary <= 0:
                continue
            jobs.append(payload)
            if len(jobs) >= limit:
                break
        return jobs

    def p8_funnel_stats(self, days: int = 7) -> Dict:
        """Compose the retention funnel from already production-safe metric sources."""
        growth = self.growth_stats(days)
        utility = self.utility_stats(days)
        starts = int(growth.get("starts") or 0)
        activated = int(growth.get("activated_users") or 0)
        setup = int(growth.get("setup_done") or 0)
        resume = int(utility.get("resume_completed_users") or 0)
        searches = int(utility.get("saved_search_users") or 0)

        def pct(value: int, base: int) -> float:
            return round(100.0 * value / base, 1) if base else 0.0

        return {
            "days": max(1, min(int(days), 90)),
            "starts": starts,
            "activated": activated,
            "activated_pct": pct(activated, starts),
            "setup": setup,
            "setup_pct": pct(setup, starts),
            "savers": int(growth.get("saves") or 0),
            "resume_completed": resume,
            "resume_completion_pct": float(utility.get("resume_completion_pct") or 0.0),
            "saved_search_users": searches,
            "resume_to_search_pct": float(utility.get("resume_to_search_pct") or 0.0),
            "active_saved_searches": int(utility.get("active_saved_searches") or 0),
            "alert_subscribers": int(growth.get("alert_subscribers") or 0),
            "digest_subscribers": int(growth.get("digest_subscribers") or 0),
            "d1_retention_pct": float(growth.get("d1_retention_pct") or 0.0),
            "d7_retention_pct": float(growth.get("d7_retention_pct") or 0.0),
        }


class JobBot(p7.JobBot):
    """P8 mobile-first retention loop on top of the durable P7 product."""

    @staticmethod
    def _resume_prompt(job: Dict) -> str:
        title = str(job.get("title") or "вакансия")
        company = str(job.get("company") or "").strip()
        target = f"{title} @ {company}" if company else title
        return (
            f"📄 Resume Match · {target}\n\n"
            "1) Пришли резюме: PDF, DOCX или текстом.\n"
            "2) Получишь совпавшие навыки, пробелы и конкретные правки перед откликом.\n"
            "3) После результата можно одним нажатием сохранить похожий поиск и включить алерты.\n\n"
            "Резюме не сохраняется в БД; файл обрабатывается в памяти. Максимум 5 МБ.\n"
            "Оценка эвристическая — это помощник, а не официальный ATS-score."
        )

    def _retention_keyboard(self, user_id: int):
        settings = self.db.get_user_settings(user_id)
        searches = self.db.list_saved_searches(user_id)
        alerts_on = bool(settings.get("alerts_enabled"))
        digest_on = bool(settings.get("digest_enabled"))
        rows = [
            [core.InlineKeyboardButton("⚡ 3 свежих match", callback_data="growth_quick_fresh")],
            [
                core.InlineKeyboardButton(
                    f"🔔 Алерты: {'ВКЛ' if alerts_on else 'ВЫКЛ'}",
                    callback_data="p8_alerts_toggle",
                ),
                core.InlineKeyboardButton(
                    f"📬 Digest: {'ВКЛ' if digest_on else 'ВЫКЛ'}",
                    callback_data="p8_digest_toggle",
                ),
            ],
            [
                core.InlineKeyboardButton(
                    f"💾 Поиски ({len(searches)})",
                    callback_data="p8_saved_searches",
                ),
                core.InlineKeyboardButton("⚙️ Профиль", callback_data="growth_quick_setup"),
            ],
        ]
        channel = str(core.Config.CHANNEL_ID or "")
        if channel.startswith("@"):
            rows.append([
                core.InlineKeyboardButton(
                    "📢 Канал",
                    url=f"https://t.me/{channel.lstrip('@')}",
                )
            ])
        return core.InlineKeyboardMarkup(rows)

    async def _send_retention_hub(self, message, user_id: int, *, title: str = "🎯 Твой поиск работы"):
        settings = self.db.get_user_settings(user_id)
        searches = self.db.list_saved_searches(user_id)
        onboarding = bool(settings.get("onboarding_done"))
        text = (
            f"{title}\n\n"
            f"Профиль: {'готов' if onboarding else 'нужно настроить'} · "
            f"алерты: {'вкл' if settings.get('alerts_enabled') else 'выкл'} · "
            f"digest: {'вкл' if settings.get('digest_enabled') else 'выкл'} · "
            f"поисков: {len(searches)}.\n"
            "Быстрый цикл: найди match → Resume Match → сохрани поиск → получай новые вакансии."
        )
        await message.reply_text(
            text,
            reply_markup=self._retention_keyboard(user_id),
            disable_web_page_preview=True,
        )
        self.db.log_event(user_id, "p8_hub_view", {"searches": len(searches)})

    async def _send_saved_searches(self, message, user_id: int):
        searches = self.db.list_saved_searches(user_id)
        lines = ["💾 Сохранённые поиски"]
        rows = []
        if searches:
            for search in searches[:8]:
                details = search.skills or ", ".join(search.categories) or "все направления"
                lines.append(f"• {search.name} — {details}")
                rows.append([
                    core.InlineKeyboardButton(
                        f"🗑 {search.name[:30]}",
                        callback_data=f"p8_saved_search_delete:{search.id}",
                    )
                ])
            lines.append("\nАлерты проверяют эти поиски; если поисков нет — используется основной профиль.")
        else:
            lines.append("Пока нет. Сохрани текущий профиль или создай поиск после Resume Match.")
        rows.append([
            core.InlineKeyboardButton(
                "💾 Сохранить текущий профиль",
                callback_data="saved_search_from_profile",
            )
        ])
        rows.append([
            core.InlineKeyboardButton("← Назад", callback_data="p8_retention_hub")
        ])
        await message.reply_text(
            "\n".join(lines),
            reply_markup=core.InlineKeyboardMarkup(rows),
        )
        self.db.log_event(user_id, "p8_saved_searches_view", {"count": len(searches)})

    async def cmd_start(self, update, context):
        payload = context.args[0] if context.args else None
        user = update.effective_user
        user_id = user.id if user else None
        if payload or not user_id:
            return await super().cmd_start(update, context)

        self.db.log_event(user_id, "start", {"payload": None})
        self.db.maybe_unlock_premium(user_id)
        settings = self.db.get_user_settings(user_id)
        if settings.get("onboarding_done"):
            title = "🎯 Профиль готов. Что сделать сейчас?"
        else:
            title = "🚀 Начни с 3 свежих вакансий или настрой подборку за ~30 секунд"
        await self._send_retention_hub(update.message, user_id, title=title)

    async def cmd_alerts(self, update, context):
        if context.args:
            return await super().cmd_alerts(update, context)
        await self._send_retention_hub(update.message, update.effective_user.id, title="🔔 Управление алертами")

    async def cmd_digest(self, update, context):
        if context.args:
            return await super().cmd_digest(update, context)
        await self._send_retention_hub(update.message, update.effective_user.id, title="📬 Управление digest")

    async def cmd_stats_growth(self, update, context):
        user_id = update.effective_user.id if update.effective_user else None
        is_admin = bool(core.Config.ADMIN_USER_ID and user_id == core.Config.ADMIN_USER_ID)
        await super().cmd_stats_growth(update, context)
        if not is_admin:
            return
        days = core.Config.GROWTH_STATS_DAYS
        if context.args:
            try:
                days = max(1, min(int(context.args[0]), 90))
            except ValueError:
                pass
        stats = self.db.p8_funnel_stats(days)
        await update.message.reply_text(
            f"🧭 P8 product funnel · {days}d\n"
            f"Start: {stats['starts']}\n"
            f"Activated D0: {stats['activated']} ({stats['activated_pct']}%)\n"
            f"Setup: {stats['setup']} ({stats['setup_pct']}%)\n"
            f"Saved vacancy users: {stats['savers']}\n"
            f"Resume Match complete: {stats['resume_completed']} "
            f"({stats['resume_completion_pct']}% of starts)\n"
            f"Saved-search creators: {stats['saved_search_users']} · active searches: {stats['active_saved_searches']}\n"
            f"Resume → search: {stats['resume_to_search_pct']}%\n"
            f"Subscribers: alerts {stats['alert_subscribers']} · digest {stats['digest_subscribers']}\n"
            f"Retention: D1 {stats['d1_retention_pct']}% · D7 {stats['d7_retention_pct']}%"
        )

    async def handle_callback(self, update, context):
        query = update.callback_query
        data = (query.data or "") if query else ""
        user_id = update.effective_user.id if update.effective_user else None

        if not query or not user_id:
            return await super().handle_callback(update, context)

        if data == "p8_retention_hub":
            await query.answer()
            await self._send_retention_hub(query.message, user_id)
            return

        if data == "p8_alerts_toggle":
            settings = self.db.get_user_settings(user_id)
            enabled = not bool(settings.get("alerts_enabled"))
            self.db.save_user_settings(user_id, {"alerts_enabled": enabled})
            self.db.log_event(
                user_id,
                "alerts_on" if enabled else "alerts_off",
                {"source": "p8_hub"},
            )
            await query.answer("Алерты включены" if enabled else "Алерты выключены")
            await self._send_retention_hub(query.message, user_id, title="🔔 Настройки обновлены")
            return

        if data == "p8_digest_toggle":
            settings = self.db.get_user_settings(user_id)
            enabled = not bool(settings.get("digest_enabled"))
            self.db.save_user_settings(user_id, {"digest_enabled": enabled})
            self.db.log_event(
                user_id,
                "digest_on" if enabled else "digest_off",
                {"source": "p8_hub"},
            )
            await query.answer("Digest включён" if enabled else "Digest выключен")
            await self._send_retention_hub(query.message, user_id, title="📬 Настройки обновлены")
            return

        if data == "p8_saved_searches":
            await query.answer()
            await self._send_saved_searches(query.message, user_id)
            return

        if data.startswith("p8_saved_search_delete:"):
            try:
                search_id = int(data.split(":", 1)[1])
            except ValueError:
                await query.answer("Некорректный поиск", show_alert=True)
                return
            deleted = self.db.delete_saved_search(user_id, search_id)
            if deleted:
                self.db.log_event(user_id, "saved_search_deleted", {"search_id": search_id, "source": "p8_hub"})
            await query.answer("Поиск удалён" if deleted else "Поиск уже удалён")
            await self._send_saved_searches(query.message, user_id)
            return

        if data == "growth_quick_fresh":
            await query.answer("Ищу 3 свежих совпадения")
            n = await self._send_first_value_preview(user_id, source="p8_quick_fresh")
            self.db.log_event(user_id, "fresh_preview", {"count": n, "source": "p8_hub"})
            if not n:
                await query.message.reply_text(
                    "Пока нет свежих совпадений. Уточни профиль — это повысит точность следующего поиска.",
                    reply_markup=core.InlineKeyboardMarkup([[
                        core.InlineKeyboardButton("⚙️ Настроить профиль", callback_data="growth_quick_setup")
                    ]]),
                )
            else:
                await self._send_retention_hub(
                    query.message,
                    user_id,
                    title="✅ Preview готов. Выбери следующий шаг",
                )
            return

        if data in {"setup_digest_on", "setup_digest_off"}:
            result = await super().handle_callback(update, context)
            await self._send_retention_hub(query.message, user_id, title="✅ Профиль готов")
            return result

        if data == "saved_search_from_resume":
            had_pending = user_id in self.pending_saved_searches
            result = await super().handle_callback(update, context)
            if had_pending:
                await self._send_retention_hub(
                    query.message,
                    user_id,
                    title="✅ Поиск сохранён, алерты включены",
                )
            return result

        if data == "saved_search_from_profile":
            result = await super().handle_callback(update, context)
            await self._send_retention_hub(
                query.message,
                user_id,
                title="✅ Профиль сохранён как поиск",
            )
            return result

        if data.startswith("save:"):
            job_hash = data.split(":", 1)[1]
            saved = self.db.add_favorite(user_id, job_hash)
            if not saved:
                await query.answer("Вакансия уже устарела", show_alert=True)
                return
            self.db.log_event(user_id, "save_job", {"hash": job_hash, "source": "p8_card"})
            await query.answer("Сохранено")
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="💾 Сохранено. Следующий полезный шаг — проверить резюме или включить алерты по похожим вакансиям.",
                    reply_markup=core.InlineKeyboardMarkup([
                        [core.InlineKeyboardButton("📄 Resume Match", callback_data=f"resume_match:{job_hash}")],
                        [core.InlineKeyboardButton("🔔 Управлять алертами", callback_data="p8_retention_hub")],
                    ]),
                )
            except Exception:
                pass
            return

        return await super().handle_callback(update, context)


# Install the final storage/runtime extension points. Ancestor main() functions
# resolve these globals at execution time, so collector code is not duplicated.
core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot

Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = p7.main
