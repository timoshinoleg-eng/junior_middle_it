"""Vercel runtime adapter for durable vacancy CTA payloads.

Supabase Edge is the authoritative serverless cross-run idempotency ledger.
Protocol v2 uses ``pending -> sending -> published`` so an expired pre-send
claim can recover while an ambiguous Telegram send is never automatically
released and therefore cannot be duplicated by a later cron invocation.

Telethon source collection and legacy Telegram-history dedup are stateful and
are disabled on Vercel by default. They may be explicitly re-enabled only with
``VERCEL_ENABLE_TELEGRAM_SOURCES=true`` when a serverless-safe session strategy
has been provisioned.

Because that legacy dedup is a no-op on Vercel, candidates are additionally
probed against the durable ledger *before* the per-cycle publication cap is
applied: a claim that is immediately released leaves no ledger trace, so
already published vacancies stop consuming publication slots.
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
    release_job_payload_edge,
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
_durable_probe_probes: ContextVar[int] = ContextVar("durable_probe_probes", default=0)
_durable_probe_unavailable: ContextVar[int] = ContextVar("durable_probe_unavailable", default=0)
_TRUE_VALUES = {"1", "true", "yes", "on"}
# Bounded probe budget: the ledger call is remote, so the cycle never turns into
# a full candidate sweep. The publication cap keeps the tail short in practice.
_PROBE_MAX_CANDIDATES = 24
_PROBE_CONCURRENCY = 6
_PROBE_TIMEOUT = 8.0


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


def _durable_probe_available() -> bool:
    """True when the Edge ledger is the dedup authority for this deployment.

    The probe must mirror the claim path in :func:`post_job_with_durable_payload`
    exactly. When a direct Postgres mirror answers instead, that store is the
    dedup authority and probing Edge would spend remote calls on a ledger the
    publication path never touches.
    """
    if not (os.getenv("VERCEL") or "").strip():
        return False
    if not edge_writer_configured():
        return False
    return _payload_store() is None


async def _probe_candidate_state(job) -> Optional[bool]:
    """Resolve one candidate against the durable ledger.

    Returns ``True`` when the ledger has no record of the vacancy, ``False``
    when it already published it, and ``None`` when the ledger could not be
    consulted. The probe claims the hash and immediately releases it, and the
    release endpoint deletes a still ``pending`` row outright, so nothing is
    left behind and the real publication claim later in the same cycle starts
    clean.

    Fail-open by design: an unreachable ledger must never remove a candidate,
    because the send path re-verifies the claim before touching Telegram.
    """
    job_hash = job.get("hash") or core.generate_job_hash(job)
    if not job_hash:
        return True

    try:
        claim = await asyncio.wait_for(
            asyncio.to_thread(claim_job_payload_edge, job_hash, job),
            timeout=_PROBE_TIMEOUT,
        )
    except Exception as exc:
        logger.warning(
            "Durable probe unavailable for %s: %s",
            job_hash,
            type(exc).__name__,
        )
        return None

    if not bool(getattr(claim, "claimed", False)):
        logger.info(
            "Durable probe found existing publication for %s (state=%s)",
            job_hash,
            getattr(claim, "publication_state", "") or "unknown",
        )
        return False

    await _edge_transition_with_retries(
        release_job_payload_edge,
        job_hash,
        getattr(claim, "claim_token", ""),
        label="probe-release",
    )
    return True


async def filter_candidates_by_durable_state(candidates):
    """Pre-selection dedup gate backed by the durable ledger.

    Without it the per-cycle slots are consumed by vacancies the ledger already
    published, because the send-time claim rejects them only after selection.
    """
    pending = list(candidates or [])
    if not pending or not _durable_probe_available():
        return pending

    # Only the highest ranked candidates can consume a slot, so the remote
    # budget is spent there. Everything past the budget stays publishable and
    # keeps its historical behaviour.
    budget = pending[:_PROBE_MAX_CANDIDATES]
    ranked = sorted(budget, key=core.job_quality_score, reverse=True)
    semaphore = asyncio.Semaphore(_PROBE_CONCURRENCY)

    async def probe(job):
        async with semaphore:
            return await _probe_candidate_state(job)

    # Probe tasks run in their own context, so the counters are reconciled here.
    _durable_probe_probes.set(_durable_probe_probes.get() + len(ranked))
    outcomes = await asyncio.gather(*(probe(job) for job in ranked), return_exceptions=True)
    publishable = set()
    for job, outcome in zip(ranked, outcomes):
        if isinstance(outcome, BaseException) or outcome is None:
            # Fail open: an unresolved probe must never remove a candidate.
            _durable_probe_unavailable.set(_durable_probe_unavailable.get() + 1)
            if isinstance(outcome, BaseException):
                logger.warning("Durable probe task failed: %s", type(outcome).__name__)
            outcome = True
        if outcome:
            publishable.add(id(job))

    probed_ids = {id(job) for job in budget}
    duplicates = len(budget) - len(publishable)
    if duplicates:
        logger.info("Durable pre-selection probe dropped %s published candidates", duplicates)
    return [
        job for job in pending
        if id(job) not in probed_ids or id(job) in publishable
    ]


def _reset_durable_telemetry() -> None:
    telemetry = getattr(core, "CYCLE_TELEMETRY", None)
    if not isinstance(telemetry, dict):
        return
    telemetry["durable_begin_failures"] = 0
    telemetry["durable_ambiguous_sends"] = 0
    telemetry["durable_finalize_failures"] = 0
    telemetry["durable_probe_probes"] = 0
    telemetry["durable_probe_unavailable"] = 0
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
    probe_token = _durable_probe_probes.set(0)
    probe_unavailable_token = _durable_probe_unavailable.set(0)
    try:
        result = await _ORIGINAL_COLLECT_AND_POST_ONCE(*args, **kwargs)
        durable_duplicates = _durable_duplicate_skips.get()
        begin_failures = _durable_begin_failures.get()
        ambiguous_sends = _durable_ambiguous_sends.get()
        finalize_failures = _durable_finalize_failures.get()
        probe_probes = _durable_probe_probes.get()
        probe_unavailable = _durable_probe_unavailable.get()
    finally:
        _durable_duplicate_skips.reset(duplicate_token)
        _durable_begin_failures.reset(begin_token)
        _durable_ambiguous_sends.reset(ambiguous_token)
        _durable_finalize_failures.reset(finalize_token)
        _durable_probe_probes.reset(probe_token)
        _durable_probe_unavailable.reset(probe_unavailable_token)

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
    reconciled["durable_probe_probes"] = probe_probes
    reconciled["durable_probe_unavailable"] = probe_unavailable
    # The core collector owns the pre-selection funnel stage; keep the field
    # present for dashboards even when the default no-op hook is installed.
    reconciled["pre_selection_duplicates"] = int(
        reconciled.get("pre_selection_duplicates") or 0
    )

    telemetry = getattr(core, "CYCLE_TELEMETRY", None)
    if isinstance(telemetry, dict):
        telemetry["failed_posts"] = reconciled["failed"]
        telemetry["durable_begin_failures"] = begin_failures
        telemetry["durable_ambiguous_sends"] = ambiguous_sends
        telemetry["durable_finalize_failures"] = finalize_failures
        telemetry["durable_probe_probes"] = probe_probes
        telemetry["durable_probe_unavailable"] = probe_unavailable
        funnel = telemetry.setdefault("funnel", {})
        if isinstance(funnel, dict):
            funnel["duplicates"] = reconciled["duplicates"]
            funnel["durable_duplicates"] = durable_duplicates
            funnel["pre_selection_duplicate"] = reconciled["pre_selection_duplicates"]

    return reconciled


# The core collector resolves these globals at execution time.
core.post_job_with_bot = post_job_with_durable_payload
core.probe_candidates_for_publication = filter_candidates_by_durable_state

Config = core.Config
CYCLE_TELEMETRY = core.CYCLE_TELEMETRY
