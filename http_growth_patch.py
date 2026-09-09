"""HTTP transport for the durable growth store.

Render can use a Supabase Edge Function as a narrow SQL bridge when a raw
PostgreSQL password is intentionally not exported from Supabase.  The bridge
keeps the existing PostgresGrowthStore API intact, including legacy runtime
layers that use ``_ensure_conn().cursor()`` directly.

This module is imported only by the persistent Render entrypoint.  Vercel never
receives the private bridge key.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import growth_store
from growth_store import GrowthStoreUnavailable, PostgresGrowthStore


_PLACEHOLDER_RE = re.compile(r"%s")


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
    # psycopg Json/Jsonb wrappers expose the wrapped Python object as ``obj``.
    if hasattr(value, "obj"):
        return _json_value(getattr(value, "obj"))
    return str(value)


def _convert_placeholders(query: str, params: Iterable[Any]) -> tuple[str, list[Any]]:
    values = list(params or ())
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
        request = Request(
            self.connection.url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Growth-Key": self.connection.key,
                "User-Agent": "junior-middle-it-render/1.0",
            },
        )
        try:
            with urlopen(request, timeout=self.connection.timeout) as response:
                body = response.read()
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace")).get("error", "http error")
            except Exception:
                detail = "http error"
            raise GrowthStoreUnavailable(
                f"Growth proxy rejected query ({exc.code}): {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise GrowthStoreUnavailable(
                f"Growth proxy unavailable: {type(exc).__name__}"
            ) from exc

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
        self.url = str(url or "").rstrip("/")
        self.key = str(key or "")
        self.timeout = float(timeout)
        self.closed = False
        if not self.url.startswith("https://"):
            raise GrowthStoreUnavailable("Growth proxy must use HTTPS")
        if not self.key:
            raise GrowthStoreUnavailable("GROWTH_HTTP_KEY is missing")

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
    """Patch the class imported by later P1-P7 runtime layers when URL is HTTPS."""
    endpoint = (os.getenv("GROWTH_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not endpoint.startswith("https://"):
        return False
    growth_store.PostgresGrowthStore = HttpPostgresGrowthStore
    return True


HTTP_GROWTH_PATCHED = install()
