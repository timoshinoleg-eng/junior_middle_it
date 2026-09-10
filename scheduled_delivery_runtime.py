"""Request-driven daily/weekly Telegram delivery for Vercel.

The old polling runtime scheduled these jobs in PTB JobQueue. This module runs
only when an authenticated HTTP cron calls it. Durable receipts are inserted
before every outbound target, intentionally biasing ambiguous Telegram outcomes
toward at-most-once delivery instead of duplicate user/channel spam.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Dict

from interactive_webhook_runtime import build_runtime


class DeliveryReceiptLedger:
    def __init__(self, db):
        store = getattr(db, "_growth_store", None)
        if store is None:
            raise RuntimeError("delivery receipts require durable growth backend")
        self.store = store

    @staticmethod
    def target_key(chat_id) -> str:
        raw = str(chat_id or "").strip()
        if not raw:
            raise ValueError("empty delivery target")
        # Keep user/channel IDs out of operational receipt keys while preserving
        # deterministic uniqueness for the same Telegram target.
        return hashlib.sha256(("telegram:" + raw).encode("utf-8")).hexdigest()

    def claim(self, kind: str, period_key: str, chat_id) -> bool:
        target = self.target_key(chat_id)
        with self.store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_delivery_receipts
                    (delivery_kind, period_key, target_key, state, item_count, created_at, sent_at)
                VALUES (%s, %s, %s, 'sending', 0, NOW(), NULL)
                ON CONFLICT (delivery_kind, period_key, target_key) DO NOTHING
                RETURNING target_key
                """,
                (str(kind), str(period_key), target),
            )
            claimed = bool(cur.fetchone())
            if claimed:
                cur.execute(
                    "DELETE FROM growth_delivery_receipts "
                    "WHERE created_at < NOW() - INTERVAL '90 days'"
                )
            return claimed

    def complete(self, kind: str, period_key: str, chat_id, item_count: int) -> None:
        target = self.target_key(chat_id)
        count = max(0, int(item_count))
        state = "sent" if count else "no_content"
        with self.store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                UPDATE growth_delivery_receipts
                SET state=%s, item_count=%s, sent_at=NOW()
                WHERE delivery_kind=%s AND period_key=%s AND target_key=%s
                  AND state='sending'
                """,
                (state, count, str(kind), str(period_key), target),
            )


def _daily_period(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).date().isoformat()


def _weekly_period(now: datetime | None = None) -> str:
    value = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    iso = value.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _channel_digest_text(jobs: list[dict], *, bot_username: str = "") -> str:
    lines = ["📬 Свежие Junior/Middle remote IT вакансии"]
    for job in jobs:
        title = str(job.get("title") or "Вакансия").strip()[:160]
        company = str(job.get("company") or "").strip()[:100]
        url = str(job.get("url") or "").strip()[:1000]
        headline = f"• {title} — {company}" if company else f"• {title}"
        lines.append(headline)
        if url.startswith(("https://", "http://")):
            lines.append(url)
    username = str(bot_username or "").strip().lstrip("@")
    if username:
        lines.append(f"Бот: https://t.me/{username}?start=invite")
    return "\n".join(lines)[:4000]


async def run_daily_delivery(*, runtime_factory=build_runtime) -> Dict:
    application, db, job_bot = runtime_factory()
    runtime = __import__("durable_product_state")
    core = __import__("channel_bot")
    period = _daily_period()
    ledger = DeliveryReceiptLedger(db)
    result = {
        "ok": True,
        "kind": "daily",
        "period": period,
        "channel_sent": 0,
        "personal_targets": 0,
        "personal_jobs": 0,
        "skipped_receipts": 0,
        "unresolved": 0,
    }
    try:
        async with application:
            if core.Config.ENABLE_DAILY_DIGEST:
                channel_id = core.Config.CHANNEL_ID
                if ledger.claim("daily_channel", period, channel_id):
                    try:
                        jobs = db.recent_jobs_for_digest(
                            hours=max(24, core.Config.PERSONAL_DIGEST_LOOKBACK_HOURS),
                            limit=core.Config.DIGEST_MAX_JOBS,
                        )
                        if jobs:
                            await application.bot.send_message(
                                chat_id=channel_id,
                                text=_channel_digest_text(
                                    jobs,
                                    bot_username=core.Config.BOT_USERNAME,
                                ),
                                disable_web_page_preview=True,
                            )
                            result["channel_sent"] = len(jobs)
                            db.log_event(None, "daily_digest_posted", {"count": len(jobs)})
                        ledger.complete("daily_channel", period, channel_id, len(jobs))
                    except Exception:
                        # Leave receipt at `sending`: outbound outcome may be ambiguous.
                        result["unresolved"] += 1
                else:
                    result["skipped_receipts"] += 1

            if core.Config.ENABLE_PERSONAL_DIGEST:
                for uid in db.list_digest_subscribers():
                    if not ledger.claim("daily_personal", period, uid):
                        result["skipped_receipts"] += 1
                        continue
                    result["personal_targets"] += 1
                    try:
                        count = await job_bot.send_personal_digest(uid)
                        result["personal_jobs"] += int(count or 0)
                        ledger.complete("daily_personal", period, uid, int(count or 0))
                    except Exception:
                        result["unresolved"] += 1
    finally:
        db.close()
    # At-most-once ambiguity is surfaced for operations but must not induce an
    # automatic retry that could duplicate outbound Telegram messages.
    result["ok"] = True
    return result


async def run_weekly_delivery(*, runtime_factory=build_runtime) -> Dict:
    application, db, job_bot = runtime_factory()
    core = __import__("channel_bot")
    period = _weekly_period()
    ledger = DeliveryReceiptLedger(db)
    result = {
        "ok": True,
        "kind": "weekly",
        "period": period,
        "posted": False,
        "skipped_receipts": 0,
        "unresolved": 0,
    }
    try:
        async with application:
            if not core.Config.ENABLE_WEEKLY_SALARY_REPORT:
                return result
            channel_id = core.Config.CHANNEL_ID
            if not ledger.claim("weekly_salary", period, channel_id):
                result["skipped_receipts"] += 1
                return result
            try:
                posted = bool(await job_bot.post_weekly_salary_magnet())
                result["posted"] = posted
                ledger.complete("weekly_salary", period, channel_id, 1 if posted else 0)
            except Exception:
                result["unresolved"] += 1
    finally:
        db.close()
    return result


async def run_scheduled_delivery(kind: str, *, runtime_factory=build_runtime) -> Dict:
    normalized = str(kind or "").strip().lower()
    if normalized == "daily":
        return await run_daily_delivery(runtime_factory=runtime_factory)
    if normalized == "weekly":
        return await run_weekly_delivery(runtime_factory=runtime_factory)
    raise ValueError("unsupported delivery kind")
