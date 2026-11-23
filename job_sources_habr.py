"""Public Habr Career RSS adapter (opt-in, no credentials required).

Reads only explicit per-vacancy remote and grade signals. RSS query parameters
are search hints, NOT sufficient evidence for a public Telegram post.
"""
from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

HABR_RSS_URL = "https://career.habr.com/vacancies/rss"
# Habr Career qid values: intern=1, junior=3, middle=4.
HABR_GRADES = ((1, "intern"), (3, "junior"), (4, "middle"))
_HEADERS = {
    "User-Agent": "JuniorMiddleITBot/6.6 (+https://github.com/timoshinoleg-eng/junior_middle_it)",
    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.5",
}
_REMOTE_RE = re.compile(
    r"можно\s+удал[её]нно|удал[её]нн(?:ая|ую|о)\s+работа|"
    r"полностью\s+удал[её]нно|дистанционно|(?<!\w)remote(?!\w)",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"^Требуется\s+«(.+)»(?:\s*\(.*\))?$", re.IGNORECASE)
_COMPANY_RE = re.compile(r"Компания\s+«(.+)»\s+ищет", re.IGNORECASE)
_SALARY_RE = re.compile(
    r"(?<!\w)(?:от|до)\s+\d[\d \u00a0]*\s*₽"
    r"(?:\s+до\s+\d[\d \u00a0]*\s*₽)?",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"(?<!\w)#([^\s,;:!?<>]+)")


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(item: ET.Element, name: str) -> str:
    for child in item:
        if _localname(child.tag) == name.lower():
            return (child.text or "").strip()
    return ""


def _plain(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


def _canonical_url(value: str) -> str:
    try:
        p = urlsplit(value.strip())
    except ValueError:
        return ""
    if (p.scheme != "https" or p.hostname != "career.habr.com"
            or not re.fullmatch(r"/vacancies/\d+", p.path)):
        return ""
    # Drop tracking/query/fragment, preserving the stable original vacancy URL.
    return f"https://career.habr.com{p.path}"


def parse_habr_rss(content: bytes | str, expected_grade: str, max_items: int = 25) -> list[dict]:
    """Normalize one grade feed; do not trust qid alone as grade evidence."""
    if expected_grade not in {"intern", "junior", "middle"}:
        raise ValueError("unsupported Habr grade")
    if len(content) > 2_000_000:
        raise ValueError("Habr RSS payload too large")

    root = ET.fromstring(content)
    jobs: list[dict] = []
    seen_urls: set[str] = set()
    for item in root.iter():
        if _localname(item.tag) != "item":
            continue
        raw_title = _plain(_child_text(item, "title"))
        description = _plain(_child_text(item, "description"))
        tags = [
            match.group(1).strip(".,;!?)]}")
            for match in _TAG_RE.finditer(description)
        ]
        grades = {tag.lower() for tag in tags}
        if expected_grade not in grades or grades.intersection({"senior", "lead", "principal", "staff"}):
            continue
        # A filtered RSS URL does not independently prove a job is remote.
        if not _REMOTE_RE.search(description):
            continue

        url = _canonical_url(_child_text(item, "link") or _child_text(item, "guid"))
        if not url or url in seen_urls:
            continue
        match = _TITLE_RE.fullmatch(raw_title)
        title = (match.group(1) if match else raw_title).strip()
        author = _plain(
            _child_text(item, "creator")
            or _child_text(item, "companyName")
            or _child_text(item, "author")
        )
        if not author:
            company_match = _COMPANY_RE.search(description)
            author = company_match.group(1).strip() if company_match else ""
        if not title or not author:
            continue

        salary_match = _SALARY_RE.search(description)
        salary = " ".join(salary_match.group(0).split()) if salary_match else "Не указана"
        seen_urls.add(url)
        jobs.append({
            "title": title[:220],
            "company": author[:160],
            "description": description[:6000],
            "url": url,
            "salary": salary,
            # Explicit remote phrase above supports this, but NOT worldwide eligibility.
            "location": "Удалённая работа (география не подтверждена)",
            "published": _child_text(item, "pubDate"),
            "employment_type": "",
            "source": "RSS:Habr Career",
            "tags": tags[:12],
            "api_level": expected_grade,
        })
        if len(jobs) >= max(1, min(50, max_items)):
            break
    return jobs


def fetch_habr_career(max_items_per_feed: int = 25) -> list[dict]:
    """Fetch three public, read-only RSS feeds; fail one grade independently."""
    result: list[dict] = []
    seen: set[str] = set()
    for qid, grade in HABR_GRADES:
        try:
            response = requests.get(
                HABR_RSS_URL,
                params={"remote": "true", "qid": qid},
                headers=_HEADERS,
                timeout=15,
            )
            response.raise_for_status()
            jobs = parse_habr_rss(response.content, grade, max_items_per_feed)
            for job in jobs:
                if job["url"] not in seen:
                    result.append(job)
                    seen.add(job["url"])
            logger.info("Habr RSS grade=%s normalized=%d", grade, len(jobs))
        except (requests.RequestException, ET.ParseError, ValueError) as exc:
            logger.warning("Habr RSS grade=%s failed: %s", grade, type(exc).__name__)
    return result
