"""Authenticated HTTP transport for the durable growth store on Render.

Render may use the Supabase ``growth-proxy`` Edge Function instead of receiving
raw PostgreSQL credentials. The private bridge key exists only in Render. This
adapter preserves the cursor API expected by the P1-P7 runtime while pinning the
remote endpoint to HTTPS Supabase hosts and bounding request/response sizes.
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
MAX_REQUEST_BYTES = 40_000
MAX_RESPONSE_BYTES = 2_000_000
MAX_PARAMS = 80


def _safe_growth_proxy_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.endswith(".supabase.co"):
        return ""
    expected_suffix = "/functions/v1/growth-proxy"
    path = parsed.path.rstrip("/")
    if path != expected_suffix:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


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
                "X-Growth-Key": self.connection.key,
                "User-Agent": "junior-middle-it-render/2.0",
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
    def __init__(self, url: str, key: str, timeout: float = 15.0):
        safe_url = _safe_growth_proxy_url(url)
        if not safe_url:
            raise GrowthStoreUnavailable(
                "Growth proxy must be the HTTPS Supabase growth-proxy endpoint"
            )
        self.url = safe_url
        self.key = str(key or "").strip()
        self.timeout = float(timeout)
        self.closed = False
        if not self.key:
            raise GrowthStoreUnavailable("GROWTH_HTTP_KEY is missing")
        if len(self.key) > 256:
            raise GrowthStoreUnavailable("GROWTH_HTTP_KEY is invalid")

    def cursor(self):
        if self.closed:
            raise GrowthStoreUnavailable("Growth proxy connection is closed")
        return RemoteGrowthCursor(self)

    def close(self):
        self.closed = True


class HttpPostgresGrowthStore(PostgresGrowthStore):
    """PostgresGrowthStore using the authenticated Edge transport."""

    def _connect(self) -> None:
        key = (os.getenv("GROWTH_HTTP_KEY") or "").strip()
        self.conn = RemoteGrowthConnection(self.dsn, key)


def install() -> bool:
    """Patch the later runtime layers only for the exact HTTPS Edge endpoint."""
    endpoint = (os.getenv("GROWTH_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not _safe_growth_proxy_url(endpoint):
        return False
    growth_store.PostgresGrowthStore = HttpPostgresGrowthStore
    return True


HTTP_GROWTH_PATCHED = install()
