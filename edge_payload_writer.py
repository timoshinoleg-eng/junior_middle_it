"""Narrow Vercel -> Supabase Edge writer for public vacancy payloads.

The serverless collector already has the Telegram bot token required to publish.
Rather than exporting a database password to Vercel, the Edge Function verifies
that token with Telegram ``getMe`` and permits only a single job-payload upsert.
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


def edge_writer_configured() -> bool:
    return bool(
        _writer_url(os.getenv("GROWTH_PUBLIC_URL", ""))
        and (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    )


def save_job_payload_edge(job_hash: str, job: Dict, *, timeout: float = 12.0) -> bool:
    endpoint = _writer_url(os.getenv("GROWTH_PUBLIC_URL", ""))
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    job_hash = str(job_hash or "").strip()
    if not endpoint:
        raise RuntimeError("GROWTH_PUBLIC_URL is missing or invalid")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
    if not _HASH_RE.fullmatch(job_hash):
        raise ValueError("invalid job hash")
    if not isinstance(job, dict):
        raise TypeError("job must be a dict")

    body = json.dumps(
        {"hash": job_hash, "payload": job},
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
            payload = json.loads(response.read(64_000).decode("utf-8"))
    except HTTPError as exc:
        # Never include response bodies or request headers: either can be
        # operationally sensitive and callers only need fail-closed semantics.
        raise RuntimeError(f"Edge payload writer rejected request ({exc.code})") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Edge payload writer unavailable: {type(exc).__name__}") from exc

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("Edge payload writer returned an invalid response")
    if payload.get("hash") != job_hash:
        raise RuntimeError("Edge payload writer hash mismatch")
    return True
