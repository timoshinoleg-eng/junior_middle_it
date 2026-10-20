"""Verification of the Telegram Mini App ``initData`` signature.

The Mini App hands the server a string that Telegram signed with the bot
token. Nothing else in the request may be trusted: the user id, the freshness
and the whole request identity come from this signature.

The algorithm is the one Telegram documents for Mini Apps:

* ``secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)``
* ``data_check_string`` - the sorted ``key=value`` pairs, joined by newlines,
  without the ``hash`` field
* ``hash = HMAC_SHA256(key=secret_key, msg=data_check_string)``

The module is pure: no network, no database, no Telegram imports.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Dict, Optional
from urllib.parse import parse_qsl

MAX_INIT_DATA_CHARS = 4_000
MAX_AGE_SECONDS = 24 * 60 * 60
CLOCK_SKEW_SECONDS = 60

WEB_APP_DATA_KEY = b"WebAppData"


class InitDataError(ValueError):
    """The signature is missing, wrong or too old."""


def parse_init_data(raw: str) -> Dict[str, str]:
    """Parse the query-string encoded ``initData`` into a mapping."""
    value = str(raw or "").strip()
    if not value:
        raise InitDataError("empty_init_data")
    if len(value) > MAX_INIT_DATA_CHARS:
        raise InitDataError("init_data_too_long")
    try:
        pairs = parse_qsl(value, keep_blank_values=True, strict_parsing=False)
    except ValueError as exc:
        raise InitDataError("malformed_init_data") from exc
    if not pairs:
        raise InitDataError("malformed_init_data")
    return {str(key): str(item) for key, item in pairs}


def compute_secret_key(bot_token: str) -> bytes:
    """Key derived from the bot token, as Telegram documents it."""
    token = str(bot_token or "")
    if not token:
        raise InitDataError("missing_bot_token")
    return hmac.new(WEB_APP_DATA_KEY, token.encode("utf-8"), hashlib.sha256).digest()


def data_check_string(params: Dict[str, str]) -> str:
    """The sorted, newline joined payload the signature covers."""
    return "\n".join(
        f"{key}={params[key]}" for key in sorted(params) if key != "hash"
    )


def verify_init_data(
    raw: str,
    bot_token: str,
    *,
    max_age_seconds: int = MAX_AGE_SECONDS,
    now: Optional[float] = None,
) -> Dict[str, str]:
    """Verify the signature and the freshness of ``initData``.

    Returns the signed parameters. Raises :class:`InitDataError` when the
    signature does not match, is missing, or is older than the allowed window.
    """
    params = parse_init_data(raw)
    received = str(params.get("hash") or "")
    if not received:
        raise InitDataError("missing_hash")
    secret = compute_secret_key(bot_token)
    expected = hmac.new(
        secret, data_check_string(params).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise InitDataError("bad_signature")

    raw_auth_date = str(params.get("auth_date") or "")
    try:
        auth_date = int(raw_auth_date)
    except (TypeError, ValueError) as exc:
        raise InitDataError("missing_auth_date") from exc
    if auth_date <= 0:
        raise InitDataError("missing_auth_date")

    current = int(now if now is not None else time.time())
    if auth_date > current + CLOCK_SKEW_SECONDS:
        raise InitDataError("auth_date_in_future")
    if max_age_seconds and current - auth_date > int(max_age_seconds):
        raise InitDataError("stale_init_data")
    return params


def extract_user(params: Dict[str, str]) -> Dict:
    """The signed user payload of ``initData``."""
    raw = str((params or {}).get("user") or "")
    if not raw:
        raise InitDataError("missing_user")
    try:
        user = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise InitDataError("malformed_user") from exc
    if not isinstance(user, dict):
        raise InitDataError("malformed_user")
    try:
        user_id = int(user.get("id"))
    except (TypeError, ValueError) as exc:
        raise InitDataError("malformed_user") from exc
    if user_id <= 0:
        raise InitDataError("malformed_user")
    return {
        "id": user_id,
        "first_name": str(user.get("first_name") or ""),
        "username": str(user.get("username") or ""),
    }


def verify_user(
    raw: str,
    bot_token: str,
    *,
    max_age_seconds: int = MAX_AGE_SECONDS,
    now: Optional[float] = None,
) -> Dict:
    """Verify ``initData`` and return the signed user."""
    params = verify_init_data(
        raw, bot_token, max_age_seconds=max_age_seconds, now=now
    )
    return extract_user(params)
