"""Vercel runtime adapter for durable vacancy CTA payloads.

Serverless collection intentionally runs with ``use_sqlite=False`` so cross-run
dedup keeps using Telegram history instead of an ephemeral local database.
That also means core ``post_job_with_bot`` normally receives ``db=None`` and
cannot mirror a posted vacancy into ``growth_job_payloads``.

This adapter fills only that gap. It prefers the existing PostgreSQL store when
a raw DSN is configured. Otherwise it uses the narrow Supabase Edge writer,
which authenticates the already-configured Telegram bot token with Telegram
``getMe`` before allowing a single ``growth_job_payloads`` upsert. The original
poster still gets the original ``db`` argument, so Vercel dedup/publication
semantics are unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import channel_bot as core
import content_runtime as runtime
from edge_payload_writer import edge_writer_configured, save_job_payload_edge

logger = logging.getLogger(__name__)

_ORIGINAL_POST_JOB_WITH_BOT = core.post_job_with_bot
_payload_store_instance: Optional[runtime.DatabaseConnection] = None


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
        # A configured but unavailable DSN may fall back to SQLite when
        # REQUIRE_DURABLE_GROWTH=false. That fallback is useless for a
        # serverless callback payload, so do not pretend it is durable.
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


async def post_job_with_durable_payload(bot, job, db=None) -> bool:
    """Pre-save public-card context while preserving core posting semantics."""
    if db is None:
        job_hash = job.get("hash") or core.generate_job_hash(job)
        job["hash"] = job_hash

        store = _payload_store()
        if store is not None:
            store.save_job_payload(job_hash, job)
        elif edge_writer_configured():
            # urllib is intentionally kept out of the event loop. Fail closed:
            # do not publish a CTA whose Resume Match/share payload is known not
            # to be durable.
            await asyncio.to_thread(save_job_payload_edge, job_hash, job)

    # Preserve core behavior exactly: do not pass the mirror DB into posting,
    # registration, dedup or any other path.
    return await _ORIGINAL_POST_JOB_WITH_BOT(bot, job, db=db)


# collect_and_post_once resolves this global at execution time, so patching the
# extension point here is sufficient while keeping the core function unchanged.
core.post_job_with_bot = post_job_with_durable_payload

Config = core.Config
collect_and_post_once = core.collect_and_post_once
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
