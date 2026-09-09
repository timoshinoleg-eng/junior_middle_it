"""Production growth layer for the existing channel_bot core.

This module intentionally patches a small set of extension points instead of
rewriting the mature 180kB ingestion pipeline.  Deployment entrypoints import
this module; it then installs:

* durable PostgreSQL profiles/referrals/events when a DB URL is configured;
* fail-closed admin authorization;
* honest referral rewards (larger digests, not unavailable Senior jobs);
* action-first /start UX;
* immediate first matched vacancies after onboarding.
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Optional

import channel_bot as core
from growth_store import GrowthStoreUnavailable, PostgresGrowthStore

logger = logging.getLogger(__name__)


def _growth_dsn() -> str:
    return (os.getenv("GROWTH_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()


def durable_growth_configured() -> bool:
    return bool(_growth_dsn())


class DatabaseConnection(core.DatabaseConnection):
    """Keep job cache in SQLite; move critical user/growth state to Postgres."""

    def __init__(self, db_path: str = "jobs.db"):
        super().__init__(db_path)
        self._growth_store: Optional[PostgresGrowthStore] = None
        dsn = _growth_dsn()
        require_durable = os.getenv("REQUIRE_DURABLE_GROWTH", "false").lower() == "true"
        if dsn:
            try:
                self._growth_store = PostgresGrowthStore(
                    dsn,
                    all_categories=list(core.CATEGORY_NAMES_RU.keys()),
                )
                logger.info("✅ Durable growth backend: PostgreSQL")
            except GrowthStoreUnavailable as exc:
                if require_durable:
                    super().close()
                    raise
                logger.error("Durable growth backend unavailable, SQLite fallback: %s", exc)
        elif require_durable:
            super().close()
            raise GrowthStoreUnavailable(
                "REQUIRE_DURABLE_GROWTH=true but GROWTH_DATABASE_URL/DATABASE_URL is missing"
            )
        else:
            logger.warning(
                "⚠️ Growth state uses local SQLite fallback. Configure GROWTH_DATABASE_URL "
                "and REQUIRE_DURABLE_GROWTH=true for production durability."
            )

    @property
    def growth_backend(self) -> str:
        return "postgres" if self._growth_store is not None else "sqlite"

    def close(self):
        if self._growth_store is not None:
            self._growth_store.close()
        super().close()

    def get_user_settings(self, user_id: int) -> Dict:
        if self._growth_store is not None:
            return self._growth_store.get_user_settings(user_id)
        return super().get_user_settings(user_id)

    def save_user_settings(self, user_id: int, settings: Dict) -> bool:
        if self._growth_store is not None:
            return self._growth_store.save_user_settings(user_id, settings)
        return super().save_user_settings(user_id, settings)

    def list_digest_subscribers(self):
        if self._growth_store is not None:
            return self._growth_store.list_digest_subscribers()
        return super().list_digest_subscribers()

    def list_alert_subscribers(self):
        if self._growth_store is not None:
            return self._growth_store.list_alert_subscribers()
        return super().list_alert_subscribers()

    def log_event(self, user_id, name: str, props: Optional[Dict] = None) -> None:
        if self._growth_store is not None:
            try:
                self._growth_store.log_event(user_id, name, props)
                return
            except Exception as exc:
                logger.error("PostgreSQL event write failed: %s", exc)
                if os.getenv("REQUIRE_DURABLE_GROWTH", "false").lower() == "true":
                    raise
        super().log_event(user_id, name, props)

    def register_referral(self, user_id: int, referrer_id: int) -> bool:
        if self._growth_store is not None:
            return self._growth_store.register_referral(user_id, referrer_id)
        return super().register_referral(user_id, referrer_id)

    def count_referrals(self, referrer_id: int) -> int:
        if self._growth_store is not None:
            return self._growth_store.count_referrals(referrer_id)
        return super().count_referrals(referrer_id)

    def maybe_unlock_premium(self, user_id: int) -> bool:
        """Referral reward must only promise value that actually exists."""
        threshold = getattr(core.Config, "REF_REWARD_THRESHOLD", 3) or 3
        n = self.count_referrals(user_id)
        if n < threshold:
            return False
        settings = self.get_user_settings(user_id)
        if settings.get("premium_unlocked"):
            return False
        # Do NOT disable hide_senior: the editorial pipeline excludes Senior roles.
        self.save_user_settings(user_id, {"premium_unlocked": True})
        self.log_event(user_id, "premium_unlocked", {"referrals": n, "threshold": threshold})
        return True

    def growth_stats(self, days: int = 7) -> Dict:
        if self._growth_store is not None:
            return self._growth_store.growth_stats(days)
        stats = super().growth_stats(days)
        starts = int(stats.get("starts") or 0)
        setup = int(stats.get("setup_done") or 0)
        stats.setdefault("activated_users", 0)
        stats.setdefault("setup_conversion_pct", round(100.0 * setup / starts, 1) if starts else 0.0)
        stats.setdefault("activation_conversion_pct", 0.0)
        stats.setdefault("retention_cohort", 0)
        stats.setdefault("d1_retention_pct", 0.0)
        stats.setdefault("d7_retention_pct", 0.0)
        stats.setdefault("acquisition", [])
        return stats


class JobBot(core.JobBot):
    """Growth-safe UX on top of the existing bot implementation."""

    async def check_admin(self, update) -> bool:
        """Fail closed: no ADMIN_USER_ID means no admin access."""
        user_id = update.effective_user.id if update.effective_user else None
        if not core.Config.ADMIN_USER_ID:
            message = update.effective_message
            if message:
                await message.reply_text("🔒 Админ-команды отключены: ADMIN_USER_ID не настроен.")
            logger.warning("Admin command rejected because ADMIN_USER_ID is unset (user=%s)", user_id)
            return False
        if user_id != core.Config.ADMIN_USER_ID:
            message = update.effective_message
            if message:
                await message.reply_text("❌ У вас нет прав для этой команды")
            logger.warning("Unauthorized admin command by user %s", user_id)
            return False
        return True

    async def cmd_tracks(self, update, context):
        if not await self.check_admin(update):
            return
        return await super().cmd_tracks(update, context)

    async def cmd_start(self, update, context):
        """Activation-first start: show value/actions before a command manual."""
        user = update.effective_user
        user_id = user.id if user else None
        payload = context.args[0] if context.args else None

        if user_id and payload:
            if core.GROWTH_UTILS_AVAILABLE:
                kind, referrer_id = core.parse_start_payload(context.args)
                if kind == "ref" and referrer_id:
                    if self.db.register_referral(user_id, referrer_id):
                        logger.info("🎁 Referral: %s <- %s", user_id, referrer_id)
                        if self.db.maybe_unlock_premium(referrer_id):
                            try:
                                await context.bot.send_message(
                                    chat_id=referrer_id,
                                    text=(
                                        "🏆 Бонус за приглашения разблокирован!\n"
                                        f"Теперь личный digest содержит +{core.Config.REF_REWARD_DIGEST_BONUS} "
                                        "подходящих вакансий. /profile"
                                    ),
                                )
                            except Exception:
                                pass
            if str(payload).startswith("share_"):
                self.db.log_event(user_id, "share_attributed_start", {"payload": payload})

        if user_id:
            self.db.log_event(user_id, "start", {"payload": payload})
            self.db.maybe_unlock_premium(user_id)

        settings = self.db.get_user_settings(user_id) if user_id else {}
        onboarding_done = bool(settings.get("onboarding_done"))
        channel = core.Config.CHANNEL_ID
        channel_link = (
            f"https://t.me/{channel.lstrip('@')}" if str(channel).startswith("@") else ""
        )

        if onboarding_done:
            intro = (
                "🎯 Профиль уже настроен.\n\n"
                "Могу сразу показать свежие совпадения или обновить фильтры."
            )
        else:
            intro = (
                "🎯 Найду Junior/Middle remote IT-вакансии именно под твой стек, "
                "зарплату и географию.\n\n"
                "Настройка занимает около 30 секунд — после неё сразу пришлю первые совпадения."
            )

        rows = [
            [core.InlineKeyboardButton("🎯 Подобрать вакансии", callback_data="growth_quick_setup")],
            [core.InlineKeyboardButton("🆕 Показать свежие", callback_data="growth_quick_fresh")],
        ]
        if core.Config.BOT_USERNAME and user_id:
            rows.append([core.InlineKeyboardButton("🎁 Пригласить друга", callback_data="growth_show_ref")])
        if channel_link:
            rows.append([core.InlineKeyboardButton("📢 Открыть канал", url=channel_link)])

        await update.message.reply_text(
            intro,
            reply_markup=core.InlineKeyboardMarkup(rows),
            disable_web_page_preview=True,
        )

        if payload and str(payload).startswith("share_"):
            job_hash = str(payload)[6:]
            job = self.db.get_job_payload(job_hash)
            if job and self.formatter:
                try:
                    formatted = self.formatter.format_job(
                        job, view_mode="compact", bot_username=core.Config.BOT_USERNAME
                    )
                    await context.bot.send_message(
                        chat_id=user_id,
                        text="Ниже та вакансия, которой с тобой поделились:",
                    )
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=formatted.text,
                        parse_mode=core.ParseMode.MARKDOWN_V2,
                        reply_markup=core.InlineKeyboardMarkup(formatted.reply_markup["inline_keyboard"]),
                        disable_web_page_preview=True,
                    )
                except Exception as exc:
                    logger.debug("Shared vacancy replay failed: %s", exc)

    async def cmd_profile(self, update, context):
        user_id = update.effective_user.id
        self.db.maybe_unlock_premium(user_id)
        s = self.db.get_user_settings(user_id)
        cats = ", ".join(core.CATEGORY_NAMES_RU.get(c, c) for c in s["enabled_categories"][:12])
        invited = self.db.count_referrals(user_id)
        threshold = core.Config.REF_REWARD_THRESHOLD
        premium = (
            f"🏆 on (+{core.Config.REF_REWARD_DIGEST_BONUS} вакансий в digest)"
            if s.get("premium_unlocked")
            else f"off ({invited}/{threshold} приглашений)"
        )
        await update.message.reply_text(
            "👤 Профиль\n"
            f"Категории: {cats or '—'}\n"
            f"Min salary: {s.get('min_salary_filter') or 0}\n"
            f"Стек: {s.get('skills') or '—'}\n"
            f"Digest: {'on' if s.get('digest_enabled') else 'off'}\n"
            f"Alerts: {'on' if s.get('alerts_enabled') else 'off'}\n"
            f"Referral bonus: {premium}\n"
            f"Onboarding: {'done' if s.get('onboarding_done') else 'incomplete'}"
        )

    async def cmd_stats_growth(self, update, context):
        if not await self.check_admin(update):
            return
        days = core.Config.GROWTH_STATS_DAYS
        if context.args:
            try:
                days = max(1, min(int(context.args[0]), 90))
            except ValueError:
                pass
        stats = self.db.growth_stats(days)
        lines = [
            f"📈 Growth funnel · {days}d · backend={getattr(self.db, 'growth_backend', 'sqlite')}",
            f"Users total: {stats['users_total']} · digest:{stats['digest_subscribers']} "
            f"· alerts:{stats['alert_subscribers']} · bonus:{stats['premium_users']}",
            "",
            f"Unique starts: {stats['starts']}",
            f"Setup: {stats['setup_done']} ({stats.get('setup_conversion_pct', 0)}%)",
            f"Activated: {stats.get('activated_users', 0)} "
            f"({stats.get('activation_conversion_pct', 0)}%)",
            f"Unique savers: {stats['saves']}",
            f"Referrals accepted: {stats['referrals']} · ref viewers:{stats['ref_views']}",
            f"Digest users: {stats['personal_digests']} · alert users:{stats['realtime_alerts']}",
            "",
            f"D1 retention: {stats.get('d1_retention_pct', 0)}%",
            f"D7 retention: {stats.get('d7_retention_pct', 0)}%",
        ]
        acquisition = stats.get("acquisition") or []
        if acquisition:
            lines.extend(["", "Acquisition payloads:"])
            for payload, count in acquisition[:8]:
                lines.append(f"  {payload}: {count}")
        lines.extend(["", "Top referrers:"])
        for uid, count in (stats.get("top_referrers") or [])[:8]:
            lines.append(f"  {uid}: {count}")
        if not stats.get("top_referrers"):
            lines.append("  —")
        await update.message.reply_text("\n".join(lines)[:4000])
        self.db.log_event(update.effective_user.id, "stats_growth_view", {"days": days})

    async def _start_setup_from_callback(self, query, user_id: int) -> None:
        self.setup_steps[user_id] = "categories"
        self.db.log_event(user_id, "setup_start", {"source": "start_cta"})
        settings = self.db.get_user_settings(user_id)
        if self.formatter:
            keyboard = self.formatter.create_category_settings_keyboard(settings["enabled_categories"])
            rows = keyboard["inline_keyboard"]
            rows = [
                row for row in rows
                if not (len(row) == 1 and row[0].get("callback_data") == "close_settings")
            ]
            rows.append([{"text": "✅ Далее: зарплата", "callback_data": "setup_next_salary"}])
            await query.message.reply_text(
                "⚙️ 1/4 · Выбери направления, затем нажми «Далее».",
                reply_markup=core.InlineKeyboardMarkup(rows),
            )
        else:
            self.setup_steps[user_id] = "salary"
            await query.message.reply_text("Пришли минимальную зарплату числом, 0 = без фильтра.")

    async def handle_callback(self, update, context):
        query = update.callback_query
        data = query.data or ""
        user_id = update.effective_user.id

        if data == "growth_quick_setup":
            await query.answer()
            await self._start_setup_from_callback(query, user_id)
            return

        if data == "growth_quick_fresh":
            await query.answer("Ищу свежие совпадения")
            n = await self.send_personal_digest(user_id)
            self.db.log_event(user_id, "fresh_preview", {"count": n})
            if not n:
                await query.message.reply_text(
                    "Пока нет свежих совпадений. Настрой профиль — так поиск станет точнее.",
                    reply_markup=core.InlineKeyboardMarkup([[
                        core.InlineKeyboardButton("🎯 Настроить профиль", callback_data="growth_quick_setup")
                    ]]),
                )
            return

        if data == "growth_show_ref":
            await query.answer()
            if not core.Config.BOT_USERNAME:
                await query.message.reply_text("Реферальная ссылка временно недоступна.")
                return
            link = core.build_referral_link(core.Config.BOT_USERNAME, user_id)
            invited = self.db.count_referrals(user_id)
            await query.message.reply_text(
                f"🎁 Твоя ссылка:\n{link}\n\n"
                f"Приглашено: {invited}/{core.Config.REF_REWARD_THRESHOLD}. "
                f"После порога digest станет больше на {core.Config.REF_REWARD_DIGEST_BONUS} вакансии."
            )
            self.db.log_event(user_id, "ref_view", {"source": "start_cta"})
            return

        if data in {"setup_digest_on", "setup_digest_off"}:
            enabled = data == "setup_digest_on"
            await query.answer("Готово")
            self.db.save_user_settings(
                user_id,
                {"digest_enabled": enabled, "onboarding_done": True},
            )
            self.setup_steps.pop(user_id, None)
            self.db.log_event(user_id, "setup_done", {"digest": enabled})
            await query.edit_message_text(
                "✅ Профиль готов. Уже ищу первые подходящие вакансии…"
            )
            n = await self.send_personal_digest(user_id)
            if n:
                self.db.log_event(user_id, "first_value_delivered", {"count": n})
            else:
                await query.message.reply_text(
                    "Под свежий профиль пока нет совпадений. Я продолжу искать; "
                    "фильтры можно изменить через /setup."
                )
            return

        return await super().handle_callback(update, context)


# Install extension points in the original module. Functions such as
# core.init_database() and core.main() resolve these globals at runtime.
core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot

Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = core.main
