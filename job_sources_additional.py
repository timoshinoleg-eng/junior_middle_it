"""Additional official public remote-job feeds with explicit source attribution.

Only documented listing URLs and source fields are used. No browser scraping,
LLM enrichment, fabricated salary, or direct-apply link substitution.
"""
from __future__ import annotations

import logging
import re
from html import unescape
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)
_HEADERS = {"Accept": "application/json", "User-Agent": "JuniorMiddleIT/1.0 (job discovery)"}


def _text(value: object, limit: int = 8000) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _plain_html(value: object, limit: int = 6000) -> str:
    raw = _text(value, 12000)
    raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", " ", raw))).strip()[:limit]


def _source_url(value: object, host: str) -> str:
    raw = _text(value, 2048)
    try:
        parsed = urlsplit(raw)
        if parsed.scheme == "https" and parsed.hostname == host:
            return raw
    except ValueError:
        pass
    return ""


def normalize_remotejobs_org(job: dict) -> dict | None:
    if not isinstance(job, dict):
        return None
    company = job.get("company") if isinstance(job.get("company"), dict) else {}
    url = _source_url(job.get("url"), "remotejobs.org")
    title, company_name = _text(job.get("title"), 220), _text(company.get("name"), 180)
    if not url or not title or not company_name or not _text(job.get("posted_at"), 40):
        return None
    return {
        "title": title,
        "company": company_name,
        "description": _plain_html(job.get("description")),
        "url": url,
        "salary": _text(job.get("salary_text"), 120),
        "location": _text(job.get("location"), 180),
        "published": _text(job.get("posted_at"), 40),
        "employment_type": _text(job.get("type"), 64),
        "source": "RemoteJobs.org",
        "tags": [],
    }


def normalize_remote_landers(job: dict) -> dict | None:
    if not isinstance(job, dict):
        return None
    url = _source_url(job.get("url"), "remotelanders.com")
    title, company = _text(job.get("title"), 220), _text(job.get("company"), 180)
    published = _text(job.get("postedDate"), 40)
    location = _text(job.get("location"), 160)
    # Unknown work eligibility is not interpreted as global eligibility.
    if not url or not title or not company or not published or not location or location.lower() == "not stated":
        return None
    tags = [_text(t, 40) for t in job.get("subtags", []) if isinstance(t, str)][:8]
    category = _text(job.get("category"), 100)
    kind = _text(job.get("type"), 80)
    level = _text(job.get("level"), 60)
    salary = _text(job.get("salary"), 150)
    # This API intentionally provides summary metadata only. Don't fabricate
    # responsibilities or qualifications from an absent full description.
    facts = [f"Remote vacancy: {title} at {company}.",
             f"Location restriction: {location}."]
    for label, value in (("Category", category), ("Specialization", ", ".join(tags)),
                         ("Employment", kind), ("Seniority", level), ("Salary", salary)):
        if value:
            facts.append(f"{label}: {value}.")
    return {
        "title": title,
        "company": company,
        "description": " ".join(facts),
        "url": url,  # Provider requires its own listing URL (not ATS-direct).
        "salary": salary,
        "location": f"Remote — {location}",
        "published": published,
        "employment_type": kind,
        "api_level": level,
        "source": "Remote Landers",
        "tags": tags,
    }


def _fetch_pages(base_url: str, requests_params: list[dict], collection_key: str,
                 normalizer, *, timeout: int = 14) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    for params in requests_params:
        try:
            response = requests.get(base_url, params=params, headers=_HEADERS, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            rows = payload.get(collection_key, []) if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                logger.warning("%s returned a non-list payload", base_url)
                continue
            for item in rows:
                parsed = normalizer(item)
                if not parsed:
                    continue
                if parsed["url"] not in seen:
                    seen.add(parsed["url"])
                    found.append(parsed)
            if not rows:
                break
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.warning("Additional job source fetch failed: %s (%s)", base_url, type(exc).__name__)
            break
    return found


def fetch_remotejobs_org() -> list[dict]:
    """Public JSON, 50 per category; link only to the source's own posting URL."""
    return _fetch_pages(
        "https://remotejobs.org/api/v1/jobs",
        [{"category": c, "limit": 50, "offset": 0}
         for c in ("programming", "data-science", "devops", "quality-assurance")],
        "data", normalize_remotejobs_org,
    )


def fetch_remote_landers() -> list[dict]:
    """Only live ATS-based remote roles; respect attribution and canonical URLs."""
    return _fetch_pages(
        "https://api.remotelanders.workers.dev/api/jobs",
        [{"limit": 100, "page": p} for p in (1, 2)],
        "jobs", normalize_remote_landers,
    )


def get_additional_fetchers():
    return [
        (fetch_remotejobs_org, "RemoteJobs.org"),
        (fetch_remote_landers, "Remote Landers"),
        (fetch_himalayas_targeted, "Himalayas targeted"),
    ]


def normalize_himalayas_targeted(job: dict) -> dict | None:
    """Preserve Himalayas' applicationLink, pay period and eligibility constraints."""
    if not isinstance(job, dict):
        return None
    url = _source_url(job.get("applicationLink"), "himalayas.app")
    title = _text(job.get("title"), 220)
    company = _text(job.get("companyName"), 180)
    if not url or not title or not company or not job.get("pubDate"):
        return None
    restrictions = [str(v).strip() for v in (job.get("locationRestrictions") or [])
                    if isinstance(v, str) and v.strip()]
    tags = [_text(x, 50) for x in ((job.get("categories") or []) +
                                   (job.get("parentCategories") or []))
            if isinstance(x, str) and x.strip()][:10]
    smin, smax, currency = job.get("minSalary"), job.get("maxSalary"), _text(job.get("currency"), 10)
    salary = ""
    if currency and isinstance(smin, (int, float)) and isinstance(smax, (int, float)):
        period = _text(job.get("salaryPeriod"), 30) or "annual"
        salary = f"{smin:,.0f}–{smax:,.0f} {currency}/{period}"
    return {
        "title": title, "company": company,
        "description": _plain_html(job.get("description") or job.get("excerpt")),
        "url": url,
        "salary": salary,
        "location": "Remote — " + ", ".join(restrictions[:4]) if restrictions else "Remote (eligibility not specified)",
        "published": job["pubDate"],
        "employment_type": _text(job.get("employmentType"), 70),
        "api_level": _text(job.get("seniority"), 70),
        "source": "Himalayas", "tags": tags,
    }


def fetch_himalayas_targeted() -> list[dict]:
    """Discover roles through the provider's supported search endpoint, not scraping."""
    return _fetch_pages(
        "https://himalayas.app/jobs/api/search",
        [{"q": term, "sort": "recent", "page": 1}
         for term in ("junior", "mid-level", "associate", "intern")],
        "jobs", normalize_himalayas_targeted,
    )
