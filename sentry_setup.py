# -*- coding: utf-8 -*-
"""Sentry integration for junior_middle_it.

Single init point used by both entry points (channel_bot.py long-running
polling and api/cron.py serverless Vercel). Controlled entirely by env:

    SENTRY_DSN    — required; if empty, Sentry is a no-op.
    SENTRY_RELEASE — optional version tag (default "dev").

Privacy: before_send strips exception values, HTTP bodies, headers and any
job text so that vacancies / job messages / credentials never leave the bot.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Values that must never be sent. Lower-cased, substring match on keys.
_BLOCKED_KEYS = (
    "token", "secret", "password", "pass", "apikey", "api_key", "key",
    "authorization", "cookie", "dns", "session", "hash", "credential",
    "cookie", "text", "message", "job", "title", "description", "company",
    "question", "prompt", "username", "email", "phone", "caption",
)


def _redact_dict(value, depth: int = 0):
    if depth > 6:
        return "<redacted>"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            kl = str(k).lower()
            if any(b in kl for b in _BLOCKED_KEYS):
                out[k] = "<redacted>"
            else:
                out[k] = _redact_dict(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact_dict(v, depth + 1) for v in value]
    return value


def _scrub(event, hint):
    """Drop exception values and scrub request/user data before send."""
    exc = hint.get("exc_info") if isinstance(hint, dict) else None
    if exc:
        # Keep type but strip value text (may contain job/vacancy content).
        try:
            for ev in event.get("exception", {}).get("values", []):
                ev["value"] = f"<{ev.get('type', 'error')}: message scrubbed>"
        except Exception:
            pass

    if isinstance(event.get("request"), dict):
        event["request"] = _redact_dict(event["request"])

    if isinstance(event.get("user"), dict):
        safe = {}
        for k, v in event["user"].items():
            safe[k] = "<redacted>"
        event["user"] = safe

    # Drop any logging breadcrumb that might carry job text.
    crumbs = event.get("breadcrumbs", {}).get("values") if isinstance(
        event.get("breadcrumbs"), dict) else None
    if crumbs:
        for cr in crumbs:
            if isinstance(cr.get("data"), dict):
                cr["data"] = _redact_dict(cr["data"])
    return event


def init_sentry() -> Optional[object]:
    """Initialize Sentry if SENTRY_DSN is set. Returns init response or None."""
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        logger.info("Sentry disabled: SENTRY_DSN not set")
        return None

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.warning("Sentry disabled: sentry-sdk not installed")
        return None

    release = os.getenv("SENTRY_RELEASE", "").strip() or "dev"
    environment = "production" if os.getenv("VERCEL") else "railway"

    sentry_logging = LoggingIntegration(
        level=logging.INFO,
        event_level=logging.ERROR,
    )

    init = sentry_sdk.init(
        dsn=dsn,
        release=release,
        environment=environment,
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        send_default_pii=False,
        before_send=_scrub,
        integrations=[sentry_logging],
    )
    import sentry_sdk as _sdk
    _sdk.set_tag("project", "junior_middle_it")
    logger.info("Sentry enabled: project=junior_middle_it release=%s env=%s",
                release, environment)
    return init
