"""P4 production runtime: attributable weekly acquisition content.

This module composes the canonical production bot without modifying the mature
collection loop. The existing weekly salary scheduled job calls
``JobBot.post_weekly_salary_magnet``; overriding that single extension point
posts both the salary pulse and one curated, forwardable vacancy collection.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import channel_bot as core
import production_bot as product
from content_magnets import append_campaign_cta, build_weekly_picks_report

logger = logging.getLogger(__name__)


class DatabaseConnection(product.DatabaseConnection):
    """Expose recent durable vacancy payloads for content generation."""

    def recent_jobs_for_magnets(self, hours: int = 168, limit: int = 250) -> List[Dict]:
        hours = max(1, min(int(hours), 24 * 60))
        limit = max(1, min(int(limit), 1000))
        if self._growth_store is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            try:
                with self._growth_store._ensure_conn().cursor() as cur:
                    cur.execute(
                        """
                        SELECT hash, payload
                        FROM growth_job_payloads
                        WHERE updated_at >= %s
                        ORDER BY updated_at DESC
                        LIMIT %s
                        """,
                        (cutoff, limit),
                    )
                    rows = cur.fetchall()
                out = []
                for job_hash, payload in rows:
                    if not isinstance(payload, dict):
                        try:
                            payload = json.loads(payload)
                        except Exception:
                            continue
                    job = dict(payload)
                    job.setdefault("hash", str(job_hash))
                    out.append(job)
                return out
            except Exception as exc:
                logger.warning("Durable content source unavailable, using SQLite: %s", exc)
        return self.recent_jobs_for_digest(hours=hours, limit=limit)


class JobBot(product.JobBot):
    """Post measurable acquisition magnets through the existing weekly schedule."""

    async def post_weekly_salary_magnet(self) -> bool:
        recent_14d = self.db.recent_jobs_for_magnets(hours=14 * 24, limit=500)
        salary_jobs = []
        for job in recent_14d:
            if core.GROWTH_UTILS_AVAILABLE:
                core.enrich_job_salary_fields(job)
            if job.get("salary_min_usd"):
                salary_jobs.append(job)

        salary_text = core.build_salary_magnet_report(
            salary_jobs,
            category_names=core.CATEGORY_NAMES_RU,
        ) if core.GROWTH_UTILS_AVAILABLE else ""
        salary_text, salary_campaign = append_campaign_cta(
            salary_text,
            core.Config.BOT_USERNAME,
            "salary",
        )

        sent_any = False
        if salary_text:
            try:
                await self.application.bot.send_message(
                    chat_id=core.Config.CHANNEL_ID,
                    text=salary_text,
                    disable_web_page_preview=True,
                )
                self.db.log_event(
                    None,
                    "salary_magnet_posted",
                    {"jobs": len(salary_jobs), "campaign": salary_campaign},
                )
                sent_any = True
            except Exception as exc:
                logger.error("salary magnet failed: %s", exc)

        weekly_jobs = self.db.recent_jobs_for_magnets(hours=7 * 24, limit=250)
        picks_text, picks_campaign = build_weekly_picks_report(
            weekly_jobs,
            bot_username=core.Config.BOT_USERNAME,
            limit=7,
        )
        if picks_text:
            try:
                if sent_any:
                    await asyncio.sleep(1.0)
                await self.application.bot.send_message(
                    chat_id=core.Config.CHANNEL_ID,
                    text=picks_text,
                    disable_web_page_preview=True,
                )
                self.db.log_event(
                    None,
                    "weekly_picks_posted",
                    {"jobs": min(7, len(weekly_jobs)), "campaign": picks_campaign},
                )
                sent_any = True
            except Exception as exc:
                logger.error("weekly picks magnet failed: %s", exc)

        return sent_any


# Install the final P4 extension points before core.main constructs the bot.
core.DatabaseConnection = DatabaseConnection
core.JobBot = JobBot

Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
main = core.main
