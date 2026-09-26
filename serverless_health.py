"""Sanitized readiness helpers for the Vercel/serverless production surface."""
from __future__ import annotations

import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


POSTING_REQUIRED_ENV = ("TELEGRAM_BOT_TOKEN", "CHANNEL_ID", "CRON_SECRET")
_PUBLIC_JOBS_LIMIT = 300
_PUBLIC_JOBS_TIMEOUT = 5.0


def _present(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


def missing_posting_env() -> List[str]:
    """Return missing collector/posting env names without exposing values."""
    return [name for name in POSTING_REQUIRED_ENV if not _present(name)]


def durable_growth_configured() -> bool:
    # Vercel may either connect to Postgres directly or consume the public,
    # read-only Supabase Edge projection produced by the persistent Render bot.
    return (
        _present("GROWTH_DATABASE_URL")
        or _present("DATABASE_URL")
        or _present("GROWTH_PUBLIC_URL")
    )


def _public_jobs_url() -> str:
    """Public, unauthenticated ledger projection endpoint on the Supabase host."""
    raw = str(os.getenv("GROWTH_PUBLIC_URL", "") or "").strip().rstrip("/")
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    if not (parsed.hostname or "").lower().endswith(".supabase.co"):
        return ""
    path = parsed.path.rstrip("/") + "/public-jobs"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _public_job_timestamps() -> List[datetime]:
    """Read the ledger's published timestamps through the public projection."""
    url = _public_jobs_url()
    if not url:
        return []
    request = Request(
        f"{url}?days=7&limit={_PUBLIC_JOBS_LIMIT}",
        headers={
            "Accept": "application/json",
            "User-Agent": "junior-middle-it-health/1.0",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=_PUBLIC_JOBS_TIMEOUT) as response:
            payload = json.loads(response.read(256_000).decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return []

    stamps: List[datetime] = []
    for row in (payload.get("rows") or []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict):
            continue
        for key in ("updated_at", "published_at", "created_at"):
            raw = str(row.get(key) or "").strip()
            if not raw:
                continue
            try:
                stamps.append(
                    datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
                )
            except ValueError:
                continue
            break
    return stamps


def publication_counters(now: Optional[datetime] = None) -> Dict[str, object]:
    """Report how much the channel actually published, straight from the ledger.

    The cron response only exists inside the cron run, so an external monitor
    had no way to see the one number that matters: whether the channel is still
    posting at all. These counters are derived from durable published rows, so
    they survive between invocations. A missing projection is reported as
    ``None`` instead of a silent zero.
    """
    url = _public_jobs_url()
    if not url:
        return {"ledger_visible": False, "reason": "growth_public_url_missing"}
    stamps = _public_job_timestamps()
    if not stamps:
        return {"ledger_visible": True, "published_1h": None, "published_24h": None, "published_7d": None}
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return {
        "ledger_visible": True,
        "published_1h": sum(1 for s in stamps if (current - s) <= timedelta(hours=1)),
        "published_24h": sum(1 for s in stamps if (current - s) <= timedelta(hours=24)),
        "published_7d": len(stamps),
    }


def cron_authorized(authorization: str) -> bool:
    """Require a configured CRON_SECRET and Authorization bearer header only."""
    secret = (os.getenv("CRON_SECRET") or "").strip()
    if not secret:
        return False
    supplied = str(authorization or "").strip()
    return hmac.compare_digest(supplied, f"Bearer {secret}")


def readiness(*, include_publication_counters: bool = False) -> Dict[str, object]:
    """Describe core collector readiness and full P7 production readiness.

    Publishing can remain available while durable growth is being wired, but
    ``ok`` represents the complete P7 contract so monitoring cannot report a
    false green when the public landing has no durable vacancy payload source.
    """
    missing = missing_posting_env()
    collector_ready = not missing
    durable_growth = durable_growth_configured()
    bot_username = _present("BOT_USERNAME")
    public_acquisition_ready = durable_growth and bot_username
    payload: Dict[str, object] = {
        "ok": collector_ready and public_acquisition_ready,
        "service": "junior_middle_it",
        "collector_ready": collector_ready,
        "durable_growth": durable_growth,
        "public_acquisition_ready": public_acquisition_ready,
        "missing_required": missing,
    }
    if include_publication_counters:
        payload["publication"] = publication_counters()
    return payload
