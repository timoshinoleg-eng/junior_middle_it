"""Narrow Vercel -> Supabase Edge writer for durable vacancy payloads.

The Edge Function verifies the existing Telegram bot token with Telegram
``getMe`` and exposes only two authenticated mutations:
- claim a vacancy hash before Telegram publication;
- release that claim when publication fails so a later run can retry.

The atomic claim is the cross-run idempotency authority for serverless posting.
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


_HASH_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
MAX_REQUEST_BYTES = 300_000


def _base_url() -> str:
    raw = str(os.getenv("GROWTH_PUBLIC_URL", "") or "").strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    if not (parsed.hostname or "").lower().endswith(".supabase.co"):
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _writer_url(base_url: str) -> str:
    raw = str(base_url or "").strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    if not (parsed.hostname or "").lower().endswith(".supabase.co"):
        return ""
    path = parsed.path.rstrip("/") + "/job-payloads"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _release_url(base_url: str) -> str:
    writer = _writer_url(base_url)
    return f"{writer}/release" if writer else ""


def edge_writer_configured() -> bool:
    return bool(
        _writer_url(os.getenv("GROWTH_PUBLIC_URL", ""))
        and (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    )


def _post(endpoint: str, payload: Dict, *, timeout: float) -> Dict:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not endpoint:
        raise RuntimeError("GROWTH_PUBLIC_URL is missing or invalid")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    body = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("job payload is too large")

    request = Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Telegram-Bot-Token": token,
            "User-Agent": "junior-middle-it-vercel/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read(64_000).decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Edge payload writer rejected request ({exc.code})") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Edge payload writer unavailable: {type(exc).__name__}") from exc

    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("Edge payload writer returned an invalid response")
    return result


def save_job_payload_edge(job_hash: str, job: Dict, *, timeout: float = 12.0) -> bool:
    """Atomically claim ``job_hash`` and persist its public payload.

    Returns ``True`` only for the first successful claim. ``False`` means the
    hash already exists in the durable ledger and therefore must not be posted
    again by this serverless invocation.
    """
    endpoint = _writer_url(os.getenv("GROWTH_PUBLIC_URL", ""))
    job_hash = str(job_hash or "").strip()
    if not _HASH_RE.fullmatch(job_hash):
        raise ValueError("invalid job hash")
    if not isinstance(job, dict):
        raise TypeError("job must be a dict")

    result = _post(endpoint, {"hash": job_hash, "payload": job}, timeout=timeout)
    if result.get("hash") != job_hash:
        raise RuntimeError("Edge payload writer hash mismatch")
    created = result.get("created")
    if not isinstance(created, bool):
        raise RuntimeError("Edge payload writer missing claim state")
    return created


def release_job_payload_edge(job_hash: str, *, timeout: float = 12.0) -> bool:
    """Release a previously claimed hash after a failed Telegram publication."""
    endpoint = _release_url(os.getenv("GROWTH_PUBLIC_URL", ""))
    job_hash = str(job_hash or "").strip()
    if not _HASH_RE.fullmatch(job_hash):
        raise ValueError("invalid job hash")

    result = _post(endpoint, {"hash": job_hash}, timeout=timeout)
    if result.get("hash") != job_hash:
        raise RuntimeError("Edge payload release hash mismatch")
    released = result.get("released")
    if not isinstance(released, bool):
        raise RuntimeError("Edge payload writer missing release state")
    return released
