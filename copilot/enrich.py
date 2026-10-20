"""Optional full-description enrichment for a vacancy link.

The cascade is deliberately conservative:

* only public pages are fetched, never private APIs behind a login;
* ``robots.txt`` of the target host is respected;
* no anti-bot, captcha or paywall is bypassed;
* the caller always keeps the original short text when enrichment fails.

Network calls run inside a worker thread so the Telegram event loop is never
blocked.
"""
from __future__ import annotations

import asyncio
import html as html_module
import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

USER_AGENT = "junior-middle-it-bot/1.0 (+https://github.com/timoshinoleg-eng/junior_middle_it)"
DEFAULT_TIMEOUT = 8.0
ROBOTS_TIMEOUT = 4.0
MAX_HTML_BYTES = 2_000_000
MAX_TEXT_CHARS = 20_000

SOURCE_ATS_JSON = "ats_json"
SOURCE_HTML = "html"
SOURCE_TEXT_PROXY = "text_proxy"

_ATS_ENDPOINTS: Tuple[Tuple[re.Pattern, str], ...] = (
    (
        re.compile(r"boards\.greenhouse\.io/([^/]+)/jobs/(\d+)"),
        "https://boards-api.greenhouse.io/v1/boards/{0}/jobs/{1}",
    ),
    (
        re.compile(r"jobs\.lever\.co/([^/]+)/([^/?#)]+)"),
        "https://api.lever.co/v0/postings/{0}/{1}",
    ),
    (
        re.compile(r"jobs\.ashbyhq\.com/([^/]+)/([^/?#)]+)"),
        "https://api.ashbyhq.com/posting-api/job-board/{0}/{1}",
    ),
)

_SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")

_robots_cache: Dict[str, Tuple[str, ...]] = {}


@dataclass(frozen=True)
class Enrichment:
    """Enriched vacancy description."""

    url: str
    text: str
    source: str


def _strip_html(raw: str) -> str:
    value = _SCRIPT_RE.sub(" ", str(raw or ""))
    value = _TAG_RE.sub(" ", value)
    value = html_module.unescape(value)
    value = _WS_RE.sub(" ", value)
    value = re.sub(r"\s*\n\s*", "\n", value)
    value = _MULTI_NEWLINE_RE.sub("\n\n", value)
    return value.strip()


def extract_text_from_html(raw_html: str) -> str:
    """Extract readable text from HTML, using trafilatura when it is installed."""
    html = str(raw_html or "")
    if not html:
        return ""
    try:  # pragma: no cover - optional dependency
        import trafilatura

        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        if extracted and extracted.strip():
            return extracted.strip()[:MAX_TEXT_CHARS]
    except Exception as exc:  # pragma: no cover - optional dependency
        logger.debug("trafilatura extraction unavailable: %s", type(exc).__name__)
    return _strip_html(html)[:MAX_TEXT_CHARS]


def _fetch_bytes(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en;q=0.9,ru;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_HTML_BYTES + 1)[:MAX_HTML_BYTES]


def _robots_disallow(url: str, timeout: float = ROBOTS_TIMEOUT) -> Tuple[str, ...]:
    """Return the ``Disallow`` prefixes that apply to the given URL."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ("/",)
    host = f"{parts.scheme}://{parts.netloc}"
    if host not in _robots_cache:
        rules: list = []
        try:
            raw = _fetch_bytes(f"{host}/robots.txt", timeout).decode("utf-8", "replace")
        except Exception:
            raw = ""
        applies = False
        for line in raw.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                applies = value in ("*", "")
                continue
            if key == "disallow" and applies:
                rules.append(value)
        _robots_cache[host] = tuple(rules)
    return _robots_cache[host]


def robots_allows(url: str, timeout: float = ROBOTS_TIMEOUT) -> bool:
    """Check whether the target host allows fetching this path."""
    try:
        rules = _robots_disallow(url, timeout)
    except Exception:
        return False
    path = urlsplit(url).path or "/"
    for rule in rules:
        if rule and path.startswith(rule):
            return False
    return True


def _ats_json_url(url: str) -> Optional[str]:
    for pattern, template in _ATS_ENDPOINTS:
        match = pattern.search(url)
        if match:
            return template.format(*match.groups())
    return None


def _text_from_ats_payload(payload: Dict) -> str:
    chunks: list = []
    for key in ("descriptionPlain", "description", "content", "additional", "descriptionHtml", "body"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            chunks.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    chunks.append(item)
                elif isinstance(item, dict):
                    for sub in ("content", "text", "description"):
                        sub_value = item.get(sub)
                        if isinstance(sub_value, str) and sub_value.strip():
                            chunks.append(sub_value)
    if not chunks:
        return ""
    return _strip_html("\n\n".join(chunks))[:MAX_TEXT_CHARS]


async def enrich_job(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    fetcher: Optional[Callable[[str, float], bytes]] = None,
) -> Optional[Enrichment]:
    """Return the full description of a vacancy page, or ``None`` on failure."""
    target = str(url or "").strip()
    parts = urlsplit(target)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None

    fetch = fetcher or _fetch_bytes

    def _read(link: str) -> bytes:
        return fetch(link, timeout)

    try:
        if not robots_allows(target, min(timeout, ROBOTS_TIMEOUT)):
            logger.info("Skipped enrichment for a robots-disallowed URL")
            return None
    except Exception:
        return None

    ats_url = _ats_json_url(target)
    if ats_url:
        try:
            raw = await asyncio.to_thread(_read, ats_url)
            payload = json.loads(raw.decode("utf-8", "replace"))
            text = _text_from_ats_payload(payload)
            if text:
                return Enrichment(url=target, text=text, source=SOURCE_ATS_JSON)
        except Exception as exc:
            logger.info("ATS JSON enrichment failed: %s", type(exc).__name__)

    try:
        raw = await asyncio.to_thread(_read, target)
        html = raw.decode("utf-8", "replace")
        text = extract_text_from_html(html)
        if text:
            return Enrichment(url=target, text=text, source=SOURCE_HTML)
    except Exception as exc:
        logger.info("Direct enrichment failed: %s", type(exc).__name__)

    proxy_url = f"https://r.jina.ai/{target}"
    try:
        raw = await asyncio.to_thread(_read, proxy_url)
        text = raw.decode("utf-8", "replace").strip()
        if text:
            return Enrichment(url=target, text=text[:MAX_TEXT_CHARS], source=SOURCE_TEXT_PROXY)
    except Exception as exc:
        logger.info("Text proxy enrichment failed: %s", type(exc).__name__)

    return None
