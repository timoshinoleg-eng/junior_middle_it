"""
v6.5 extra job sources — free public APIs / RSS, no scraped private pages.

Sources (all free, no API key):
- 4dayweek.io API v2
- The Muse public jobs API
- RemoteOK category JSON feeds (dev / backend / fullstack / design / devops / data)
- Working Nomads public exposed_jobs API
- RSS/Atom: WWR programming + design/devops/product, Himalayas
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "JuniorMiddleITBot/6.5 "
    "(https://github.com/timoshinoleg-eng/junior_middle_it; +remote job aggregator)"
)


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": DEFAULT_UA,
        "Accept": "application/json, application/rss+xml, application/atom+xml, */*",
    }


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(text))
    return " ".join(text.split())


def _first_text(parent, *names: str) -> str:
    for name in names:
        child = parent.find(name)
        if child is not None and child.text:
            return child.text.strip()
    return ""


def _atom_text(item, local: str) -> str:
    el = item.find(f"{{http://www.w3.org/2005/Atom}}{local}")
    if el is not None and el.text:
        return el.text.strip()
    return ""


# ---------------------------------------------------------------------------
# 4dayweek.io
# ---------------------------------------------------------------------------
def fetch_4dayweek(max_pages: int = 3, limit: int = 50) -> List[Dict]:
    """
    Free public API: https://4dayweek.io/api/v2/jobs
    Docs: https://4dayweek.io/developers
    Fair use: ≤60 req/min; credit 4dayweek.io.
    """
    jobs: List[Dict] = []
    try:
        for page in range(1, max_pages + 1):
            params = {
                "page": page,
                "limit": min(limit, 100),
                "work_arrangement": "remote",
                "level": "junior,mid,entry,associate",
                "category": "engineering,product,design,data,devops,security,qa",
                "sort": "date",
            }
            resp = requests.get(
                "https://4dayweek.io/api/v2/jobs",
                params=params,
                headers=_headers(),
                timeout=25,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data") or []
            if not items:
                break
            for item in items:
                company = (item.get("company") or {}) if isinstance(item.get("company"), dict) else {}
                salary = "Не указана"
                smin, smax = item.get("salary_min"), item.get("salary_max")

                def _usd(cents):
                    if cents is None:
                        return None
                    try:
                        return int(cents) // 100
                    except (TypeError, ValueError):
                        return None

                if smin or smax:
                    cur = item.get("salary_currency") or "USD"
                    period = item.get("salary_period") or "year"
                    lo, hi = _usd(smin), _usd(smax)
                    if lo and hi:
                        salary = f"${lo:,}-${hi:,} {cur}/{period}"
                    elif lo:
                        salary = f"от ${lo:,} {cur}/{period}"
                    elif hi:
                        salary = f"до ${hi:,} {cur}/{period}"

                tags = []
                for key in ("skills", "stack", "tools"):
                    for t in item.get(key) or []:
                        if isinstance(t, dict) and t.get("name"):
                            tags.append(t["name"])
                        elif isinstance(t, str):
                            tags.append(t)

                level_raw = str(item.get("level") or "").lower()
                jobs.append({
                    "title": item.get("title") or item.get("role") or "",
                    "company": company.get("name") or "4dayweek",
                    "description": item.get("description") or "",
                    "url": item.get("url") or (
                        f"https://4dayweek.io/job/{item.get('slug')}" if item.get("slug") else ""
                    ),
                    "salary": salary,
                    "location": "Remote",
                    "published": item.get("posted_at") or "",
                    "employment_type": item.get("contract_type") or item.get("schedule_type") or "",
                    "source": "4dayweek",
                    "tags": tags[:12],
                    "minSalary": (int(smin) // 100) if smin else 0,
                    "maxSalary": (int(smax) // 100) if smax else 0,
                    "currency": item.get("salary_currency") or "USD",
                    "api_level": level_raw,
                })
            if not data.get("has_more"):
                break
        logger.info(f"4dayweek fetched {len(jobs)}")
        return jobs
    except Exception as e:
        logger.error(f"❌ 4dayweek error: {e}")
        return []


# ---------------------------------------------------------------------------
# The Muse
# ---------------------------------------------------------------------------
def fetch_themuse(max_pages: int = 3) -> List[Dict]:
    """
    Free public API (no key): https://www.themuse.com/api/public/jobs
    Filter Flexible/Remote + software categories; entry/mid levels.
    """
    jobs: List[Dict] = []
    categories = [
        "Software Engineering",
        "Data Science",
        "Design and UX",
        "IT",
    ]
    levels = ["Entry Level", "Mid Level"]
    try:
        for page in range(1, max_pages + 1):
            params = [
                ("page", str(page)),
                ("descending", "true"),
                ("location", "Flexible / Remote"),
            ]
            for c in categories:
                params.append(("category", c))
            for lv in levels:
                params.append(("level", lv))

            resp = requests.get(
                "https://www.themuse.com/api/public/jobs",
                params=params,
                headers=_headers(),
                timeout=25,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results") or []
            if not results:
                break
            for item in results:
                company = item.get("company") or {}
                locs = item.get("locations") or []
                loc_names = ", ".join(x.get("name", "") for x in locs if x.get("name")) or "Remote"
                cats = [c.get("name", "") for c in (item.get("categories") or []) if c.get("name")]
                lvl = [c.get("name", "") for c in (item.get("levels") or []) if c.get("name")]
                refs = item.get("refs") or {}
                url = refs.get("landing_page") or refs.get("external_link") or ""
                jobs.append({
                    "title": item.get("name") or "",
                    "company": company.get("name") or "The Muse",
                    "description": _strip_html(item.get("contents") or ""),
                    "url": url,
                    "salary": "Не указана",
                    "location": (
                        loc_names
                        if "remote" in loc_names.lower() or "flexible" in loc_names.lower()
                        else f"{loc_names}, Remote"
                    ),
                    "published": item.get("publication_date") or "",
                    "employment_type": item.get("type") or "",
                    "source": "The Muse",
                    "tags": cats + lvl,
                })
            page_count = int(data.get("page_count") or 1)
            if page >= page_count:
                break
        logger.info(f"The Muse fetched {len(jobs)}")
        return jobs
    except Exception as e:
        logger.error(f"❌ The Muse error: {e}")
        return []


# ---------------------------------------------------------------------------
# RemoteOK category JSON feeds
# ---------------------------------------------------------------------------
REMOTEOK_CATEGORY_FEEDS = [
    ("dev", "https://remoteok.com/remote-dev-jobs.json"),
    ("backend", "https://remoteok.com/remote-backend-jobs.json"),
    ("fullstack", "https://remoteok.com/remote-full-stack-jobs.json"),
    ("design", "https://remoteok.com/remote-design-jobs.json"),
    ("devops", "https://remoteok.com/remote-devops-jobs.json"),
    ("data", "https://remoteok.com/remote-data-jobs.json"),
]


def _parse_remoteok_json(data, source_label: str = "RemoteOK Dev") -> List[Dict]:
    jobs: List[Dict] = []
    if not isinstance(data, list):
        return jobs
    for job in data:
        if not isinstance(job, dict):
            continue
        # first element is often legal/meta
        if job.get("legal") and not job.get("id") and not job.get("slug"):
            continue
        if job.get("last_updated") is not None and not job.get("id") and not job.get("position"):
            continue
        title = job.get("position") or job.get("title") or ""
        if not title:
            continue
        tags = job.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        salary = job.get("salary")
        if not salary and (job.get("salary_min") or job.get("salary_max")):
            salary = f"${job.get('salary_min')}-${job.get('salary_max')}"
        if not salary:
            salary = "Не указана"
        jobs.append({
            "title": title,
            "company": job.get("company") or "",
            "description": _strip_html(job.get("description") or ""),
            "url": job.get("url") or job.get("apply_url") or (
                f"https://remoteok.com/remote-jobs/{job.get('id')}" if job.get("id") else ""
            ),
            "salary": salary,
            "location": job.get("location") or "Remote",
            "published": job.get("date") or job.get("epoch") or "",
            "employment_type": "",
            "source": source_label,
            "tags": list(tags)[:12],
        })
    return jobs


def fetch_remoteok_dev() -> List[Dict]:
    """
    RemoteOK engineering-heavy category feeds.
    Complements full /api dump in channel_bot (dedup handles overlap).
    Credit Remote OK per their API terms.
    """
    seen_urls = set()
    all_jobs: List[Dict] = []
    try:
        for cat, url in REMOTEOK_CATEGORY_FEEDS:
            try:
                resp = requests.get(url, headers=_headers(), timeout=25)
                resp.raise_for_status()
                batch = _parse_remoteok_json(resp.json(), source_label="RemoteOK Dev")
                for j in batch:
                    key = (j.get("url") or "") + "|" + (j.get("title") or "")
                    if key in seen_urls:
                        continue
                    seen_urls.add(key)
                    if cat and cat not in (j.get("tags") or []):
                        j["tags"] = list(j.get("tags") or []) + [cat]
                    all_jobs.append(j)
            except Exception as e:
                logger.warning(f"RemoteOK category {cat}: {e}")
        logger.info(f"RemoteOK Dev (multi-cat) fetched {len(all_jobs)}")
        return all_jobs
    except Exception as e:
        logger.error(f"❌ RemoteOK Dev error: {e}")
        return []


# ---------------------------------------------------------------------------
# Working Nomads (public exposed_jobs)
# ---------------------------------------------------------------------------
def fetch_working_nomads() -> List[Dict]:
    """
    Free public API: https://www.workingnomads.com/api/exposed_jobs/
    No key. Prefer Development / Design / DevOps categories when present.
    """
    prefer = {
        "development",
        "design",
        "devops",
        "sysadmin",
        "product",
        "data",
        "marketing",  # filtered later by IT title signals if needed
    }
    try:
        resp = requests.get(
            "https://www.workingnomads.com/api/exposed_jobs/",
            headers=_headers(),
            timeout=25,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            return []
        jobs: List[Dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or ""
            if not title:
                continue
            cat = str(item.get("category_name") or "").strip()
            # Keep IT-ish categories; if missing category, still include (title filter later)
            if cat and cat.lower() not in prefer and "dev" not in cat.lower():
                # still keep software-looking titles
                blob = f"{title} {item.get('tags') or ''}".lower()
                if not any(x in blob for x in (
                    "engineer", "developer", "dev ", "frontend", "backend",
                    "full stack", "fullstack", "devops", "sre", "qa", "data",
                    "python", "react", "design", "product manager",
                )):
                    continue
            tags_raw = item.get("tags") or ""
            if isinstance(tags_raw, str):
                tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
            elif isinstance(tags_raw, list):
                tags = [str(t) for t in tags_raw]
            else:
                tags = []
            if cat:
                tags = [cat] + tags
            jobs.append({
                "title": title,
                "company": item.get("company_name") or "Working Nomads",
                "description": _strip_html(item.get("description") or ""),
                "url": item.get("url") or "",
                "salary": "Не указана",
                "location": item.get("location") or "Remote",
                "published": item.get("pub_date") or "",
                "employment_type": "",
                "source": "Working Nomads",
                "tags": tags[:12],
            })
        logger.info(f"Working Nomads fetched {len(jobs)}")
        return jobs
    except Exception as e:
        logger.error(f"❌ Working Nomads error: {e}")
        return []


# ---------------------------------------------------------------------------
# Generic RSS / Atom job boards
# ---------------------------------------------------------------------------
RSS_FEEDS = [
    # (name, url, force_remote_tag)
    ("WWR Full-Stack", "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss", True),
    ("WWR Backend", "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss", True),
    ("WWR Frontend", "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss", True),
    ("WWR Design", "https://weworkremotely.com/categories/remote-design-jobs.rss", True),
    ("WWR DevOps", "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss", True),
    ("WWR Product", "https://weworkremotely.com/categories/remote-product-jobs.rss", True),
    ("Himalayas", "https://himalayas.app/jobs/rss", True),
]


def _prepare_rss_xml(content: bytes) -> bytes:
    """
    Flatten namespaces so ElementTree accepts messy RSS (unbound media:/himalayasJobs:).
    - Drop xmlns declarations
    - Strip prefixes from tags/attrs: <atom:link> → <link>, himalayasJobs:companyName → companyName
    """
    text = content.decode("utf-8", errors="replace")
    # remove xmlns declarations (default and prefixed)
    text = re.sub(r'\sxmlns(:\w+)?="[^"]*"', "", text)
    text = re.sub(r"\sxmlns(:\w+)?='[^']*'", "", text)
    # opening/closing tags with prefix: <foo:bar ...> </foo:bar> <foo:bar/>
    text = re.sub(r"</([A-Za-z_][\w.-]*):([A-Za-z_][\w.-]*)>", r"</\2>", text)
    text = re.sub(r"<([A-Za-z_][\w.-]*):([A-Za-z_][\w.-]*)", r"<\2", text)
    # attributes with prefix: foo:bar="..."
    text = re.sub(r"\s([A-Za-z_][\w.-]*):([A-Za-z_][\w.-]*)=", r" \2=", text)
    return text.encode("utf-8")


def fetch_rss_jobs(feeds: Optional[List[Tuple[str, str, bool]]] = None, limit_per: int = 25) -> List[Dict]:
    """Parse public RSS/Atom job feeds into normalized job dicts."""
    feeds = feeds or RSS_FEEDS
    all_jobs: List[Dict] = []
    for name, url, force_remote in feeds:
        try:
            resp = requests.get(url, headers=_headers(), timeout=25)
            resp.raise_for_status()
            root = ET.fromstring(_prepare_rss_xml(resp.content))
            # RSS 2.0 items (after namespace strip)
            items = root.findall(".//item")
            if not items:
                items = root.findall(".//entry")
            count = 0
            for item in items:
                if count >= limit_per:
                    break
                title = _first_text(item, "title")
                link = _first_text(item, "link")
                if not link:
                    link_el = item.find("link")
                    if link_el is not None:
                        link = (link_el.get("href") or link_el.text or "").strip()
                desc = _strip_html(
                    _first_text(item, "description")
                    or _first_text(item, "summary")
                    or _first_text(item, "content")
                )
                pub = (
                    _first_text(item, "pubDate")
                    or _first_text(item, "updated")
                    or _first_text(item, "published")
                )
                company = (
                    _first_text(item, "companyName")
                    or _first_text(item, "creator")
                    or name
                )
                if company == name:
                    author = item.find("author")
                    if author is not None:
                        an = author.find("name")
                        if an is not None and an.text:
                            company = an.text.strip()
                        elif author.text:
                            company = author.text.strip()
                loc_extra = _first_text(item, "locationRestriction")
                location = loc_extra or "Remote"
                if force_remote and location and "remote" not in location.lower():
                    location = f"{location}, Remote"
                cats = [
                    (c.text or "").strip()
                    for c in item.findall("category")
                    if c is not None and (c.text or "").strip()
                ]
                if not title:
                    continue
                all_jobs.append({
                    "title": title,
                    "company": company,
                    "description": desc,
                    "url": link,
                    "salary": "Не указана",
                    "location": location,
                    "published": pub,
                    "employment_type": "",
                    "source": f"RSS:{name}",
                    "tags": cats[:12],
                })
                count += 1
            logger.info(f"RSS {name}: {count} items")
        except Exception as e:
            logger.error(f"❌ RSS {name} error: {e}")
    return all_jobs




# ---------------------------------------------------------------------------
# Source health registry
# ---------------------------------------------------------------------------
class SourceHealthRegistry:
    """In-memory last-cycle health for admin /sources + fail-streak auto-skip."""

    def __init__(self):
        self._rows: Dict[str, Dict] = {}
        self._fail_streak: Dict[str, int] = {}
        # After threshold, skip this many cycles then probe once (avoids permanent skip).
        self._cooldown_left: Dict[str, int] = {}

    def record(self, name: str, fetched: int, error: str = "", elapsed_ms: int = 0) -> None:
        err = (error or "").strip()
        if err:
            self._fail_streak[name] = self._fail_streak.get(name, 0) + 1
        else:
            self._fail_streak[name] = 0
            self._cooldown_left[name] = 0
        self._rows[name] = {
            "name": name,
            "fetched": int(fetched),
            "error": err,
            "elapsed_ms": int(elapsed_ms),
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ok": not err,
            "fail_streak": self._fail_streak.get(name, 0),
            "cooldown_left": self._cooldown_left.get(name, 0),
        }

    def fail_streak(self, name: str) -> int:
        return int(self._fail_streak.get(name, 0))

    def should_skip(self, name: str, max_fails: int = 3) -> bool:
        """
        After N consecutive hard failures, skip for N cycles, then probe once.
        max_fails<=0 disables. Success in record() clears streak + cooldown.
        """
        if not max_fails or max_fails <= 0:
            return False
        if self.fail_streak(name) < int(max_fails):
            return False
        left = int(self._cooldown_left.get(name, 0))
        if left <= 0:
            # Enter / re-enter cooldown window, skip this cycle
            self._cooldown_left[name] = int(max_fails)
            return True
        self._cooldown_left[name] = left - 1
        if self._cooldown_left[name] <= 0:
            # Cooldown exhausted → allow one probe this cycle
            return False
        return True

    def snapshot(self) -> List[Dict]:
        return sorted(self._rows.values(), key=lambda r: r["name"].lower())

    def format_report(self, skip_threshold: int = 0) -> str:
        rows = self.snapshot()
        if not rows:
            return "📡 Source health: пока нет данных (нужен полный crawl cycle)."
        lines = ["📡 Source health (last cycle)", ""]
        total = 0
        fails = 0
        skipped = 0
        for r in rows:
            total += r["fetched"]
            streak = int(r.get("fail_streak") or self.fail_streak(r["name"]))
            is_skip = bool(skip_threshold and streak >= skip_threshold)
            if is_skip:
                skipped += 1
            mark = (
                "⏭️" if is_skip
                else ("✅" if r["fetched"] > 0 and not r["error"] else ("⚠️" if not r["error"] else "❌"))
            )
            if r["error"]:
                fails += 1
            line = f"{mark} {r['name']}: {r['fetched']}"
            if r["elapsed_ms"]:
                line += f" ({r['elapsed_ms']}ms)"
            if streak:
                line += f" fail×{streak}"
            cd = int(r.get("cooldown_left") or 0)
            if cd:
                line += f" cd={cd}"
            if r["error"]:
                line += f" — {r['error'][:80]}"
            lines.append(line)
        lines.append("")
        lines.append(
            f"Σ fetched={total} · sources={len(rows)} · errors={fails}"
            + (f" · auto-skip≥{skip_threshold}: {skipped}" if skip_threshold else "")
        )
        return "\n".join(lines)


SOURCE_HEALTH = SourceHealthRegistry()


def run_fetcher(name: str, fn: Callable[[], List[Dict]]) -> List[Dict]:
    """Execute fetcher with timing + health record."""
    import time
    t0 = time.monotonic()
    err = ""
    jobs: List[Dict] = []
    try:
        jobs = fn() or []
    except Exception as e:
        err = str(e)
        logger.error(f"❌ {name} crashed: {e}")
        jobs = []
    elapsed = int((time.monotonic() - t0) * 1000)
    SOURCE_HEALTH.record(name, len(jobs), error=err, elapsed_ms=elapsed)
    return jobs


def get_extra_fetchers() -> List[Tuple[Callable[[], List[Dict]], str]]:
    """Fetchers to append to the main list (wired in channel_bot.get_api_fetch_functions)."""
    return [
        (fetch_4dayweek, "4dayweek"),
        (fetch_themuse, "The Muse"),
        (fetch_remoteok_dev, "RemoteOK Dev"),
        (fetch_working_nomads, "Working Nomads"),
        (fetch_rss_jobs, "RSS boards"),
    ]
