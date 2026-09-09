"""Shareable, measurable content magnets built from already-approved vacancies."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Sequence
from urllib.parse import quote


CATEGORY_LABELS = {
    "development": "Разработка",
    "qa": "QA",
    "devops": "DevOps",
    "data": "Data / AI",
    "design": "Design",
    "pm": "Product / PM",
    "security": "Security",
    "support": "Support",
    "other": "IT",
}


def campaign_payload(kind: str, moment: datetime | None = None) -> str:
    """Stable Telegram-safe payload, e.g. magnet_picks_2026w37."""
    moment = moment or datetime.now(timezone.utc)
    year, week, _ = moment.isocalendar()
    safe_kind = re.sub(r"[^a-z0-9_]+", "_", str(kind).lower()).strip("_")[:24] or "weekly"
    return f"magnet_{safe_kind}_{year}w{week:02d}"[:64]


def build_campaign_bot_link(bot_username: str, payload: str) -> str:
    username = str(bot_username or "").strip().lstrip("@")
    if not username:
        return ""
    safe_payload = re.sub(r"[^A-Za-z0-9_-]", "_", str(payload))[:64]
    return f"https://t.me/{username}?start={safe_payload}"


def _has_salary(job: Dict) -> bool:
    salary = str(job.get("salary") or "").strip().lower()
    return bool(salary and salary not in {"не указана", "not specified", "договорная", "none"})


def _location_value(job: Dict) -> str:
    return str(job.get("geo_restriction") or job.get("location") or "Remote").strip()


def _job_rank(job: Dict) -> tuple:
    """Deterministic product rank for a human-readable weekly shortlist."""
    score = 0
    if _has_salary(job):
        score += 4
    if str(job.get("level") or "").lower() == "junior":
        score += 3
    elif str(job.get("level") or "").lower() == "middle":
        score += 2
    location = _location_value(job).lower()
    if any(token in location for token in ("worldwide", "anywhere", "global")):
        score += 3
    elif "remote" in location:
        score += 1
    if str(job.get("url") or "").startswith("http"):
        score += 1
    if job.get("tags"):
        score += 1
    return (score, str(job.get("title") or "").lower(), str(job.get("company") or "").lower())


def select_weekly_picks(jobs: Sequence[Dict], limit: int = 7) -> List[Dict]:
    """Pick diverse jobs: avoid same company/category dominating the magnet."""
    ranked = sorted(jobs, key=_job_rank, reverse=True)
    out: List[Dict] = []
    companies = set()
    category_counts: Dict[str, int] = {}
    hashes = set()

    for job in ranked:
        url = str(job.get("url") or "")
        title = str(job.get("title") or "").strip()
        if not title or not url.startswith("http"):
            continue
        fingerprint = str(job.get("hash") or url)
        if fingerprint in hashes:
            continue
        company = str(job.get("company") or "").strip().lower()
        category = str(job.get("category") or "other").lower()
        if company and company in companies:
            continue
        if category_counts.get(category, 0) >= 2:
            continue
        hashes.add(fingerprint)
        if company:
            companies.add(company)
        category_counts[category] = category_counts.get(category, 0) + 1
        out.append(job)
        if len(out) >= max(1, limit):
            break

    # If diversity rules were too strict, fill remaining slots without duplicates.
    if len(out) < limit:
        for job in ranked:
            url = str(job.get("url") or "")
            fingerprint = str(job.get("hash") or url)
            if not str(job.get("title") or "").strip() or not url.startswith("http") or fingerprint in hashes:
                continue
            hashes.add(fingerprint)
            out.append(job)
            if len(out) >= limit:
                break
    return out


def build_weekly_picks_report(
    jobs: Sequence[Dict],
    bot_username: str,
    moment: datetime | None = None,
    limit: int = 7,
) -> tuple[str, str]:
    """Return (text, campaign_payload) for an attributable weekly shortlist."""
    picks = select_weekly_picks(jobs, limit=limit)
    payload = campaign_payload("picks", moment)
    if not picks:
        return "", payload

    lines = [
        f"🔥 {len(picks)} свежих Junior/Middle remote IT-вакансий недели",
        "",
        "Не просто лента: отобрал разные направления и компании, чтобы подборку было удобно переслать другу.",
        "",
    ]
    for index, job in enumerate(picks, 1):
        title = str(job.get("title") or "Вакансия").strip()
        company = str(job.get("company") or "").strip()
        category = CATEGORY_LABELS.get(str(job.get("category") or "other"), "IT")
        level = str(job.get("level") or "Junior/Middle")
        salary = str(job.get("salary") or "").strip()
        location = _location_value(job)
        lines.append(f"{index}. {title}" + (f" — {company}" if company else ""))
        meta = f"   {category} · {level} · {location}"
        if _has_salary(job):
            meta += f" · {salary}"
        lines.append(meta)
        lines.append(f"   {job.get('url')}")
        lines.append("")

    link = build_campaign_bot_link(bot_username, payload)
    if link:
        lines.extend([
            "🎯 Хочешь подборку только под свой стек и зарплату?",
            link,
        ])
    return "\n".join(lines)[:4000], payload


def append_campaign_cta(text: str, bot_username: str, kind: str, moment: datetime | None = None) -> tuple[str, str]:
    payload = campaign_payload(kind, moment)
    link = build_campaign_bot_link(bot_username, payload)
    if not link:
        return text[:4000], payload
    suffix = "\n\n🎯 Получать вакансии под свой профиль:\n" + link
    return (str(text).rstrip() + suffix)[:4000], payload
