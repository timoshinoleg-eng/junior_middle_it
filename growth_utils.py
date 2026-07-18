"""
Growth utilities v6.1+ — fuzzy dedup, salary normalize, referral, salary magnet.
"""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

try:
    from rapidfuzz import fuzz

    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

# Approximate FX for min-filter comparisons (not trading rates)
FX_TO_USD = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "RUB": 0.011,
    "RUR": 0.011,
    "₽": 0.011,
    "$": 1.0,
    "€": 1.08,
    "£": 1.27,
}


def job_fingerprint(job: Dict) -> str:
    title = re.sub(r"\s+", " ", str(job.get("title", "")).lower()).strip()
    company = re.sub(r"\s+", " ", str(job.get("company", "")).lower()).strip()
    return f"{title}::{company}"


def fuzzy_is_near_duplicate(
    job: Dict,
    recent_fingerprints: List[str],
    threshold: int = 90,
) -> bool:
    """Return True if title+company is near-duplicate of a recent post."""
    if not recent_fingerprints:
        return False
    key = job_fingerprint(job)
    if not key or key == "::":
        return False
    if key in recent_fingerprints:
        return True
    if not RAPIDFUZZ_AVAILABLE:
        return False
    for other in recent_fingerprints:
        if fuzz.token_set_ratio(key, other) >= threshold:
            return True
    return False


def parse_salary_to_usd_min(job: Dict) -> Optional[int]:
    """
    Best-effort extract minimum annual-ish USD amount for filtering.
    Monthly RUB/USD figures kept as-is numerically after FX; not perfect,
    enough for min_salary_filter gate.
    """
    # Structured fields first
    for key in ("salary_min", "minSalary", "min_salary"):
        val = job.get(key)
        if val is not None:
            try:
                amount = float(val)
                if amount <= 0:
                    continue
                currency = str(job.get("currency") or job.get("salary_currency") or "USD").upper()
                rate = FX_TO_USD.get(currency, 1.0)
                # Heuristic: RUB under 1e6 likely monthly; USD under 500 likely monthly-ish hour→skip
                if currency in {"RUB", "RUR"} and amount < 500_000:
                    amount *= 12  # monthly → annual-ish for comparison consistency optional
                return int(amount * rate)
            except (TypeError, ValueError):
                pass

    raw = str(job.get("salary") or "")
    if not raw or raw in {"Не указана", "Not specified", "Договорная"}:
        return None

    text = raw.replace("\u00a0", " ").replace(",", "")
    currency = "USD"
    lower = text.lower()
    if "₽" in text or "руб" in lower or "rub" in lower:
        currency = "RUB"
    elif "€" in text or "eur" in lower:
        currency = "EUR"
    elif "£" in text or "gbp" in lower:
        currency = "GBP"
    elif "$" in text or "usd" in lower:
        currency = "USD"

    # Ranges: 1000-2000, 100k-150k, 150 000 – 200 000
    patterns = [
        r"(\d+(?:\.\d+)?)\s*[kк]\s*[-–—]\s*(\d+(?:\.\d+)?)\s*[kк]",
        r"(\d{2,7})\s*[-–—]\s*(\d{2,7})",
        r"(?:от|from)\s*(\d+(?:\.\d+)?)\s*[kк]?",
        r"(\d+(?:\.\d+)?)\s*[kк]",
        r"(\d{3,7})",
    ]
    min_amount: Optional[float] = None
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if not m:
            continue
        groups = [g for g in m.groups() if g is not None]
        nums = []
        for g in groups:
            try:
                n = float(g)
                if "k" in m.group(0).lower() or "к" in m.group(0).lower():
                    n *= 1000
                nums.append(n)
            except ValueError:
                continue
        if nums:
            min_amount = min(nums)
            break

    if min_amount is None or min_amount <= 0:
        return None

    rate = FX_TO_USD.get(currency, 1.0)
    # Monthly RUB common in CIS postings
    if currency == "RUB" and min_amount < 500_000:
        min_amount *= 12
    return int(min_amount * rate)


def enrich_job_salary_fields(job: Dict) -> Dict:
    """Attach salary_min_usd for filtering; leave display string untouched."""
    usd_min = parse_salary_to_usd_min(job)
    if usd_min is not None:
        job["salary_min_usd"] = usd_min
    return job


def passes_min_salary(job: Dict, min_salary_usd: int = 0) -> bool:
    if not min_salary_usd or min_salary_usd <= 0:
        return True
    amount = job.get("salary_min_usd")
    if amount is None:
        amount = parse_salary_to_usd_min(job)
    # Unknown salary: keep (don't over-filter firehose)
    if amount is None:
        return True
    return int(amount) >= int(min_salary_usd)


def build_referral_link(bot_username: str, user_id: int) -> str:
    uname = (bot_username or "").lstrip("@")
    if not uname:
        return ""
    return f"https://t.me/{uname}?start=ref_{user_id}"


def parse_start_payload(args: Optional[List[str]]) -> Tuple[Optional[str], Optional[int]]:
    """Parse /start deep-link. Returns (kind, referrer_id)."""
    if not args:
        return None, None
    payload = str(args[0]).strip()
    if payload.startswith("ref_"):
        try:
            return "ref", int(payload[4:])
        except ValueError:
            return "ref", None
    return payload, None


def job_matches_profile(job: Dict, settings: Dict) -> bool:
    """
    Match job against user profile from /setup.
    settings keys: enabled_categories, min_salary_filter, skills (comma str), hide_senior
    """
    if not settings:
        return True

    cats = settings.get("enabled_categories") or []
    if cats and cats != [""]:
        cat = job.get("category") or "other"
        if cat not in cats:
            return False

    if settings.get("hide_senior", True):
        level = str(job.get("level") or "").lower()
        if level == "senior":
            return False

    min_sal = int(settings.get("min_salary_filter") or 0)
    if min_sal > 0 and not passes_min_salary(job, min_sal):
        return False

    skills_raw = settings.get("skills") or ""
    skills = [s.strip().lower() for s in str(skills_raw).split(",") if s.strip()]
    if skills:
        blob = " ".join(
            [
                str(job.get("title") or ""),
                str(job.get("description") or ""),
                " ".join(job.get("tags") or []) if isinstance(job.get("tags"), list) else str(job.get("tags") or ""),
            ]
        ).lower()
        if not any(s in blob for s in skills):
            return False

    return True


def passes_channel_tracks(job: Dict, allowed: List[str]) -> bool:
    """Channel publication track filter. allowed=['all'] disables filter."""
    if not allowed:
        return True
    allowed_norm = [a.strip().lower() for a in allowed if a and a.strip()]
    if not allowed_norm or "all" in allowed_norm:
        return True
    cat = str(job.get("category") or "other").lower()
    return cat in allowed_norm


def apply_premium_to_settings(settings: Dict, premium: bool) -> Dict:
    """Soft ref reward: premium users see senior + larger digests (caller sets max)."""
    s = dict(settings or {})
    if premium:
        s['premium_unlocked'] = True
        s['hide_senior'] = False
    return s


def build_salary_magnet_report(
    jobs: List[Dict],
    category_names: Optional[Dict[str, str]] = None,
    top_n: int = 8,
) -> str:
    """
    Weekly content-magnet text: salary medians by category×level.
    Uses salary_min_usd when present.
    """
    category_names = category_names or {}
    buckets: Dict[Tuple[str, str], List[int]] = defaultdict(list)
    for job in jobs:
        amount = job.get('salary_min_usd')
        if amount is None:
            continue
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        cat = str(job.get('category') or 'other')
        level = str(job.get('level') or 'Junior')
        buckets[(cat, level)].append(amount)

    if not buckets:
        return (
            "📊 Недельный salary-дайджест\n\n"
            "Пока мало вакансий с распознанной вилкой. "
            "Подпишись на канал — обновим через неделю."
        )

    ranked = sorted(
        buckets.items(),
        key=lambda kv: statistics.median(kv[1]),
        reverse=True,
    )[:top_n]

    lines = [
        "📊 Недельный salary-магнит · Junior/Middle remote IT",
        f"Выборка: {sum(len(v) for v in buckets.values())} вакансий с вилкой",
        "",
        "Медиана min (USD-ish / год-эквивалент):",
    ]
    for (cat, level), amounts in ranked:
        cat_ru = category_names.get(cat, cat)
        med = int(statistics.median(amounts))
        lo, hi = min(amounts), max(amounts)
        lines.append(
            f"• {cat_ru} · {level}: ~${med:,}  "
            f"(n={len(amounts)}, ${lo:,}–${hi:,})"
        )

    lines.extend([
        "",
        "⚠️ Ориентир, не оффер: разные валюты/месяц-год сведены эвристикой.",
        "Настрой профиль: /setup · личный digest: /digest on",
    ])
    return "\n".join(lines)


def serialize_job_payload(job: Dict) -> Dict:
    """Compact job dict for expand/compact cache (JSON-safe)."""
    tags = job.get("tags") or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    return {
        "hash": job.get("hash") or job.get("content_hash") or "",
        "title": job.get("title") or "",
        "company": job.get("company") or "",
        "level": job.get("level") or "Junior",
        "category": job.get("category") or "other",
        "salary": job.get("salary") or "Не указана",
        "location": job.get("location") or "Remote",
        "description": str(job.get("description") or "")[:800],
        "url": job.get("url") or "",
        "source": job.get("source") or "",
        "tags": tags[:12],
        "salary_min_usd": job.get("salary_min_usd"),
    }
