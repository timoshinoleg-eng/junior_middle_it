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

# The Copilot rollout finishes with three manual steps - the migrations, the
# Edge deploy and the production flags. None of them can be observed from the
# outside without credentials, so the health payload reports the state as
# booleans. No value, URL or secret ever leaves this module.
COPILOT_ROLLOUT_FLAGS = (
    ("enabled", "COPILOT_ENABLED"),
    ("pdf", "COPILOT_PDF_ENABLED"),
    ("forward", "COPILOT_FORWARD_ENABLED"),
    ("outcome", "COPILOT_OUTCOME_ENABLED"),
    ("upskill", "COPILOT_UPSKILL_ENABLED"),
    ("webapp", "COPILOT_WEBAPP_ENABLED"),
    ("llm", "COPILOT_LLM_ENABLED"),
)
_TRUTHY = {"1", "true", "yes", "on"}


def _present(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


def _flag_enabled(name: str) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    return bool(raw) and raw in _TRUTHY


def copilot_rollout() -> Dict[str, object]:
    """Report which Copilot features are switched on in this deployment.

    The result is a set of booleans, so the payload can be published without
    exposing a secret or an internal URL.
    """
    state: Dict[str, object] = {
        key: _flag_enabled(name) for key, name in COPILOT_ROLLOUT_FLAGS
    }
    state["webapp_url"] = _present("COPILOT_WEBAPP_URL")
    state["tables"] = copilot_tables_present()
    state["ready"] = bool(state.get("enabled")) and bool(state.get("webapp"))
    return state


# One table per migration of P9-P12. The probe touches all of them in a single
# statement, so the answer is "all four migrations are applied" or nothing.
_COPILOT_TABLE_PROBE = (
    "SELECT (SELECT count(*) FROM growth_copilot_profiles) "
    "+ (SELECT count(*) FROM growth_copilot_ai_reports) "
    "+ (SELECT count(*) FROM growth_copilot_applications) "
    "+ (SELECT count(*) FROM growth_copilot_rate_limits)"
)
_PROBE_TIMEOUT = 8.0


def _interactive_bridge_url() -> str:
    """The interactive Edge bridge of this deployment, if it is configured."""
    endpoint = (os.getenv("GROWTH_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    mode = (os.getenv("GROWTH_HTTP_AUTH_MODE") or "").strip().lower()
    if mode != "telegram" or not endpoint.startswith("https://"):
        return ""
    if "supabase.co/functions/v1/interactive-growth-proxy" not in endpoint:
        return ""
    return endpoint


def copilot_tables_present() -> Optional[bool]:
    """Whether the tables of the migrations P9-P12 exist in the database.

    ``None`` means "unknown": the probe needs the interactive bridge and the
    bot token, and a failure of the probe must never turn the health endpoint
    red.
    """
    endpoint = _interactive_bridge_url()
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not endpoint or not token:
        return None
    body = json.dumps({"query": _COPILOT_TABLE_PROBE, "params": []}).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json", "x-telegram-bot-token": token},
        method="POST",
    )
    try:
        with urlopen(request, timeout=_PROBE_TIMEOUT) as response:
            return int(getattr(response, "status", 200)) == 200
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", "replace")
        if int(exc.code) == 503 and "query failed" in payload:
            # The bridge answered, the database refused the statement: a table
            # of the migrations is missing.
            return False
        return None
    except Exception:
        return None


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
        "copilot": copilot_rollout(),
    }
    if include_publication_counters:
        payload["publication"] = publication_counters()
    return payload
