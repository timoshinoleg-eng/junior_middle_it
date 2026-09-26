"""Production publication guardrails for the Vercel vacancy collector.

The core collector intentionally supports broad source ingestion. This module
adds stricter serverless publication policy at the final automatic channel
surface: only verifiably fresh jobs are eligible and each cron cycle has a
small burst ceiling.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Optional

import channel_bot as core
from growth_utils import SOURCE_DATE_FIELDS


_ORIGINAL_IS_SUITABLE_JOB = core.is_suitable_job
_ORIGINAL_EMERGENCY_MAX_POSTS = core.Config.EMERGENCY_MAX_POSTS_PER_CYCLE
_DEFAULT_MAX_AGE_DAYS = 7
_DEFAULT_MAX_POSTS_PER_CYCLE = 8
_MAX_REASONABLE_FUTURE_SKEW = timedelta(hours=6)

_stats = {
    "stale_excluded": 0,
    "unknown_date_excluded": 0,
    "future_date_excluded": 0,
}
_source_stats: Dict[str, Dict[str, int]] = {}
_SOURCE_EVENTS = (
    "base_rejected",
    "freshness_checked",
    "freshness_passed",
    "stale_excluded",
    "unknown_date_excluded",
    "future_date_excluded",
)


def _source_name(job: Dict) -> str:
    return str(job.get("source") or "unknown").strip() or "unknown"


def _source_bucket(job: Dict) -> Dict[str, int]:
    source = _source_name(job)
    bucket = _source_stats.setdefault(
        source,
        {event: 0 for event in _SOURCE_EVENTS},
    )
    return bucket


def _record_source_event(job: Dict, event: str) -> None:
    bucket = _source_bucket(job)
    bucket["max_job_age_days"] = _source_max_age_days(job)
    bucket[event] = bucket.get(event, 0) + 1


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _bounded_days(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, 30))


def _default_max_age_days() -> int:
    return _env_int(
        "SERVERLESS_MAX_JOB_AGE_DAYS",
        _DEFAULT_MAX_AGE_DAYS,
        minimum=1,
        maximum=30,
    )


def _source_max_age_days(job: Dict) -> int:
    default = _default_max_age_days()
    raw = str(os.getenv("SERVERLESS_SOURCE_MAX_AGE_DAYS_JSON", "") or "").strip()
    if not raw:
        return default
    try:
        overrides = json.loads(raw)
    except (TypeError, ValueError):
        return default
    if not isinstance(overrides, dict):
        return default
    source = _source_name(job)
    family = source.split(":", 1)[0]
    for key in (source, family):
        if key in overrides:
            return _bounded_days(overrides[key], default)
    return default


def _parse_source_value(raw: object) -> Optional[datetime]:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        if not math.isfinite(value):
            return None
        if value > 10_000_000_000:
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
            if not math.isfinite(value):
                return None
            if value > 10_000_000_000:
                value /= 1000.0
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    if re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", text):
        # Dotted European dates: devitjobs.uk publishes <pubdate> as DD.MM.YYYY,
        # which used to fall through as an unknown date and hide the source.
        try:
            return datetime.strptime(text, "%d.%m.%Y").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_source_datetime_with_field(job: Dict) -> tuple[Optional[datetime], str]:
    for field in SOURCE_DATE_FIELDS:
        parsed = _parse_source_value(job.get(field))
        if parsed is not None:
            return parsed, field
    return None, ""


def parse_source_datetime(job: Dict) -> Optional[datetime]:
    """Parse heterogeneous source timestamps into an aware UTC datetime."""
    return _parse_source_datetime_with_field(job)[0]


def is_fresh_for_serverless_publication(job: Dict, *, now: Optional[datetime] = None) -> bool:
    """Require a trustworthy source date within the configured freshness window."""
    _record_source_event(job, "freshness_checked")
    published, source_field = _parse_source_datetime_with_field(job)
    if published is None:
        _stats["unknown_date_excluded"] += 1
        _record_source_event(job, "unknown_date_excluded")
        return False

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)

    if published - current > _MAX_REASONABLE_FUTURE_SKEW:
        _stats["future_date_excluded"] += 1
        _record_source_event(job, "future_date_excluded")
        return False

    if current - published > timedelta(days=_source_max_age_days(job)):
        _stats["stale_excluded"] += 1
        _record_source_event(job, "stale_excluded")
        return False

    job["source_published_at"] = published.isoformat()
    job["source_date_field"] = source_field
    _record_source_event(job, "freshness_passed")
    return True


def _serverless_is_suitable_job(job: Dict) -> bool:
    if not _ORIGINAL_IS_SUITABLE_JOB(job):
        _record_source_event(job, "base_rejected")
        return False
    return is_fresh_for_serverless_publication(job)


def reset_publication_policy_stats() -> None:
    for key in _stats:
        _stats[key] = 0
    _source_stats.clear()


def publication_policy_snapshot() -> dict:
    by_source = {
        source: dict(_source_stats[source])
        for source in sorted(_source_stats)
    }
    return {
        **_stats,
        "max_job_age_days": _default_max_age_days(),
        "max_posts_per_cycle": _env_int(
            "SERVERLESS_MAX_POSTS_PER_CYCLE",
            _DEFAULT_MAX_POSTS_PER_CYCLE,
            minimum=1,
            maximum=20,
        ),
        "source_max_age_days_json": os.getenv("SERVERLESS_SOURCE_MAX_AGE_DAYS_JSON", ""),
        "by_source": by_source,
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
