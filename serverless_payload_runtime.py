"""Vercel runtime adapter for durable vacancy CTA payloads.

Supabase Edge is the authoritative serverless cross-run idempotency ledger.
Protocol v2 uses ``pending -> sending -> published`` so an expired pre-send
claim can recover while an ambiguous Telegram send is never automatically
released and therefore cannot be duplicated by a later cron invocation.

Telethon source collection and legacy Telegram-history dedup are stateful and
are disabled on Vercel by default. They may be explicitly re-enabled only with
``VERCEL_ENABLE_TELEGRAM_SOURCES=true`` when a serverless-safe session strategy
has been provisioned.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextvars import ContextVar
from typing import Callable, Optional

import channel_bot as core
import content_runtime as runtime
from edge_payload_writer import (
    begin_job_payload_send_edge,
    claim_job_payload_edge,
    edge_writer_configured,
    mark_job_payload_published_edge,
)

logger = logging.getLogger(__name__)

_ORIGINAL_POST_JOB_WITH_BOT = core.post_job_with_bot
_ORIGINAL_COLLECT_AND_POST_ONCE = core.collect_and_post_once
_ORIGINAL_GET_RECENT_CHANNEL_JOB_HASHES = core.get_recent_channel_job_hashes
_payload_store_instance: Optional[runtime.DatabaseConnection] = None
_durable_duplicate_skips: ContextVar[int] = ContextVar("durable_duplicate_skips", default=0)
_durable_begin_failures: ContextVar[int] = ContextVar("durable_begin_failures", default=0)
_durable_ambiguous_sends: ContextVar[int] = ContextVar("durable_ambiguous_sends", default=0)
_durable_finalize_failures: ContextVar[int] = ContextVar("durable_finalize_failures", default=0)
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _durable_dsn_configured() -> bool:
    value = (os.getenv("GROWTH_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    return value.startswith(("postgresql://", "postgres://"))


async def _empty_recent_channel_job_hashes(limit=None) -> set:
    """Serverless no-op: durable Supabase claims are the dedup authority."""
    return set()


def _configure_serverless_source_policy() -> None:
    """Disable stateful Telethon work on Vercel unless explicitly opted in."""
    if not (os.getenv("VERCEL") or "").strip():
        core.get_recent_channel_job_hashes = _ORIGINAL_GET_RECENT_CHANNEL_JOB_HASHES
        return

    opt_in = (os.getenv("VERCEL_ENABLE_TELEGRAM_SOURCES") or "").strip().lower() in _TRUE_VALUES
    core.Config.ENABLE_TELEGRAM_CHANNELS = opt_in
    core.get_recent_channel_job_hashes = (
        _ORIGINAL_GET_RECENT_CHANNEL_JOB_HASHES if opt_in else _empty_recent_channel_job_hashes
    )


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
            logger.warning("Serverless durable Postgres mirror unavailable; trying Edge writer.")
        return None

    _payload_store_instance = store
    return store


async def _edge_transition_with_retries(
    func: Callable[[str, str], bool],
    job_hash: str,
    claim_token: str,
    *,
    label: str,
    attempts: int = 3,
) -> bool:
    """Retry idempotent Edge state transitions without exposing credentials."""
    last_exc: Optional[BaseException] = None
    for attempt in range(max(1, attempts)):
        try:
            result = await asyncio.to_thread(func, job_hash, claim_token)
            if result:
                return True
            logger.error("Durable %s transition rejected for %s", label, job_hash)
            return False
        except Exception as exc:
            last_exc = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(0.25 * (attempt + 1))
    logger.error(
        "Durable %s transition unavailable for %s: %s",
        label,
        job_hash,
        type(last_exc).__name__ if last_exc else "unknown",
    )
    return False


async def post_job_with_durable_payload(bot, job, db=None) -> bool:
    """Publish only after acquiring and advancing an owned durable claim.

    Once a claim enters ``sending`` it is intentionally preserved on every
    ambiguous Telegram outcome. That biases the system toward at-most-once
    publication instead of risking a duplicate public post.
    """
    job_hash = job.get("hash") or core.generate_job_hash(job)
    job["hash"] = job_hash

    if db is None:
        store = _payload_store()
        if store is not None:
            # Non-Edge deployments retain their historical direct-Postgres behavior.
            store.save_job_payload(job_hash, job)
        elif edge_writer_configured():
            # Validate routing before consuming a durable claim.
            if not core.get_target_channels(job):
                logger.error("No target channels for durable job %s", job_hash)
                return False

            claim = await asyncio.to_thread(claim_job_payload_edge, job_hash, job)
            if not claim.claimed:
                _durable_duplicate_skips.set(_durable_duplicate_skips.get() + 1)
                logger.info(
                    "Durable duplicate skipped: %s (state=%s)",
                    job_hash,
                    claim.publication_state or "unknown",
                )
                return False

            began = await _edge_transition_with_retries(
                begin_job_payload_send_edge,
                job_hash,
                claim.claim_token,
                label="begin-send",
            )
            if not began:
                # If Edge committed the transition but the response was lost, a
                # send here would be unsafe. Preserve the claim and do not post.
                _durable_begin_failures.set(_durable_begin_failures.get() + 1)
                return False

            try:
                posted = await _ORIGINAL_POST_JOB_WITH_BOT(bot, job, db=db)
            except BaseException:
                _durable_ambiguous_sends.set(_durable_ambiguous_sends.get() + 1)
                logger.error(
                    "Telegram send raised after durable sending transition for %s; claim preserved",
                    job_hash,
                )
                raise

            if not posted:
                # Generic Telegram/client exceptions may happen after the server
                # accepted a message. Never release a sending claim automatically.
                _durable_ambiguous_sends.set(_durable_ambiguous_sends.get() + 1)
                logger.error(
                    "Telegram send outcome ambiguous for %s; durable sending claim preserved",
                    job_hash,
                )
                return False

            finalized = await _edge_transition_with_retries(
                mark_job_payload_published_edge,
                job_hash,
                claim.claim_token,
                label="published",
            )
            if not finalized:
                # Telegram succeeded, so returning True prevents a false post
                # failure/retry. The sending row remains hidden and blocks replay
                # until an operator reconciles it.
                _durable_finalize_failures.set(_durable_finalize_failures.get() + 1)
                logger.error(
                    "Telegram posted %s but durable finalize is unresolved; claim preserved",
                    job_hash,
                )
            return True

    return bool(await _ORIGINAL_POST_JOB_WITH_BOT(bot, job, db=db))


def _reset_durable_telemetry() -> None:
    telemetry = getattr(core, "CYCLE_TELEMETRY", None)
    if not isinstance(telemetry, dict):
        return
    telemetry["durable_begin_failures"] = 0
    telemetry["durable_ambiguous_sends"] = 0
    telemetry["durable_finalize_failures"] = 0
    funnel = telemetry.setdefault("funnel", {})
    if isinstance(funnel, dict):
        funnel["durable_duplicates"] = 0


async def collect_and_post_once(*args, **kwargs):
    """Run the core collector and reconcile per-invocation durable telemetry."""
    _configure_serverless_source_policy()
    _reset_durable_telemetry()
    duplicate_token = _durable_duplicate_skips.set(0)
    begin_token = _durable_begin_failures.set(0)
    ambiguous_token = _durable_ambiguous_sends.set(0)
    finalize_token = _durable_finalize_failures.set(0)
    try:
        result = await _ORIGINAL_COLLECT_AND_POST_ONCE(*args, **kwargs)
        durable_duplicates = _durable_duplicate_skips.get()
        begin_failures = _durable_begin_failures.get()
        ambiguous_sends = _durable_ambiguous_sends.get()
        finalize_failures = _durable_finalize_failures.get()
    finally:
        _durable_duplicate_skips.reset(duplicate_token)
        _durable_begin_failures.reset(begin_token)
        _durable_ambiguous_sends.reset(ambiguous_token)
        _durable_finalize_failures.reset(finalize_token)

    if not isinstance(result, dict):
        return result

    reconciled = dict(result)
    failed = int(reconciled.get("failed") or 0)
    duplicates = int(reconciled.get("duplicates") or 0)
    reconciled["failed"] = max(0, failed - durable_duplicates)
    reconciled["duplicates"] = duplicates + durable_duplicates
    reconciled["durable_duplicates"] = durable_duplicates
    reconciled["durable_begin_failures"] = begin_failures
    reconciled["durable_ambiguous_sends"] = ambiguous_sends
    reconciled["durable_finalize_failures"] = finalize_failures

    telemetry = getattr(core, "CYCLE_TELEMETRY", None)
    if isinstance(telemetry, dict):
        telemetry["failed_posts"] = reconciled["failed"]
        telemetry["durable_begin_failures"] = begin_failures
        telemetry["durable_ambiguous_sends"] = ambiguous_sends
        telemetry["durable_finalize_failures"] = finalize_failures
        funnel = telemetry.setdefault("funnel", {})
        if isinstance(funnel, dict):
            funnel["duplicates"] = reconciled["duplicates"]
            funnel["durable_duplicates"] = durable_duplicates

    return reconciled


# The core collector resolves this global at execution time.
core.post_job_with_bot = post_job_with_durable_payload

Config = core.Config
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
