"""Fail-closed authority guard for public Telegram channel publication.

Only the canonical production Vercel runtime may send messages to configured
public channels. Non-Vercel runtimes may still send direct messages and handle
interactive bot workflows, but attempts to publish to CHANNEL_ID or configured
multi-track channels are rejected at the Telegram transport boundary.
"""
from __future__ import annotations

import os
from functools import wraps
from typing import Mapping, Optional, Set, Type


class PublicChannelPublicationDenied(RuntimeError):
    """Raised when a non-canonical runtime tries to publish to a public channel."""


_GUARD_MARKER = "__junior_middle_it_publication_authority_guard__"
_CHANNEL_ENV_KEYS = (
    "CHANNEL_ID",
    "CHANNEL_ID_DEV",
    "CHANNEL_ID_QA",
    "CHANNEL_ID_DATA",
    "CHANNEL_ID_DESIGN",
)


def _env(env: Optional[Mapping[str, str]] = None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_canonical_production_publisher(env: Optional[Mapping[str, str]] = None) -> bool:
    """Return True only for the canonical Vercel production runtime."""
    values = _env(env)
    return _truthy(values.get("VERCEL")) and str(values.get("VERCEL_ENV", "")).strip().lower() == "production"


def protected_channel_ids(env: Optional[Mapping[str, str]] = None) -> Set[str]:
    """Resolve every configured public channel target from environment values."""
    values = _env(env)
    protected: Set[str] = set()

    for key in _CHANNEL_ENV_KEYS:
        value = str(values.get(key, "") or "").strip()
        if value:
            protected.add(value)

    routes = str(values.get("CHANNEL_ROUTES", "") or "")
    for route in routes.split(";"):
        route = route.strip()
        if not route or ":" not in route:
            continue
        _, target = route.rsplit(":", 1)
        target = target.strip()
        if target:
            protected.add(target)

    return protected


def should_block_public_channel_send(
    chat_id: object,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """Return True when a non-canonical runtime targets a configured public channel."""
    if is_canonical_production_publisher(env):
        return False
    target = str(chat_id or "").strip()
    return bool(target and target in protected_channel_ids(env))


def install_telegram_channel_publish_guard(bot_class: Optional[Type[object]] = None) -> bool:
    """Patch ``Bot.send_message`` once so non-Vercel public publication fails closed.

    The environment is read at send time, after dotenv/platform configuration has
    been loaded. Direct-message chat IDs that are not configured public channels
    are unaffected.
    """
    if bot_class is None:
        from telegram import Bot as TelegramBot

        bot_class = TelegramBot

    original = getattr(bot_class, "send_message")
    if getattr(original, _GUARD_MARKER, False):
        return False

    @wraps(original)
    async def guarded_send_message(self, *args, **kwargs):
        chat_id = kwargs.get("chat_id")
        if chat_id is None and args:
            chat_id = args[0]
        if should_block_public_channel_send(chat_id):
            raise PublicChannelPublicationDenied(
                "Public Telegram channel publication is Vercel-production-only"
            )
        return await original(self, *args, **kwargs)

    setattr(guarded_send_message, _GUARD_MARKER, True)
    setattr(guarded_send_message, "__wrapped_publication_authority_original__", original)
    setattr(bot_class, "send_message", guarded_send_message)
    return True
