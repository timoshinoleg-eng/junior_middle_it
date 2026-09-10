"""Final P7 product-state layer shared by polling and webhook runtimes.

This module removes the remaining user-visible dependence on ephemeral SQLite:
- favorites live in Supabase when durable growth storage is configured;
- personal digests read published vacancy payloads from the durable ledger;
- weekly salary reports read the same published durable ledger.

SQLite behavior remains unchanged for local development and explicit fallback.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import channel_bot as core
import public_acquisition_runtime as p7


class DatabaseConnection(p7.DatabaseConnection):
    """P7 runtime with durable favorites and published-job reads."""

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
        """Read published durable payloads so digests survive cold starts/redeploys."""
        if self._growth_store is None:
            return super().recent_jobs_for_digest(hours=hours, limit=limit)
        hours = max(1, min(int(hours), 24 * 45))
        limit = max(1, min(int(limit), 500))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
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
                (cutoff, limit),
            )
            rows = cur.fetchall()
        out: List[Dict] = []
        for job_hash, raw in rows:
            payload = self._payload_dict(raw, str(job_hash))
            if payload:
                out.append(payload)
        return out

    def jobs_with_salary_for_report(self, days: int = 14, limit: int = 500) -> List[Dict]:
        """Use published durable payloads for the weekly salary content magnet."""
        if self._growth_store is None:
            return super().jobs_with_salary_for_report(days=days, limit=limit)
        days = max(1, min(int(days), 45))
        limit = max(1, min(int(limit), 1000))
        # Pull a bounded superset because some published payloads have no parsed
        # salary. Filtering in Python avoids database casts on untrusted source text.
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
                (cutoff, min(2000, max(limit, limit * 2))),
            )
            rows = cur.fetchall()
        jobs: List[Dict] = []
        for job_hash, raw in rows:
            payload = self._payload_dict(raw, str(job_hash))
            salary = payload.get("salary_min_usd")
            if isinstance(salary, bool) or not isinstance(salary, (int, float)) or salary <= 0:
                continue
            jobs.append(payload)
            if len(jobs) >= limit:
                break
        return jobs


class JobBot(p7.JobBot):
    """Make save callbacks truthful when the durable vacancy no longer exists."""

    async def handle_callback(self, update, context):
        query = update.callback_query
        data = (query.data or "") if query else ""
        user_id = update.effective_user.id if update.effective_user else None
        if data.startswith("save:") and user_id:
            job_hash = data.split(":", 1)[1]
            saved = self.db.add_favorite(user_id, job_hash)
            if not saved:
                await query.answer("Вакансия уже устарела", show_alert=True)
                return
            self.db.log_event(user_id, "save_job", {"hash": job_hash})
            await query.answer("Сохранено")
            try:
                await context.bot.send_message(chat_id=user_id, text="💾 Вакансия в /favorites")
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
