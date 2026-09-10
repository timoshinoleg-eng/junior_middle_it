"""Production-only Telegram webhook administration helpers.

No bot token is returned or logged. Mutating actions are intended to be called
only from the CRON_SECRET-protected Vercel admin endpoint.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from interactive_webhook_runtime import (
    build_runtime,
    production_webhook_url,
    webhook_secret,
)


def _telegram_api(method: str, payload: dict | None = None, *, timeout: float = 15.0) -> dict:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("telegram_not_configured")
    body = json.dumps(payload or {}, separators=(",", ":")).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(64_000)
    except HTTPError as exc:
        raise RuntimeError(f"telegram_http_{exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"telegram_unavailable_{type(exc).__name__}") from exc
    try:
        result = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("telegram_invalid_response") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("telegram_api_rejected")
    return result


def _sanitize_webhook_info(result: dict) -> dict:
    data = result.get("result") if isinstance(result, dict) else {}
    data = data if isinstance(data, dict) else {}
    return {
        "url": str(data.get("url") or "")[:500],
        "has_custom_certificate": bool(data.get("has_custom_certificate")),
        "pending_update_count": int(data.get("pending_update_count") or 0),
        "last_error_date": data.get("last_error_date"),
        "last_error_message": str(data.get("last_error_message") or "")[:300],
        "max_connections": data.get("max_connections"),
        "allowed_updates": data.get("allowed_updates") or [],
    }


def webhook_status() -> dict:
    return _sanitize_webhook_info(_telegram_api("getWebhookInfo"))


def interactive_backend_preflight() -> None:
    application, db, _job_bot = build_runtime()
    try:
        # Read-only request proves Vercel -> interactive Edge -> Postgres and
        # validates the bot token through the Edge getMe check.
        db.get_user_settings(-1)
    finally:
        db.close()
        # build_runtime does not initialize the PTB application, so no async
        # shutdown is required here.
        del application


def enable_webhook() -> dict:
    if (os.getenv("VERCEL_ENV") or "").strip().lower() != "production":
        raise RuntimeError("not_production")
    url = production_webhook_url()
    secret = webhook_secret()
    if not url or not secret:
        raise RuntimeError("webhook_not_configured")

    interactive_backend_preflight()
    _telegram_api(
        "setWebhook",
        {
            "url": url,
            "secret_token": secret,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": False,
            "max_connections": 20,
        },
    )
    info = webhook_status()
    if info.get("url") != url:
        raise RuntimeError("webhook_verification_failed")
    return info


def disable_webhook() -> dict:
    if (os.getenv("VERCEL_ENV") or "").strip().lower() != "production":
        raise RuntimeError("not_production")
    _telegram_api("deleteWebhook", {"drop_pending_updates": False})
    info = webhook_status()
    if info.get("url"):
        raise RuntimeError("webhook_disable_verification_failed")
    return info
