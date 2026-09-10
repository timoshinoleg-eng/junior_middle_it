"""Vercel runtime adapter for durable vacancy CTA payloads.

Serverless collection runs without persistent SQLite. Supabase Edge therefore
acts as both the durable payload store and the atomic cross-run idempotency
ledger for Vercel publication.

Before Telegram publication the adapter claims the vacancy hash. Existing
claims are skipped. If Telegram publication fails, the claim is released so a
later cron run can retry safely. Durable duplicate skips are tracked per
collector invocation so they are reported as duplicates, never as failed posts.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextvars import ContextVar
from typing import Optional

import channel_bot as core
import content_runtime as runtime
from edge_payload_writer import (
    edge_writer_configured,
    release_job_payload_edge,
    save_job_payload_edge,
)

logger = logging.getLogger(__name__)

_ORIGINAL_POST_JOB_WITH_BOT = core.post_job_with_bot
_ORIGINAL_COLLECT_AND_POST_ONCE = core.collect_and_post_once
_payload_store_instance: Optional[runtime.DatabaseConnection] = None
_durable_duplicate_skips: ContextVar[int] = ContextVar(
    "durable_duplicate_skips",
    default=0,
)


def _durable_dsn_configured() -> bool:
    value = (os.getenv("GROWTH_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    return value.startswith(("postgresql://", "postgres://"))


def _payload_store() -> Optional[runtime.DatabaseConnection]:
    """Return a Postgres-backed payload mirror, never an ephemeral fallback."""
    global _payload_store_instance
    if not _durable_dsn_configured():
        return None
    if _payload_store_instance is not None:
        return _payload_store_instance

    store = runtime.DatabaseConnection(":memory:")
    if getattr(store, "growth_backend", "sqlite") != "postgres":
        try:
            store.close()
        finally:
            logger.warning(
                "Serverless durable payload mirror unavailable; trying Edge writer "
                "when configured."
            )
        return None

    _payload_store_instance = store
    return store


async def _release_edge_claim(job_hash: str) -> None:
    """Best-effort compensation after a failed publication."""
    try:
        await asyncio.to_thread(release_job_payload_edge, job_hash)
    except Exception as exc:
        # Do not hide the original Telegram failure. A stale claim is safer than
        # a duplicate post; operators can reconcile it explicitly if necessary.
        logger.error(
            "Failed to release durable job claim %s: %s",
            job_hash,
            type(exc).__name__,
        )


async def post_job_with_durable_payload(bot, job, db=None) -> bool:
    """Publish a vacancy only after acquiring a durable cross-run claim."""
    edge_claimed = False
    job_hash = job.get("hash") or core.generate_job_hash(job)
    job["hash"] = job_hash

    if db is None:
        store = _payload_store()
        if store is not None:
            # Direct Postgres deployments retain their historical behavior.
            store.save_job_payload(job_hash, job)
        elif edge_writer_configured():
            edge_claimed = await asyncio.to_thread(save_job_payload_edge, job_hash, job)
            if not edge_claimed:
                _durable_duplicate_skips.set(_durable_duplicate_skips.get() + 1)
                logger.info("Durable duplicate skipped: %s", job_hash)
                return False

    try:
        posted = await _ORIGINAL_POST_JOB_WITH_BOT(bot, job, db=db)
    except BaseException:
        if edge_claimed:
            await _release_edge_claim(job_hash)
        raise

    if not posted and edge_claimed:
        await _release_edge_claim(job_hash)
    return bool(posted)


async def collect_and_post_once(*args, **kwargs):
    """Run the core collector and reconcile durable duplicate telemetry."""
    token = _durable_duplicate_skips.set(0)
    try:
        result = await _ORIGINAL_COLLECT_AND_POST_ONCE(*args, **kwargs)
        durable_duplicates = _durable_duplicate_skips.get()
    finally:
        _durable_duplicate_skips.reset(token)

    if not isinstance(result, dict) or durable_duplicates <= 0:
        return result

    reconciled = dict(result)
    failed = int(reconciled.get("failed") or 0)
    duplicates = int(reconciled.get("duplicates") or 0)
    reconciled["failed"] = max(0, failed - durable_duplicates)
    reconciled["duplicates"] = duplicates + durable_duplicates
    reconciled["durable_duplicates"] = durable_duplicates

    telemetry = getattr(core, "CYCLE_TELEMETRY", None)
    if isinstance(telemetry, dict):
        telemetry["failed_posts"] = reconciled["failed"]
        funnel = telemetry.get("funnel")
        if isinstance(funnel, dict):
            funnel["duplicates"] = reconciled["duplicates"]
            funnel["durable_duplicates"] = durable_duplicates

    return reconciled


# collect_and_post_once resolves this global at execution time, so patching the
# extension point here is sufficient while keeping the core function unchanged.
core.post_job_with_bot = post_job_with_durable_payload

Config = core.Config
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
