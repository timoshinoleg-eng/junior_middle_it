"""Production publication guardrails for the Vercel vacancy collector.

The core collector intentionally supports broad source ingestion. This module
adds stricter serverless publication policy at the final automatic channel
surface: only verifiably fresh jobs are eligible and each cron cycle has a
small burst ceiling.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Optional

import channel_bot as core


_ORIGINAL_IS_SUITABLE_JOB = core.is_suitable_job
_ORIGINAL_EMERGENCY_MAX_POSTS = core.Config.EMERGENCY_MAX_POSTS_PER_CYCLE
_DEFAULT_MAX_AGE_DAYS = 7
_DEFAULT_MAX_POSTS_PER_CYCLE = 3
_MAX_REASONABLE_FUTURE_SKEW = timedelta(hours=6)

_stats = {
    "stale_excluded": 0,
    "unknown_date_excluded": 0,
    "future_date_excluded": 0,
}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def parse_source_datetime(job: Dict) -> Optional[datetime]:
    """Parse heterogeneous source timestamps into an aware UTC datetime."""
    raw = (
        job.get("published")
        or job.get("created")
        or job.get("publication_date")
        or job.get("date_published")
        or job.get("posted_at")
        or job.get("posted_date")
    )
    if raw is None or raw == "":
        return None

    if isinstance(raw, (int, float)):
        value = float(raw)
        if value > 10_000_000_000:  # milliseconds
            value /= 1000.0
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(raw).strip()
    if not text:
        return None

    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        try:
            value = float(text)
            if value > 10_000_000_000:
                value /= 1000.0
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_fresh_for_serverless_publication(job: Dict, *, now: Optional[datetime] = None) -> bool:
    """Require a trustworthy source date within the configured freshness window."""
    published = parse_source_datetime(job)
    if published is None:
        _stats["unknown_date_excluded"] += 1
        return False

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)

    if published - current > _MAX_REASONABLE_FUTURE_SKEW:
        _stats["future_date_excluded"] += 1
        return False

    max_age_days = _env_int(
        "SERVERLESS_MAX_JOB_AGE_DAYS",
        _DEFAULT_MAX_AGE_DAYS,
        minimum=1,
        maximum=30,
    )
    if current - published > timedelta(days=max_age_days):
        _stats["stale_excluded"] += 1
        return False
    return True


def _serverless_is_suitable_job(job: Dict) -> bool:
    if not _ORIGINAL_IS_SUITABLE_JOB(job):
        return False
    return is_fresh_for_serverless_publication(job)


def reset_publication_policy_stats() -> None:
    for key in _stats:
        _stats[key] = 0


def publication_policy_snapshot() -> dict:
    return {
        **_stats,
        "max_job_age_days": _env_int(
            "SERVERLESS_MAX_JOB_AGE_DAYS",
            _DEFAULT_MAX_AGE_DAYS,
            minimum=1,
            maximum=30,
        ),
        "max_posts_per_cycle": _env_int(
            "SERVERLESS_MAX_POSTS_PER_CYCLE",
            _DEFAULT_MAX_POSTS_PER_CYCLE,
            minimum=1,
            maximum=20,
        ),
        "unknown_dates_allowed": False,
    }


def install_serverless_publication_policy() -> bool:
    """Install production-only policy without changing local/Render behavior."""
    if not str(os.getenv("VERCEL", "") or "").strip():
        core.is_suitable_job = _ORIGINAL_IS_SUITABLE_JOB
        core.Config.EMERGENCY_MAX_POSTS_PER_CYCLE = _ORIGINAL_EMERGENCY_MAX_POSTS
        return False

    core.is_suitable_job = _serverless_is_suitable_job
    core.Config.EMERGENCY_MAX_POSTS_PER_CYCLE = _env_int(
        "SERVERLESS_MAX_POSTS_PER_CYCLE",
        _DEFAULT_MAX_POSTS_PER_CYCLE,
        minimum=1,
        maximum=20,
    )
    return True
