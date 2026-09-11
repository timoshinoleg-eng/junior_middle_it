"""P7 production runtime: public acquisition plus restart-safe interaction state.

The public landing is read-only; attribution happens only after a user explicitly
opens the Telegram bot. This final runtime layer turns ``web_*`` and
``resume_*`` start payloads into useful in-bot continuation and persists only
non-sensitive workflow metadata needed to survive polling-worker restarts.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import channel_bot as core
import referral_runtime as referral
from durable_runtime_state import RuntimeStateMap
from p7_growth_metrics import compute_growth_stats


SETUP_STATE_TTL_SECONDS = 24 * 60 * 60
RESUME_STATE_TTL_SECONDS = 2 * 60 * 60
PENDING_SEARCH_TTL_SECONDS = 24 * 60 * 60
FIRST_VALUE_PREVIEW_MAX = 3


class DatabaseConnection(referral.DatabaseConnection):
    """P7 storage plus cohort metrics and short-lived durable workflow state."""

    def __init__(self, db_path: str = "jobs.db"):
        super().__init__(db_path)
        # Local/dev parity. Production uses public.growth_runtime_state through
        # the durable Postgres/Edge growth backend.
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_state (
                user_id INTEGER NOT NULL,
                state_key TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT '{}',
                expires_at TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, state_key)
            )
            """
        )
        self.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_state_expires "
            "ON runtime_state(expires_at)"
        )

    def growth_stats(self, days: int = 7):
        if self._growth_store is not None:
            return compute_growth_stats(self._growth_store, days)
        return super().growth_stats(days)

    @staticmethod
    def _runtime_expiry(ttl_seconds: int) -> datetime:
        ttl = max(60, min(int(ttl_seconds), 7 * 24 * 60 * 60))
        return datetime.now(timezone.utc) + timedelta(seconds=ttl)

    @staticmethod
    def _parse_runtime_expiry(value):
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def set_runtime_state(
        self,
        user_id: int,
        state_key: str,
        state: dict,
        *,
        ttl_seconds: int,
    ) -> None:
        key = str(state_key or "").strip()[:80]
        if not key or not isinstance(state, dict):
            raise ValueError("invalid runtime state")
        expiry = self._runtime_expiry(ttl_seconds)
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > 16_000:
            raise ValueError("runtime state is too large")

        if self._growth_store is not None:
            with self._growth_store._ensure_conn().cursor() as cur:
                cur.execute(
                    "DELETE FROM growth_runtime_state "
                    "WHERE expires_at IS NOT NULL AND expires_at <= NOW()"
                )
                cur.execute(
                    """
                    INSERT INTO growth_runtime_state
                        (user_id, state_key, state, expires_at, updated_at)
                    VALUES (%s, %s, %s::jsonb, %s, NOW())
                    ON CONFLICT (user_id, state_key) DO UPDATE SET
                        state=EXCLUDED.state,
                        expires_at=EXCLUDED.expires_at,
                        updated_at=NOW()
                    """,
                    (int(user_id), key, payload, expiry),
                )
            return

        now = datetime.now(timezone.utc).isoformat()
        self.execute(
            "DELETE FROM runtime_state WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        self.execute(
            """
            INSERT INTO runtime_state (user_id, state_key, state, expires_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, state_key) DO UPDATE SET
                state=excluded.state,
                expires_at=excluded.expires_at,
                updated_at=excluded.updated_at
            """,
            (int(user_id), key, payload, expiry.isoformat(), now),
        )

    def get_runtime_state(self, user_id: int, state_key: str):
        key = str(state_key or "").strip()[:80]
        if not key:
            return None
        if self._growth_store is not None:
            with self._growth_store._ensure_conn().cursor() as cur:
                cur.execute(
                    "SELECT state, expires_at FROM growth_runtime_state "
                    "WHERE user_id=%s AND state_key=%s",
                    (int(user_id), key),
                )
                row = cur.fetchone()
        else:
            row = self.fetchone(
                "SELECT state, expires_at FROM runtime_state "
                "WHERE user_id=? AND state_key=?",
                (int(user_id), key),
            )
        if not row:
            return None

        expiry = self._parse_runtime_expiry(row[1])
        if expiry is None or expiry <= datetime.now(timezone.utc):
            self.delete_runtime_state(user_id, key)
            return None
        raw_state = row[0]
        if isinstance(raw_state, dict):
            return raw_state
        try:
            parsed = json.loads(raw_state or "{}")
            return parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError, json.JSONDecodeError):
            self.delete_runtime_state(user_id, key)
            return None

    def delete_runtime_state(self, user_id: int, state_key: str) -> None:
        key = str(state_key or "").strip()[:80]
        if not key:
            return
        if self._growth_store is not None:
            with self._growth_store._ensure_conn().cursor() as cur:
                cur.execute(
                    "DELETE FROM growth_runtime_state WHERE user_id=%s AND state_key=%s",
                    (int(user_id), key),
                )
            return
        self.execute(
            "DELETE FROM runtime_state WHERE user_id=? AND state_key=?",
            (int(user_id), key),
        )


class JobBot(referral.JobBot):
    """Continue landing intent and preserve safe workflow metadata on restart."""

    def __init__(self, application, db):
        super().__init__(application, db)

        def encode_setup(step):
            value = str(step or "")[:32]
            return {"step": value}

        def decode_setup(state):
            step = str(state.get("step") or "")
            return step if step in {"categories", "salary", "skills", "digest"} else None

        def encode_resume(session):
            # Never serialize the vacancy payload or any resume content here.
            return {"job_hash": str((session or {}).get("job_hash") or "")[:100]}

        def decode_resume(state):
            job_hash = str(state.get("job_hash") or "")[:100]
            if not job_hash:
                return None
            job = db.get_job_payload(job_hash)
            if not job:
                return None
            return {"job_hash": job_hash, "job": job}

        def encode_pending_search(spec):
            normalized = db._normalize_search_spec(spec or {})
            return {
                "name": normalized["name"],
                "categories": normalized["categories"],
                "skills": normalized["skills"],
                "min_salary_filter": normalized["min_salary_filter"],
                "hide_senior": normalized["hide_senior"],
            }

        def decode_pending_search(state):
            if not isinstance(state, dict):
                return None
            return {
                "name": str(state.get("name") or "")[:80],
                "categories": state.get("categories") or [],
                "skills": str(state.get("skills") or "")[:500],
                "min_salary_filter": int(state.get("min_salary_filter") or 0),
                "hide_senior": bool(state.get("hide_senior", True)),
            }

        # Ancestor code intentionally continues to use ordinary dict operations.
        # These facades make those same reads/writes durable without modifying the
        # mature core setup/Resume Match implementations.
        self.setup_steps = RuntimeStateMap(
            db,
            "setup_step",
            ttl_seconds=SETUP_STATE_TTL_SECONDS,
            encode=encode_setup,
            decode=decode_setup,
        )
        self.resume_sessions = RuntimeStateMap(
            db,
            "resume_session",
            ttl_seconds=RESUME_STATE_TTL_SECONDS,
            encode=encode_resume,
            decode=decode_resume,
        )
        self.pending_saved_searches = RuntimeStateMap(
            db,
            "pending_saved_search",
            ttl_seconds=PENDING_SEARCH_TTL_SECONDS,
            encode=encode_pending_search,
            decode=decode_pending_search,
        )

    @staticmethod
    def _safe_apply_url(value: str) -> str:
        raw = str(value or "").strip()
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return ""
        return raw if parsed.scheme in {"http", "https"} and parsed.netloc else ""

    def _select_first_value_jobs(self, user_id: int):
        """Return at most three profile matches for a low-friction first preview."""
        settings = self._effective_profile(user_id)
        jobs = self.db.recent_jobs_for_digest(
            hours=core.Config.PERSONAL_DIGEST_LOOKBACK_HOURS,
            limit=80,
        )
        matched = []
        for job in jobs:
            if core.GROWTH_UTILS_AVAILABLE and not core.job_matches_profile(job, settings):
                continue
            matched.append(job)
            if len(matched) >= FIRST_VALUE_PREVIEW_MAX:
                break
        return settings, matched

    async def _send_first_value_preview(self, user_id: int, *, source: str) -> int:
        settings, matched = self._select_first_value_jobs(user_id)
        if not matched:
            return 0

        header = (
            f"⚡ Первые {len(matched)} совпадения — короткий preview.\n"
            "В карточке можно сохранить вакансию или запустить Resume Match."
        )
        try:
            await self.application.bot.send_message(chat_id=user_id, text=header)
        except Exception:
            return 0

        sent = 0
        for job in matched:
            try:
                if core.Config.ENABLE_MARKDOWN_V2 and self.formatter:
                    formatted = self.formatter.format_job(
                        job,
                        view_mode="compact",
                        bot_username=core.Config.BOT_USERNAME,
                    )
                    await self.application.bot.send_message(
                        chat_id=user_id,
                        text=formatted.text,
                        parse_mode=core.ParseMode.MARKDOWN_V2,
                        reply_markup=core.InlineKeyboardMarkup(
                            formatted.reply_markup["inline_keyboard"]
                        ),
                        disable_web_page_preview=True,
                    )
                else:
                    await self.application.bot.send_message(
                        chat_id=user_id,
                        text=core.format_job_message_legacy(job),
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                sent += 1
            except Exception:
                continue
        self.db.log_event(user_id, "first_value_preview_sent", {"count": sent, "source": source})
        return sent

    async def cmd_start(self, update, context):
        user = update.effective_user
        user_id = user.id if user else None
        payload = context.args[0] if context.args else ""
        payload = str(payload or "")[:64]

        # P5 already owns the complete resume_* deep-link lifecycle: canonical
        # start analytics, resume attribution, session creation, stale-card UX,
        # PDF/DOCX-aware prompt, and resume_match_started. P7 must only add the
        # public-site attribution and delegate once.
        if user_id and payload.startswith("resume_"):
            job_hash = payload[7:]
            if job_hash:
                self.db.log_event(
                    user_id,
                    "web_resume_start",
                    {"surface": "public_site", "hash": job_hash},
                )
            return await super().cmd_start(update, context)

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

    async def handle_callback(self, update, context):
        query = update.callback_query
        data = (query.data or "") if query else ""
        user_id = update.effective_user.id if update.effective_user else None

        if data == "growth_quick_fresh" and user_id:
            await query.answer("Ищу 3 свежих совпадения")
            n = await self._send_first_value_preview(user_id, source="quick_fresh")
            self.db.log_event(user_id, "fresh_preview", {"count": n})
            if not n:
                await query.message.reply_text(
                    "Пока нет свежих совпадений. Настрой профиль — так поиск станет точнее.",
                    reply_markup=core.InlineKeyboardMarkup([[
                        core.InlineKeyboardButton(
                            "🎯 Настроить профиль",
                            callback_data="growth_quick_setup",
                        )
                    ]]),
                )
            else:
                settings = self.db.get_user_settings(user_id)
                if not settings.get("onboarding_done"):
                    await query.message.reply_text(
                        "Хочешь точнее? Настрой стек и зарплату — займёт около 30 секунд.",
                        reply_markup=core.InlineKeyboardMarkup([[
                            core.InlineKeyboardButton(
                                "🎯 Настроить подборку",
                                callback_data="growth_quick_setup",
                            )
                        ]]),
                    )
            return

        if data in {"setup_digest_on", "setup_digest_off"} and user_id:
            enabled = data == "setup_digest_on"
            await query.answer("Готово")
            self.db.save_user_settings(
                user_id,
                {"digest_enabled": enabled, "onboarding_done": True},
            )
            self.setup_steps.pop(user_id, None)
            self.db.log_event(user_id, "setup_done", {"digest": enabled})
            await query.edit_message_text(
                "✅ Профиль готов. Уже ищу 3 первых подходящих вакансии…"
            )
            n = await self._send_first_value_preview(user_id, source="onboarding")
            if n:
                self.db.log_event(user_id, "first_value_delivered", {"count": n})
                await query.message.reply_text(
                    "Готово. Полную подборку можно запросить командой /digest now; "
                    "понравившуюся вакансию — сохранить или проверить через Resume Match."
                )
            else:
                await query.message.reply_text(
                    "Под свежий профиль пока нет совпадений. Я продолжу искать; "
                    "фильтры можно изменить через /setup."
                )
            return

        if data == "saved_search_from_resume" and user_id:
            had_pending = user_id in self.pending_saved_searches
            result = await super().handle_callback(update, context)
            if had_pending:
                # Parent only returns here after a successful create path. A stale
                # button should not keep reusing yesterday's Resume Match result.
                self.pending_saved_searches.pop(user_id, None)
            return result
        return await super().handle_callback(update, context)


core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot

Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = referral.main
