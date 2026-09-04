"""Optional adapter: kalil0321/ats-scrapers (MIT) → channel_bot job dicts.

v7 Stage 3, OSS decision D-10. Facts verified in-session 2026-09-03:
- license MIT (GitHub API + PyPI metadata),
- pushed_at 2026-09-02 (active), 5 contributors, not archived,
- PyPI ats-scrapers 0.3.0.

IMPORTANT deployment constraint (verified in-session via pip resolver):
ats-scrapers requires ``httpx>=0.27`` while python-telegram-bot 20.6 pins
``httpx~=0.25.0`` → installing both in ONE environment is ResolutionImpossible.
Therefore the adapter is strictly optional: it activates only when the package
is importable (i.e. a separate collector environment, see PROCESS_ROLE in
docs/v7/DECISION_LOG.md) and channel_bot falls back to its own fetchers
otherwise. Never add ats-scrapers to requirements.txt while PTB 20.6 is pinned.

Scrapers are used through their registry classes; network access goes through
the package's own httpx-based fetcher (isolated collector process).
"""
from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

try:
    from ats_scrapers.scrapers.ashby import AshbyScraper
    from ats_scrapers.scrapers.greenhouse import GreenhouseScraper
    from ats_scrapers.scrapers.lever import LeverScraper
    ATS_SCRAPERS_AVAILABLE = True
    _IMPORT_ERROR = ''
except Exception as e:  # ImportError or transitive dep missing (bs4, httpx…)
    ATS_SCRAPERS_AVAILABLE = False
    _IMPORT_ERROR = str(e)
    AshbyScraper = GreenhouseScraper = LeverScraper = None  # type: ignore


def import_error() -> str:
    return _IMPORT_ERROR


def _normalize(job, company: str, source_prefix: str) -> Dict:
    """ats_scrapers.models.Job → channel_bot legacy job dict."""
    salary = getattr(job, 'salary_summary', None)
    if not salary:
        parts = [getattr(job, 'salary_min', None), getattr(job, 'salary_max', None)]
        currency = getattr(job, 'salary_currency', None) or ''
        if any(parts):
            salary = f"{parts[0] or '?'}–{parts[1] or '?'} {currency}".strip()
    return {
        'title': getattr(job, 'title', '') or '',
        'company': company,
        'description': getattr(job, 'description', '') or '',
        'url': getattr(job, 'url', '') or getattr(job, 'apply_url', '') or '',
        'salary': salary or 'Не указана',
        'location': getattr(job, 'location', '') or 'Remote',
        'published': str(getattr(job, 'posted_at', '') or ''),
        'employment_type': getattr(job, 'employment_type', '') or '',
        'source': f'{source_prefix}:{company}',
        'tags': [getattr(job, 'department', '')] if getattr(job, 'department', '') else [],
    }


def fetch_with_scraper(scraper_cls, slugs: List[str], source_prefix: str,
                       timeout: float = 20.0) -> List[Dict]:
    """Run one scraper class over all slugs; one bad slug never kills the batch."""
    jobs: List[Dict] = []
    for slug in slugs:
        try:
            fetched = scraper_cls(company_slug=slug, timeout=timeout).fetch()
            for job in fetched:
                jobs.append(_normalize(job, slug, source_prefix))
        except Exception as e:
            logger.error(f"❌ ats-scrapers {source_prefix} {slug} error: {e}")
    return jobs


def fetch_greenhouse(boards: List[str]) -> List[Dict]:
    return fetch_with_scraper(GreenhouseScraper, boards, 'Greenhouse')


def fetch_lever(companies: List[str]) -> List[Dict]:
    return fetch_with_scraper(LeverScraper, companies, 'Lever')


def fetch_ashby(companies: List[str]) -> List[Dict]:
    return fetch_with_scraper(AshbyScraper, companies, 'Ashby')
