"""Best-effort HTTP cache (ETag / Last-Modified) for ATS JSON endpoints.

v7 Stage 3 (B24): revalidation cuts payload size and parsing time for boards
that support conditional requests. The cache is deliberately simple:
in-memory dict + optional JSON file persistence (Render's disk is ephemeral,
Vercel's FS is read-only — both degrade gracefully to "no cache").
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ETagCache:
    def __init__(self, path: Optional[str] = None, max_entries: int = 500):
        self.path = path
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                self._data = json.load(f)
        except Exception:
            self._data = {}

    def _save(self) -> None:
        if not self.path:
            return
        try:
            items = list(self._data.items())[-self.max_entries:]
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(dict(items), f)
        except Exception as e:
            logger.debug(f"ETagCache persist failed: {e}")

    def conditional_headers(self, url: str) -> Dict[str, str]:
        with self._lock:
            entry = self._data.get(url)
        headers: Dict[str, str] = {}
        if entry:
            if entry.get('etag'):
                headers['If-None-Match'] = entry['etag']
            if entry.get('last_modified'):
                headers['If-Modified-Since'] = entry['last_modified']
        return headers

    def cached_payload(self, url: str) -> Optional[Any]:
        with self._lock:
            entry = self._data.get(url)
        return entry.get('payload') if entry else None

    def store(self, url: str, etag: Optional[str], last_modified: Optional[str],
              payload: Any) -> None:
        if not etag and not last_modified:
            return
        with self._lock:
            self._data[url] = {
                'etag': etag,
                'last_modified': last_modified,
                'payload': payload,
            }
            self._save()


_default_cache: Optional[ETagCache] = None
_cache_init_lock = threading.Lock()


def get_cache() -> Optional[ETagCache]:
    """Process-wide cache; None on read-only serverless filesystems."""
    global _default_cache
    with _cache_init_lock:
        if _default_cache is None:
            if os.getenv('VERCEL'):
                return None
            path = os.getenv('HTTP_CACHE_FILE', 'http_cache.json')
            _default_cache = ETagCache(path=path or None)
    return _default_cache


def fetch_json_cached(url: str, *, headers: Dict[str, str], timeout: float):
    """GET JSON with conditional-request revalidation.

    Returns parsed JSON. Raises requests exceptions on hard errors; a 304 with
    a valid cache entry returns the cached payload.
    """
    import requests

    cache = get_cache()
    req_headers = dict(headers)
    if cache:
        req_headers.update(cache.conditional_headers(url))
    response = requests.get(url, headers=req_headers, timeout=timeout)
    if response.status_code == 304 and cache:
        cached = cache.cached_payload(url)
        if cached is not None:
            response.close()
            return cached
    response.raise_for_status()
    data = response.json()
    if cache:
        cache.store(url, response.headers.get('ETag'),
                    response.headers.get('Last-Modified'), data)
    response.close()
    return data
