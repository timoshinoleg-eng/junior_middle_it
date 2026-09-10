"""Authenticated HTTP transport for durable growth state.

Render uses the private ``growth-proxy`` with a dedicated ``X-Growth-Key``.
The Vercel interactive webhook uses a separate ``interactive-growth-proxy`` and
its already-secret Telegram bot token. In webhook mode schema bootstrap DDL is
handled as a local no-op because production migrations own the schema; the
remote endpoint only accepts data/query operations on ``growth_*`` relations.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

import growth_store
from growth_store import GrowthStoreUnavailable, PostgresGrowthStore


_PLACEHOLDER_RE = re.compile(r"%s")
_SCHEMA_BOOTSTRAP_RE = re.compile(
    r"^\s*(?:"
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(?:public\.)?growth_[A-Za-z0-9_]+\b"
    r"|CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+[A-Za-z_][A-Za-z0-9_]*\s+ON\s+(?:public\.)?growth_[A-Za-z0-9_]+\b"
    r")",
    re.IGNORECASE,
)
MAX_REQUEST_BYTES = 40_000
MAX_RESPONSE_BYTES = 2_000_000
MAX_PARAMS = 80
_RENDER_MODE = "render"
_TELEGRAM_MODE = "telegram"


def _safe_proxy_url(value: str, expected_suffix: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.endswith(".supabase.co"):
        return ""
    path = parsed.path.rstrip("/")
    if path != expected_suffix:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _safe_growth_proxy_url(value: str) -> str:
    return _safe_proxy_url(value, "/functions/v1/growth-proxy")


def _safe_interactive_proxy_url(value: str) -> str:
    return _safe_proxy_url(value, "/functions/v1/interactive-growth-proxy")


def _json_value(value: Any) -> Any:
    """Convert psycopg/Python parameter wrappers into JSON-safe values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if hasattr(value, "obj"):
        return _json_value(getattr(value, "obj"))
    return str(value)


def _convert_placeholders(query: str, params: Iterable[Any]) -> tuple[str, list[Any]]:
    values = list(params or ())
    if len(values) > MAX_PARAMS:
        raise GrowthStoreUnavailable("Growth SQL has too many parameters")
    index = 0

    def replace(_match):
        nonlocal index
        index += 1
        return f"${index}"

    converted = _PLACEHOLDER_RE.sub(replace, str(query))
    if index != len(values):
        raise GrowthStoreUnavailable(
            f"Growth SQL placeholder mismatch: query={index}, params={len(values)}"
        )
    return converted, [_json_value(v) for v in values]


def _is_schema_bootstrap(query: str) -> bool:
    """Recognize only idempotent growth schema bootstrap issued by constructors."""
    normalized = str(query or "").strip().rstrip(";").strip()
    if not _SCHEMA_BOOTSTRAP_RE.match(normalized):
        return False
    if ";" in normalized or "--" in normalized or "/*" in normalized:
        return False
    return True


class RemoteGrowthCursor:
    def __init__(self, connection: "RemoteGrowthConnection"):
        self.connection = connection
        self._rows: list[list[Any]] = []
        self._index = 0
        self.rowcount = -1
        self.columns: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query: str, params=()):
        converted, values = _convert_placeholders(query, params or ())
        if self.connection.auth_mode == _TELEGRAM_MODE and _is_schema_bootstrap(converted):
            # Production schema is migration-owned. Constructors still issue
            # CREATE IF NOT EXISTS for legacy direct-Postgres compatibility;
            # never forward DDL through a bot-token endpoint.
            self._rows = []
            self._index = 0
            self.rowcount = 0
            self.columns = []
            return self

        payload = json.dumps(
            {"query": converted, "params": values},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            raise GrowthStoreUnavailable("Growth SQL request is too large")
        request = Request(
            self.connection.url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "junior-middle-it-growth/3.0",
                self.connection.auth_header: self.connection.credential,
            },
        )
        try:
            with urlopen(request, timeout=self.connection.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            try:
                detail = json.loads(
                    exc.read(16_384).decode("utf-8", "replace")
                ).get("error", "http error")
            except Exception:
                detail = "http error"
            raise GrowthStoreUnavailable(
                f"Growth proxy rejected query ({exc.code}): {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise GrowthStoreUnavailable(
                f"Growth proxy unavailable: {type(exc).__name__}"
            ) from exc

        if len(body) > MAX_RESPONSE_BYTES:
            raise GrowthStoreUnavailable("Growth proxy response is too large")
        try:
            result = json.loads(body.decode("utf-8"))
            rows = result.get("rows") or []
            if not isinstance(rows, list):
                raise ValueError("rows must be a list")
            self._rows = [list(row) for row in rows]
            self.columns = [str(name) for name in (result.get("columns") or [])]
            self.rowcount = int(result.get("rowcount", len(self._rows)))
            self._index = 0
        except Exception as exc:
            raise GrowthStoreUnavailable("Invalid growth proxy response") from exc
        return self

    def fetchone(self):
        if self._index >= len(self._rows):
            return None
        row = tuple(self._rows[self._index])
        self._index += 1
        return row

    def fetchall(self):
        rows = [tuple(row) for row in self._rows[self._index :]]
        self._index = len(self._rows)
        return rows


class RemoteGrowthConnection:
    def __init__(
        self,
        url: str,
        credential: str,
        timeout: float = 15.0,
        *,
        auth_mode: str = _RENDER_MODE,
    ):
        mode = str(auth_mode or _RENDER_MODE).strip().lower()
        if mode not in {_RENDER_MODE, _TELEGRAM_MODE}:
            raise GrowthStoreUnavailable("Unsupported growth proxy auth mode")
        safe_url = (
            _safe_interactive_proxy_url(url)
            if mode == _TELEGRAM_MODE
            else _safe_growth_proxy_url(url)
        )
        if not safe_url:
            expected = "interactive-growth-proxy" if mode == _TELEGRAM_MODE else "growth-proxy"
            raise GrowthStoreUnavailable(
                f"Growth proxy must be the HTTPS Supabase {expected} endpoint"
            )
        secret = str(credential or "").strip()
        if not secret or len(secret) > 256:
            raise GrowthStoreUnavailable("Growth proxy credential is missing or invalid")
        self.url = safe_url
        self.credential = secret
        self.key = secret  # backward-compatible alias for Render callers/tests
        self.timeout = float(timeout)
        self.auth_mode = mode
        self.auth_header = (
            "X-Telegram-Bot-Token" if mode == _TELEGRAM_MODE else "X-Growth-Key"
        )
        self.closed = False

    def cursor(self):
        if self.closed:
            raise GrowthStoreUnavailable("Growth proxy connection is closed")
        return RemoteGrowthCursor(self)

    def close(self):
        self.closed = True


class HttpPostgresGrowthStore(PostgresGrowthStore):
    """PostgresGrowthStore using one of the authenticated Edge transports."""

    def _connect(self) -> None:
        mode = (os.getenv("GROWTH_HTTP_AUTH_MODE") or _RENDER_MODE).strip().lower()
        credential = (
            (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
            if mode == _TELEGRAM_MODE
            else (os.getenv("GROWTH_HTTP_KEY") or "").strip()
        )
        self.conn = RemoteGrowthConnection(self.dsn, credential, auth_mode=mode)


def install() -> bool:
    """Patch the store only for the exact endpoint matching the selected auth mode."""
    endpoint = (os.getenv("GROWTH_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    mode = (os.getenv("GROWTH_HTTP_AUTH_MODE") or _RENDER_MODE).strip().lower()
    valid = (
        bool(_safe_interactive_proxy_url(endpoint))
        if mode == _TELEGRAM_MODE
        else bool(_safe_growth_proxy_url(endpoint))
    )
    if not valid:
        return False
    growth_store.PostgresGrowthStore = HttpPostgresGrowthStore
    return True


HTTP_GROWTH_PATCHED = install()
