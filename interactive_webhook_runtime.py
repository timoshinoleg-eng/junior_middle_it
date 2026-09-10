"""Request-driven Telegram runtime for Vercel.

This module deliberately does *not* call ``core.main()``, ``Application.start()``,
``run_polling()`` or ``run_webhook()``. Each HTTPS request processes exactly one
Telegram Update. User/product state is durable in Supabase, while PTB itself is
initialized only for the duration of the request. This prevents the Vercel
interactive path from starting a second vacancy collector or in-process JobQueue.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Dict
from urllib.parse import urlsplit, urlunsplit

from secure_http_logging import configure_sensitive_http_logging

configure_sensitive_http_logging()

MAX_UPDATE_BYTES = 1_000_000
UPDATE_LEASE_MINUTES = 10


def _safe_public_growth_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.endswith(".supabase.co"):
        return ""
    if parsed.path.rstrip("/") != "/functions/v1/growth-proxy":
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def interactive_growth_url(public_url: str | None = None) -> str:
    base = _safe_public_growth_url(
        public_url if public_url is not None else os.getenv("GROWTH_PUBLIC_URL", "")
    )
    if not base:
        return ""
    parsed = urlsplit(base)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, "/functions/v1/interactive-growth-proxy", "", "")
    )


def configure_growth_transport() -> str:
    """Bind this function process to the restricted bot-token Edge transport."""
    endpoint = interactive_growth_url()
    if not endpoint:
        raise RuntimeError("interactive growth transport is not configured")
    if not (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip():
        raise RuntimeError("Telegram bot token is not configured")
    os.environ["GROWTH_DATABASE_URL"] = endpoint
    os.environ["GROWTH_HTTP_AUTH_MODE"] = "telegram"
    os.environ["REQUIRE_DURABLE_GROWTH"] = "true"
    os.environ.setdefault("DISABLE_FILE_LOG", "true")
    return endpoint


def webhook_secret(token: str | None = None) -> str:
    """Derive a Telegram-compatible secret_token without adding another secret."""
    value = str(token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    if not value:
        return ""
    return hashlib.sha256(("junior-middle-it:webhook:" + value).encode("utf-8")).hexdigest()


def webhook_secret_valid(candidate: str | None) -> bool:
    expected = webhook_secret()
    supplied = str(candidate or "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def production_webhook_url() -> str:
    explicit = (os.getenv("WEBHOOK_PUBLIC_URL") or "").strip().rstrip("/")
    if explicit:
        try:
            parsed = urlsplit(explicit)
        except ValueError:
            return ""
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            return ""
        base = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        return base if base.endswith("/api/webhook") else base + "/api/webhook"

    domain = (
        (os.getenv("PUBLIC_SITE_URL") or "").strip()
        or (os.getenv("VERCEL_PROJECT_PRODUCTION_URL") or "").strip()
    ).rstrip("/")
    if not domain:
        return ""
    if not domain.startswith(("http://", "https://")):
        domain = "https://" + domain
    try:
        parsed = urlsplit(domain)
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return urlunsplit(("https", parsed.netloc, "/api/webhook", "", ""))


def _runtime():
    """Import final P7 runtime only after installing the webhook growth transport."""
    configure_growth_transport()
    import http_growth_patch

    if not http_growth_patch.install():
        raise RuntimeError("interactive growth transport refused endpoint")
    # Defensive support for warm/test import order. DatabaseConnection resolves
    # this module global when instantiated.
    import channel_bot_v2

    channel_bot_v2.PostgresGrowthStore = http_growth_patch.HttpPostgresGrowthStore
    import durable_product_state as runtime
    import channel_bot as core

    return runtime, core


def build_runtime():
    """Build handlers and durable DB without starting polling or PTB JobQueue."""
    runtime, core = _runtime()
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    channel_id = (os.getenv("CHANNEL_ID") or "").strip()
    if not token or not channel_id:
        raise RuntimeError("interactive Telegram runtime is incomplete")

    application = core.Application.builder().token(token).build()
    db = runtime.DatabaseConnection(":memory:")
    if getattr(db, "growth_backend", "sqlite") != "postgres":
        db.close()
        raise RuntimeError("interactive runtime requires durable growth backend")
    job_bot = runtime.JobBot(application, db)

    commands = {
        "start": job_bot.cmd_start,
        "ref": job_bot.cmd_ref,
        "setup": job_bot.cmd_setup,
        "profile": job_bot.cmd_profile,
        "digest": job_bot.cmd_digest,
        "alerts": job_bot.cmd_alerts,
        "stats_growth": job_bot.cmd_stats_growth,
        "tracks": job_bot.cmd_tracks,
        "sources": job_bot.cmd_sources,
        "status": job_bot.cmd_status,
        "last": job_bot.cmd_last,
        "favorites": job_bot.cmd_favorites,
        "categories": job_bot.cmd_categories,
        "pause": job_bot.cmd_pause,
        "resume": job_bot.cmd_resume,
    }
    for command, callback in commands.items():
        application.add_handler(CommandHandler(command, callback))
    application.add_handler(CallbackQueryHandler(job_bot.handle_callback))

    document_filter = (
        filters.Document.PDF
        | filters.Document.DOCX
        | filters.Document.FileExtension("pdf")
        | filters.Document.FileExtension("docx")
    )
    application.add_handler(
        MessageHandler(
            (filters.TEXT | document_filter) & ~filters.COMMAND,
            job_bot.handle_setup_text,
        )
    )
    return application, db, job_bot


def build_application():
    """Compatibility helper used by tests and narrow callers."""
    application, db, _job_bot = build_runtime()
    return application, db


class TelegramUpdateLedger:
    """Atomic lease ledger for duplicate/retried Telegram webhook deliveries."""

    def __init__(self, db):
        store = getattr(db, "_growth_store", None)
        if store is None:
            raise RuntimeError("update ledger requires durable growth backend")
        self.store = store

    def claim(self, update_id: int) -> str:
        """Return ``claimed``, ``processed`` or ``busy`` for one update id."""
        uid = int(update_id)
        with self.store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_telegram_updates
                    (update_id, state, lease_expires_at, first_seen_at, processed_at)
                VALUES (%s, 'processing', NOW() + INTERVAL '10 minutes', NOW(), NULL)
                ON CONFLICT (update_id) DO UPDATE SET
                    state='processing',
                    lease_expires_at=NOW() + INTERVAL '10 minutes',
                    processed_at=NULL
                WHERE growth_telegram_updates.state='processing'
                  AND growth_telegram_updates.lease_expires_at IS NOT NULL
                  AND growth_telegram_updates.lease_expires_at <= NOW()
                RETURNING update_id
                """,
                (uid,),
            )
            if cur.fetchone():
                cur.execute(
                    "DELETE FROM growth_telegram_updates "
                    "WHERE state='processed' AND processed_at < NOW() - INTERVAL '30 days'"
                )
                return "claimed"
            cur.execute(
                "SELECT state FROM growth_telegram_updates WHERE update_id=%s LIMIT 1",
                (uid,),
            )
            row = cur.fetchone()
        if row and str(row[0]) == "processed":
            return "processed"
        return "busy"

    def complete(self, update_id: int) -> None:
        with self.store._ensure_conn().cursor() as cur:
            cur.execute(
                """
                UPDATE growth_telegram_updates
                SET state='processed', lease_expires_at=NULL, processed_at=NOW()
                WHERE update_id=%s AND state='processing'
                """,
                (int(update_id),),
            )


def _parse_update_id(payload: Dict[str, Any]) -> int:
    value = payload.get("update_id")
    if isinstance(value, bool):
        raise ValueError("invalid update id")
    try:
        update_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid update id") from exc
    if update_id < 0:
        raise ValueError("invalid update id")
    return update_id


async def process_update_payload(payload: Dict[str, Any]) -> str:
    """Process one Telegram update and preserve retryability on handler failure."""
    if not isinstance(payload, dict):
        raise ValueError("Telegram update must be an object")
    update_id = _parse_update_id(payload)
    application, db, _job_bot = build_runtime()
    ledger = TelegramUpdateLedger(db)
    claim_state = ledger.claim(update_id)
    if claim_state == "processed":
        db.close()
        return "duplicate"
    if claim_state == "busy":
        db.close()
        return "busy"

    from telegram import Update

    handler_errors: list[BaseException] = []

    async def capture_error(_update, context):
        error = getattr(context, "error", None)
        handler_errors.append(error if isinstance(error, BaseException) else RuntimeError("handler failed"))

    application.add_error_handler(capture_error)
    try:
        update = Update.de_json(payload, application.bot)
        if update is None:
            raise ValueError("invalid Telegram update")
        async with application:
            # Deliberately no application.start(): start() would launch JobQueue.
            await application.process_update(update)
        if handler_errors:
            raise RuntimeError("Telegram update handler failed") from handler_errors[0]
        ledger.complete(update_id)
        return "processed"
    finally:
        db.close()
