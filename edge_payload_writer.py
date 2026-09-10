"""Narrow Vercel -> Supabase Edge transport for durable vacancy publication.

Protocol v2 uses an owned, expiring claim before Telegram publication and an
explicit ``pending -> sending -> published`` state machine. The payload sent to
Edge is compact and canonical so oversized source responses cannot abort a cron
batch.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


_HASH_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_CLAIM_TOKEN_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
MAX_REQUEST_BYTES = 300_000
PROTOCOL_VERSION = 2


@dataclass(frozen=True)
class EdgeClaim:
    claimed: bool
    claim_token: str = ""
    publication_state: str = ""
    lease_expires_at: str = ""


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


def _state_url(base_url: str, state: str) -> str:
    writer = _writer_url(base_url)
    return f"{writer}/{state}" if writer else ""


def _release_url(base_url: str) -> str:
    return _state_url(base_url, "release")


def _sending_url(base_url: str) -> str:
    return _state_url(base_url, "sending")


def _published_url(base_url: str) -> str:
    return _state_url(base_url, "published")


def edge_writer_configured() -> bool:
    return bool(
        _writer_url(os.getenv("GROWTH_PUBLIC_URL", ""))
        and (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    )


def _clean_text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _compact_list(value: object, *, max_items: int, item_limit: int) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return [_clean_text(item, item_limit) for item in list(values)[:max_items] if _clean_text(item, item_limit)]


def compact_job_payload(job_hash: str, job: Dict) -> Dict:
    """Return the bounded public/CTA payload persisted by the Edge ledger."""
    source_published = (
        job.get("published")
        or job.get("created")
        or job.get("publication_date")
        or job.get("date_published")
        or job.get("posted_at")
        or job.get("posted_date")
    )
    return {
        "hash": job_hash,
        "title": _clean_text(job.get("title"), 300),
        "company": _clean_text(job.get("company"), 200),
        "level": _clean_text(job.get("level") or "Junior", 40),
        "category": _clean_text(job.get("category") or "other", 60),
        "salary": _clean_text(job.get("salary") or "Не указана", 120),
        "salary_min_usd": job.get("salary_min_usd") if isinstance(job.get("salary_min_usd"), (int, float)) else None,
        "location": _clean_text(job.get("location") or "Remote", 180),
        "description": _clean_text(job.get("description"), 1200),
        "url": _clean_text(job.get("url"), 2000),
        "source": _clean_text(job.get("source"), 160),
        "source_published_at": _clean_text(source_published, 100),
        "tags": _compact_list(job.get("tags"), max_items=12, item_limit=80),
        "quality_gate_status": _clean_text(job.get("quality_gate_status"), 30),
        "url_preflight_status": _clean_text(job.get("url_preflight_status"), 30),
        "url_preflight_http_status": job.get("url_preflight_http_status") if isinstance(job.get("url_preflight_http_status"), int) else None,
        "primary_track": _clean_text(job.get("primary_track") or job.get("thematic_track"), 80),
        "specialization_tags": _compact_list(job.get("specialization_tags"), max_items=8, item_limit=80),
        "geo_restriction": _clean_text(job.get("geo_restriction"), 180),
        "remote_scope": _clean_text(job.get("remote_scope"), 60),
        "remote_evidence": _clean_text(job.get("remote_evidence"), 300),
        "level_source": _clean_text(job.get("level_source"), 60),
        "level_confidence": job.get("level_confidence") if isinstance(job.get("level_confidence"), (int, float)) else None,
        "employment_type": _clean_text(job.get("employment_type"), 80),
    }


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
            "User-Agent": "junior-middle-it-vercel/2.0",
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


def _valid_hash(job_hash: str) -> str:
    job_hash = str(job_hash or "").strip()
    if not _HASH_RE.fullmatch(job_hash):
        raise ValueError("invalid job hash")
    return job_hash


def _valid_claim_token(claim_token: str) -> str:
    claim_token = str(claim_token or "").strip()
    if not _CLAIM_TOKEN_RE.fullmatch(claim_token):
        raise ValueError("invalid claim token")
    return claim_token


def claim_job_payload_edge(job_hash: str, job: Dict, *, timeout: float = 12.0) -> EdgeClaim:
    """Acquire or reclaim an expiring pending claim using protocol v2."""
    endpoint = _writer_url(os.getenv("GROWTH_PUBLIC_URL", ""))
    job_hash = _valid_hash(job_hash)
    if not isinstance(job, dict):
        raise TypeError("job must be a dict")
    compact = compact_job_payload(job_hash, job)
    result = _post(
        endpoint,
        {"protocol_version": PROTOCOL_VERSION, "hash": job_hash, "payload": compact},
        timeout=timeout,
    )
    if result.get("hash") != job_hash:
        raise RuntimeError("Edge payload writer hash mismatch")
    claimed = result.get("claimed")
    if not isinstance(claimed, bool):
        raise RuntimeError("Edge payload writer missing claim state")
    claim_token = str(result.get("claim_token") or "")
    if claimed:
        _valid_claim_token(claim_token)
    return EdgeClaim(
        claimed=claimed,
        claim_token=claim_token,
        publication_state=str(result.get("publication_state") or ""),
        lease_expires_at=str(result.get("lease_expires_at") or ""),
    )


def begin_job_payload_send_edge(job_hash: str, claim_token: str, *, timeout: float = 12.0) -> bool:
    endpoint = _sending_url(os.getenv("GROWTH_PUBLIC_URL", ""))
    job_hash = _valid_hash(job_hash)
    claim_token = _valid_claim_token(claim_token)
    result = _post(endpoint, {"hash": job_hash, "claim_token": claim_token}, timeout=timeout)
    if result.get("hash") != job_hash or not isinstance(result.get("sending"), bool):
        raise RuntimeError("Edge sending transition returned an invalid response")
    return bool(result["sending"])


def mark_job_payload_published_edge(job_hash: str, claim_token: str, *, timeout: float = 12.0) -> bool:
    endpoint = _published_url(os.getenv("GROWTH_PUBLIC_URL", ""))
    job_hash = _valid_hash(job_hash)
    claim_token = _valid_claim_token(claim_token)
    result = _post(endpoint, {"hash": job_hash, "claim_token": claim_token}, timeout=timeout)
    if result.get("hash") != job_hash or not isinstance(result.get("published"), bool):
        raise RuntimeError("Edge published transition returned an invalid response")
    return bool(result["published"])


def save_job_payload_edge(job_hash: str, job: Dict, *, timeout: float = 12.0) -> bool:
    """Protocol-v1 compatibility helper retained during the rolling upgrade."""
    endpoint = _writer_url(os.getenv("GROWTH_PUBLIC_URL", ""))
    job_hash = _valid_hash(job_hash)
    if not isinstance(job, dict):
        raise TypeError("job must be a dict")
    result = _post(endpoint, {"hash": job_hash, "payload": compact_job_payload(job_hash, job)}, timeout=timeout)
    if result.get("hash") != job_hash or not isinstance(result.get("created"), bool):
        raise RuntimeError("Edge payload writer returned an invalid legacy response")
    return bool(result["created"])


def release_job_payload_edge(job_hash: str, claim_token: str = "", *, timeout: float = 12.0) -> bool:
    """Release only a pending owned claim; token-less mode is rollout compatibility."""
    endpoint = _release_url(os.getenv("GROWTH_PUBLIC_URL", ""))
    job_hash = _valid_hash(job_hash)
    body = {"hash": job_hash}
    if claim_token:
        body["claim_token"] = _valid_claim_token(claim_token)
    result = _post(endpoint, body, timeout=timeout)
    if result.get("hash") != job_hash or not isinstance(result.get("released"), bool):
        raise RuntimeError("Edge payload release returned an invalid response")
    return bool(result["released"])
