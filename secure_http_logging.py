"""Harden HTTP/source logging before Telegram and job-source clients import.

Two common secret-leak surfaces are covered here:
1. httpx INFO records may contain a full Telegram Bot API URL whose path embeds
   the bot credential;
2. requests exceptions may stringify their request URL, including query-string
   API credentials used by external job sources.

Expected source conditions are also normalized so production error telemetry is
reserved for actionable failures. A company moving away from a public ATS often
produces a 404/410 for its old board slug; that is a normal per-board skip, not a
collector failure.
"""
from __future__ import annotations

import logging
import re


SENSITIVE_HTTP_LOGGERS = (
    "httpx",
    "httpcore",
    "telegram.request",
    "telegram.ext.ExtBot",
)

# Cover common URL/query credential names plus Telegram's credential-in-path
# convention. Values are deliberately replaced, never partially preserved.
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|api[_-]?key|access[_-]?token|secret|key)=)[^&\s]+"
)
_TELEGRAM_BOT_PATH_RE = re.compile(r"(?i)(/bot)\d{5,20}:[A-Za-z0-9_-]{20,200}")
_BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+")

_EXPECTED_ATS_MISS_RE = re.compile(
    r"(?i)\b(?:Greenhouse|Lever|Ashby)\b.*\berror:\s*(?:404|410)\s+Client Error\b"
)
_OPTIONAL_SOURCE_BLOCK_RE = re.compile(
    r"(?i)\bDevITJobs\s+error:\s*403\s+Client Error\b"
)


def redact_credentials(value: object) -> str:
    text = str(value)
    text = _QUERY_SECRET_RE.sub(r"\1<redacted>", text)
    text = _TELEGRAM_BOT_PATH_RE.sub(r"\1<redacted>", text)
    text = _BEARER_RE.sub(r"\1<redacted>", text)
    return text


class CredentialRedactionFilter(logging.Filter):
    """Sanitize secrets and normalize expected source log severity."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
            redacted = redact_credentials(rendered)
            if redacted != rendered:
                record.msg = redacted
                record.args = ()

            if record.levelno >= logging.ERROR and _EXPECTED_ATS_MISS_RE.search(redacted):
                record.levelno = logging.INFO
                record.levelname = "INFO"
            elif record.levelno >= logging.ERROR and _OPTIONAL_SOURCE_BLOCK_RE.search(redacted):
                record.levelno = logging.WARNING
                record.levelname = "WARNING"
        except Exception:
            # Logging hardening must never break the collector itself.
            pass
        return True


_REDACTION_FILTER = CredentialRedactionFilter()


def configure_sensitive_http_logging() -> None:
    """Suppress credential-bearing request logs and sanitize source errors."""
    for name in SENSITIVE_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # These loggers are created later during module imports; getLogger() is safe
    # and attaches the filter before their handlers start emitting records.
    for name in ("channel_bot", "job_sources_extra", "telegram_job_parser"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, CredentialRedactionFilter) for f in logger.filters):
            logger.addFilter(_REDACTION_FILTER)
