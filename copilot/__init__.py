"""Deterministic Copilot core for junior_middle_it.

The package stays free of Telegram, HTTP and database imports so the very same
logic runs in the interactive bot runtime, in serverless endpoints and in
offline unit tests. Anything that needs Telegram lives in
``copilot_webhook_runtime.py``; anything that needs SQL lives in
``copilot/store.py``.
"""
from __future__ import annotations

import os

__all__ = ["__version__", "flag_enabled", "flag_int", "paid_operations_enabled"]

__version__ = "0.1.0"

_TRUTHY = {"1", "true", "yes", "on"}


def flag_enabled(name: str, default: bool = False) -> bool:
    """Read a boolean feature flag from the environment."""
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in _TRUTHY


def flag_int(name: str, default: int, *, minimum: int = 0, maximum: int = 1_000_000) -> int:
    """Read a bounded integer feature flag from the environment."""
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except ValueError:
        return int(default)
    return max(int(minimum), min(int(value), int(maximum)))


def paid_operations_enabled(*, pdf: bool = False) -> bool:
    """Read paid-surface gates on every request, with no process cache."""
    return flag_enabled("COPILOT_ENABLED") and (not pdf or flag_enabled("COPILOT_PDF_ENABLED"))
