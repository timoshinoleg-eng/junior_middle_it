"""P6 interactive runtime: quality-weighted referral loop.

Leaderboard scores use *activated* invitees (setup_done), not raw starts.  This
keeps the growth loop aligned with useful subscribers and makes trivial account
spam less rewarding. Existing referral bonus semantics stay backward compatible.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import channel_bot as core
import interactive_runtime as interactive
from referral_growth import build_referral_share_url, format_top_scores, pct


class DatabaseConnection(interactive.DatabaseConnection):
    """Add referral quality/rank queries for SQLite and PostgreSQL."""

    def _referral_rows(self, days: int | None = None):
        if days is not None:
            days = max(1, min(int(days), 90))
        cutoff_aware = datetime.now(timezone.utc) - timedelta(days=days or 36500)

        if self._growth_store is not None:
            with self._growth_store._ensure_conn().cursor() as cur:
                cur.execute(
                    """
                    SELECT r.referrer_id, r.user_id, r.created_at,
                           EXISTS (
                               SELECT 1 FROM growth_events e
                               WHERE e.user_id = r.user_id AND e.name = 'setup_done'
                           ) AS activated
                    FROM growth_referrals r
                    WHERE r.created_at >= %s
                    """,
                    (cutoff_aware,),
                )
                return cur.fetchall()

        cutoff_sqlite = cutoff_aware.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        return self.fetchall(
            """
            SELECT r.referrer_id, r.user_id, r.created_at,
                   EXISTS (
                       SELECT 1 FROM events e
                       WHERE e.user_id = r.user_id AND e.name = 'setup_done'
                   ) AS activated
            FROM referrals r
            WHERE r.created_at >= ?
            """,
            (cutoff_sqlite,),
        )

    @staticmethod
    def _score_rows(rows) -> Dict[int, Dict[str, int]]:
        scores: Dict[int, Dict[str, int]] = {}
        for referrer_id, _invitee_id, _created_at, activated in rows:
            rid = int(referrer_id)
            entry = scores.setdefault(rid, {"accepted": 0, "activated": 0})
            entry["accepted"] += 1
            if bool(activated):
                entry["activated"] += 1
        return scores

    def referral_progress(self, user_id: int, days: int = 7) -> Dict:
        uid = int(user_id)
        # Total accepted keeps the reward progress compatible with the existing
        # threshold, while total activated describes referral quality.
        total_rows = self._referral_rows(None)
        week_rows = self._referral_rows(days)
        totals = self._score_rows(total_rows)
        weekly = self._score_rows(week_rows)

        total = totals.get(uid, {"accepted": 0, "activated": 0})
        current = weekly.get(uid, {"accepted": 0, "activated": 0})
        ranked = sorted(
            (
                (rid, values["activated"], values["accepted"])
                for rid, values in weekly.items()
                if values["activated"] > 0
            ),
            key=lambda item: (-item[1], -item[2], item[0]),
        )
        rank = None
        if current["activated"] > 0:
            # Competition rank: equal activated scores share the same rank.
            rank = 1 + sum(1 for _rid, activated, _accepted in ranked if activated > current["activated"])

        top_scores = [activated for _rid, activated, _accepted in ranked[:5]]
        return {
            "accepted_total": int(total["accepted"]),
            "activated_total": int(total["activated"]),
            "activation_pct_total": pct(total["activated"], total["accepted"]),
            "accepted_period": int(current["accepted"]),
            "activated_period": int(current["activated"]),
            "activation_pct_period": pct(current["activated"], current["accepted"]),
            "rank_period": rank,
            "active_referrers_period": len(ranked),
            "top_scores": top_scores,
        }

    def referral_funnel(self, days: int = 7) -> Dict:
        rows = self._referral_rows(days)
        scores = self._score_rows(rows)
        accepted = len(rows)
        activated = sum(1 for row in rows if bool(row[3]))
        active_referrers = sum(1 for values in scores.values() if values["activated"] > 0)
        ranked = sorted(
            (values["activated"] for values in scores.values() if values["activated"] > 0),
            reverse=True,
        )
        return {
            "days": days,
            "accepted": accepted,
            "activated": activated,
            "activation_pct": pct(activated, accepted),
            "referrers": len(scores),
            "active_referrers": active_referrers,
            "top_score": ranked[0] if ranked else 0,
        }


class JobBot(interactive.JobBot):
    """Expose personal referral quality and a privacy-safe weekly leaderboard."""

    async def cmd_ref(self, update, context):
        user_id = update.effective_user.id if update.effective_user else None
        if not user_id:
            return
        self.db.log_event(user_id, "ref_view", {"surface": "referral_2"})

        if not core.Config.BOT_USERNAME:
            await update.message.reply_text(
                "Реферальная ссылка временно недоступна: BOT_USERNAME не настроен."
            )
            return
        if not core.GROWTH_UTILS_AVAILABLE:
            await update.message.reply_text("Реферальный модуль временно недоступен.")
            return

        link = core.build_referral_link(core.Config.BOT_USERNAME, user_id)
        stats = self.db.referral_progress(user_id, days=7)
        threshold = int(core.Config.REF_REWARD_THRESHOLD or 3)
        bonus_done = min(stats["accepted_total"], threshold)
        if stats["rank_period"] is None:
            rank_line = "Позиция недели: пока без активированных приглашённых"
        else:
            rank_line = (
                f"Позиция недели: #{stats['rank_period']} из "
                f"{stats['active_referrers_period']} активных рефереров"
            )

        text = (
            "🎁 Referral 2.0\n\n"
            f"Твоя ссылка:\n{link}\n\n"
            f"Всего приглашено: {stats['accepted_total']}\n"
            f"Активировались: {stats['activated_total']} "
            f"({stats['activation_pct_total']}%)\n"
            f"За 7 дней: {stats['accepted_period']} приглашено · "
            f"{stats['activated_period']} активировались\n"
            f"{rank_line}\n"
            f"Топ недели по активированным: {format_top_scores(stats['top_scores'])}\n\n"
            f"Бонус digest: {bonus_done}/{threshold} принятых приглашений.\n"
            "В рейтинге считаются только друзья, которые завершили настройку профиля."
        )
        share_url = build_referral_share_url(link)
        buttons = []
        if share_url:
            buttons.append([
                core.InlineKeyboardButton("📤 Пригласить друга", url=share_url)
            ])
        buttons.append([
            core.InlineKeyboardButton("🎯 Мой профиль", callback_data="growth_quick_setup")
        ])
        await update.message.reply_text(
            text,
            reply_markup=core.InlineKeyboardMarkup(buttons),
            disable_web_page_preview=True,
        )

    async def cmd_stats_growth(self, update, context):
        user_id = update.effective_user.id if update.effective_user else None
        # Let the inherited implementation own the denial message and all
        # existing funnel/utility output. Avoid emitting referral stats after a
        # denied parent call.
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
        stats = self.db.referral_funnel(days)
        await update.message.reply_text(
            "🤝 Referral quality · " + str(days) + "d\n"
            f"Accepted: {stats['accepted']}\n"
            f"Activated: {stats['activated']} ({stats['activation_pct']}%)\n"
            f"Referrers: {stats['referrers']} · with activated users: {stats['active_referrers']}\n"
            f"Top activated score: {stats['top_score']}"
        )


core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot

Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = interactive.main
