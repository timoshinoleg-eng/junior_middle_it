"""Harden third-party HTTP logging before Telegram clients are imported.

httpx INFO records include the full request URL. Telegram Bot API embeds the bot
credential in that URL, so INFO-level HTTP client logging must never be enabled
in production/serverless runtimes.
"""
from __future__ import annotations

import logging


SENSITIVE_HTTP_LOGGERS = (
    "httpx",
    "httpcore",
    "telegram.request",
    "telegram.ext.ExtBot",
)


def configure_sensitive_http_logging() -> None:
    """Suppress request-level logs that can contain Telegram Bot API secrets."""
    for name in SENSITIVE_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
