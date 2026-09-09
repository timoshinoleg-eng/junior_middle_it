"""Sanitized readiness helpers for the Vercel/serverless production surface."""
from __future__ import annotations

import hmac
import os
from typing import Dict, List


POSTING_REQUIRED_ENV = ("TELEGRAM_BOT_TOKEN", "CHANNEL_ID", "CRON_SECRET")


def _present(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


def missing_posting_env() -> List[str]:
    """Return missing collector/posting env names without exposing values."""
    return [name for name in POSTING_REQUIRED_ENV if not _present(name)]


def durable_growth_configured() -> bool:
    return _present("GROWTH_DATABASE_URL") or _present("DATABASE_URL")


def cron_authorized(authorization: str) -> bool:
    """Require a configured CRON_SECRET and Authorization bearer header only."""
    secret = (os.getenv("CRON_SECRET") or "").strip()
    if not secret:
        return False
    supplied = str(authorization or "")
    return hmac.compare_digest(supplied, f"Bearer {secret}")


def readiness() -> Dict[str, object]:
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
    return {
        "ok": collector_ready and public_acquisition_ready,
        "service": "junior_middle_it",
        "collector_ready": collector_ready,
        "durable_growth": durable_growth,
        "public_acquisition_ready": public_acquisition_ready,
        "missing_required": missing,
    }
