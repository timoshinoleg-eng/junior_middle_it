"""Read-only client for public vacancy payloads exposed by Supabase Edge."""
from __future__ import annotations

import json
import logging
from typing import Dict, List
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 2_000_000


def _safe_endpoint(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    # P7 uses a hosted Supabase Edge Function. Keep server-side fetching pinned
    # to Supabase rather than turning this setting into a generic SSRF primitive.
    host = (parsed.hostname or "").lower()
    if not host.endswith(".supabase.co"):
        return ""
    return raw


def load_recent_public_jobs_http(endpoint: str, *, days: int = 14, limit: int = 120) -> List[Dict]:
    base = _safe_endpoint(endpoint)
    if not base:
        return []
    days = max(1, min(int(days), 45))
    limit = max(1, min(int(limit), 300))
    parsed = urlsplit(base)
    path = parsed.path.rstrip("/") + "/public-jobs"
    url = urlunsplit((parsed.scheme, parsed.netloc, path, urlencode({"days": days, "limit": limit}), ""))
    request = Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "junior-middle-it-vercel/1.0"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("response too large")
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        logger.warning("Public growth endpoint unavailable: %s", type(exc).__name__)
        return []

    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    jobs: List[Dict] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
            continue
        job = dict(row["payload"])
        job.setdefault("hash", str(row.get("hash") or ""))
        job["_updated_at"] = row.get("updated_at")
        jobs.append(job)
    return jobs
