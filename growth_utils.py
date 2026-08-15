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


# Generic/source-like company names that should yield to "Company: Title" parse
_GENERIC_COMPANY_MARKERS = (
    "wwr",
    "rss:",
    "rss ",
    "remoteok",
    "we work remotely",
    "himalayas",
    "4dayweek",
    "the muse",
    "working nomads",
    "job board",
    "unknown",
)


def normalize_job_title_company(job: Dict) -> Dict:
    """
    Split 'Company: Role Title' patterns (common on WWR RSS / HN).
    Only rewrites when company looks generic/source-like or matches left side.
    """
    title = str(job.get("title") or "").strip()
    company = str(job.get("company") or "").strip()
    if not title or ":" not in title:
        return job
    left, right = title.split(":", 1)
    left, right = left.strip(), right.strip()
    if len(left) < 2 or len(left) > 80 or len(right) < 8:
        return job
    # avoid time-like "10:00 Remote Engineer"
    if left.isdigit() or re.fullmatch(r"\d{1,2}", left):
        return job
    company_l = company.lower()
    generic = (
        not company
        or any(m in company_l for m in _GENERIC_COMPANY_MARKERS)
        or company_l == left.lower()
        or company_l.startswith("rss:")
    )
    if not generic:
        return job
    job["company"] = left
    job["title"] = right
    return job


THEMATIC_TRACK_LABELS = {
    "development": "Разработка",
    "data_ai": "Data / AI",
    "vibe_coding": "Vibe coding / Builders",
    "qa": "QA",
    "devops_infra": "DevOps / Infra",
    "design_product": "Design / Product",
    "support_other": "Other IT",
}

_REMOTE_SIGNALS = (
    "remote", "remoto", "work from home", "wfh", "distributed", "fully distributed",
    "удалённо", "удаленно", "дистанционно",
)
_HYBRID_OR_ONSITE_SIGNALS = (
    "hybrid", "on-site", "onsite", "in office", "office-based",
    "гибрид", "офис", "в офисе",
)
_WORLDWIDE_SIGNALS = (
    "worldwide", "anywhere", "global remote", "work from anywhere",
    "remote worldwide", "из любой страны", "в любой стране",
)
_GEO_RESTRICTION_SIGNALS = (
    "india", "usa", "united states", "canada", "uk", "united kingdom",
    "europe", "european union", "eu ", "latam", "latin america",
    "russia", "росси", "снг", "cнg", "germany", "france", "spain",
    "poland", "japan", "australia", "israel", "brazil", "mexico",
)
_SENIORITY_CONFLICT_SIGNALS = (
    "senior", "staff", "principal", "lead", "head of", "director",
    "architect", "5+ years", "6+ years", "7+ years", "8+ years",
    "experienced ", "deep experience", "considerable experience",
    "proven experience", "extensive experience",
)
_EXPLICIT_JUNIOR_SIGNALS = (
    "junior", "jr.", "entry level", "entry-level", "graduate", "trainee", "intern",
    "стажёр", "стажер", "джун",
)
_EXPLICIT_MIDDLE_SIGNALS = (
    "middle", "mid-level", "mid level", "2+ years", "3+ years", "мидл",
)


def _job_text(job: Dict) -> str:
    """Return normalized text used by the editorial gate."""
    tags = job.get("tags") or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    parts = [
        str(job.get("title") or ""),
        str(job.get("description") or ""),
        str(job.get("location") or ""),
        " ".join(str(tag) for tag in tags),
    ]
    return " ".join(parts).lower()


def _contains_any(text: str, signals: Tuple[str, ...]) -> bool:
    return any(signal in text for signal in signals)


def classify_thematic_track(job: Dict) -> str:
    """Map legacy categories and vacancy text to one stable audience-facing track."""
    category = str(job.get("category") or "other").lower()
    text = _job_text(job)

    vibe_signals = (
        "vibe coding", "vibecoding", "no-code", "nocode", "low-code", "lowcode",
        "ai builder", "ai-assisted", "ai assisted", "agentic", "ai agent",
        "prompt engineer", "rapid prototyping",
    )
    if _contains_any(text, vibe_signals):
        return "vibe_coding"
    if category == "data" or _contains_any(text, ("machine learning", "data scientist", "data engineer", "analytics", "llm engineer")):
        return "data_ai"
    if category == "qa" or _contains_any(text, ("quality assurance", "software tester", "test automation", "qa engineer")):
        return "qa"
    if category == "devops" or _contains_any(text, ("site reliability", "sre", "platform engineer", "cloud engineer", "devops")):
        return "devops_infra"
    design_signal = bool(re.search(r"\b(?:ux|ui)\b", text)) or _contains_any(
        text, ("product designer", "product manager")
    )
    if category in {"design", "pm"} or design_signal:
        return "design_product"
    if category in {"support", "security"}:
        return "support_other"
    if category == "development" or _contains_any(text, ("developer", "software engineer", "frontend", "backend", "fullstack", "mobile")):
        return "development"
    return "support_other"


def build_specialization_tags(job: Dict, track: Optional[str] = None) -> List[str]:
    """Return stable, lowercase tags for discovery and digest grouping."""
    text = _job_text(job)
    track = track or classify_thematic_track(job)
    tags: List[str] = [track]
    signals = (
        ("backend", ("backend", "back-end", "django", "fastapi", "flask", "java", "go ")),
        ("frontend", ("frontend", "front-end", "react", "vue", "angular")),
        ("mobile", ("mobile", "android", "ios", "react native", "flutter")),
        ("fullstack", ("fullstack", "full-stack")),
        ("data", ("data analyst", "data engineer", "data scientist", "analytics")),
        ("ml", ("machine learning", "deep learning", "ml engineer", "mlops")),
        ("ai", ("artificial intelligence", "generative ai", "llm", "large language model")),
        ("nocode", ("no-code", "nocode", "low-code", "lowcode")),
        ("agentic_workflows", ("agentic", "ai agent", "workflow automation")),
        ("qa_manual", ("manual testing", "manual qa")),
        ("qa_auto", ("automation testing", "test automation", "selenium", "playwright")),
        ("sre", ("site reliability", "sre")),
        ("cloud", ("aws", "azure", "gcp", "cloud")),
        ("uxui", ("ux", "ui", "figma", "product designer")),
        ("product_management", ("product manager", "product owner")),
        ("security", ("security", "cybersecurity", "infosec")),
    )
    for tag, tag_signals in signals:
        if _contains_any(text, tag_signals) and tag not in tags:
            tags.append(tag)
    return tags[:4]


def assess_remote_eligibility(job: Dict, remote_only_sources: Tuple[str, ...] = ()) -> Dict[str, Any]:
    """Assess remote evidence without presenting inferred conditions as verified facts."""
    text = _job_text(job)
    location = str(job.get("location") or "").strip()
    source = str(job.get("source") or "").strip()
    source_family = source.split(":", 1)[0].strip().lower()
    trusted_sources = {str(item).lower() for item in remote_only_sources}
    source_declares_remote = source.lower() in trusted_sources or source_family in trusted_sources or source.lower().startswith("rss:")

    if _contains_any(text, _HYBRID_OR_ONSITE_SIGNALS):
        return {
            "remote_evidence": "",
            "remote_scope": "not_remote_only",
            "location_restriction": location,
            "remote_confidence": 0,
            "remote_status": "rejected",
            "remote_reason": "hybrid_or_onsite_signal",
        }

    evidence = ""
    if _contains_any(location.lower(), _REMOTE_SIGNALS):
        evidence = f"location: {location}"
    elif _contains_any(text, _REMOTE_SIGNALS):
        evidence = "description marks the role as remote"

    # Source policy is useful context, but it is not proof that an individual role
    # is remote. Aggregators and remote-first boards can contain local, hybrid or
    # expired listings; publish only when the vacancy itself states remote terms.
    if not evidence:
        reason = (
            "source_policy_without_explicit_remote_evidence"
            if source_declares_remote
            else "missing_remote_evidence"
        )
        return {
            "remote_evidence": "",
            "remote_scope": "unconfirmed",
            "location_restriction": location,
            "remote_confidence": 0,
            "remote_status": "quarantine",
            "remote_reason": reason,
        }

    if _contains_any(text, _WORLDWIDE_SIGNALS):
        scope = "worldwide"
    elif re.search(r"\b(?:utc|gmt)\s*[+\-−]?\s*\d{1,2}", text):
        scope = "timezone_restricted"
    elif _contains_any(text, _GEO_RESTRICTION_SIGNALS):
        scope = "country_restricted"
    else:
        scope = "scope_unconfirmed"

    confidence = 95 if evidence.startswith("location:") else 80
    return {
        "remote_evidence": evidence,
        "remote_scope": scope,
        "location_restriction": location,
        "remote_confidence": confidence,
        "remote_status": "passed",
        "remote_reason": "",
    }


def assess_level_evidence(job: Dict) -> Dict[str, Any]:
    """Describe whether Junior/Middle is explicit or inferred, including conflicts."""
    title = str(job.get("title") or "").lower()
    text = _job_text(job)
    level = str(job.get("level") or "").strip()

    if _contains_any(text, _SENIORITY_CONFLICT_SIGNALS):
        return {
            "level_source": "conflict",
            "level_confidence": 0,
            "level_reason": "seniority_conflict",
        }
    if _contains_any(title, _EXPLICIT_JUNIOR_SIGNALS) or _contains_any(title, _EXPLICIT_MIDDLE_SIGNALS):
        return {
            "level_source": "explicit_title",
            "level_confidence": 95,
            "level_reason": "",
        }
    if _contains_any(text, _EXPLICIT_JUNIOR_SIGNALS) or _contains_any(text, _EXPLICIT_MIDDLE_SIGNALS):
        return {
            "level_source": "explicit_description",
            "level_confidence": 85,
            "level_reason": "",
        }
    if level in {"Junior", "Middle"}:
        return {
            "level_source": "inferred",
            "level_confidence": 55,
            "level_reason": "level_not_explicit_in_source",
        }
    return {
        "level_source": "unknown",
        "level_confidence": 0,
        "level_reason": "missing_level_evidence",
    }


def apply_editorial_quality_gate(job: Dict, remote_only_sources: Tuple[str, ...] = ()) -> Dict:
    """Attach routing and evidence fields; quarantine uncertain jobs before publication."""
    track = classify_thematic_track(job)
    remote = assess_remote_eligibility(job, remote_only_sources=remote_only_sources)
    level = assess_level_evidence(job)
    reasons = [reason for reason in (remote["remote_reason"], level["level_reason"]) if reason]

    if remote["remote_status"] == "rejected" or level["level_source"] == "conflict":
        status = "excluded"
    elif remote["remote_status"] != "passed" or level["level_confidence"] == 0:
        status = "quarantine"
    else:
        status = "passed"

    job.update(remote)
    job.update(level)
    job["primary_track"] = track
    job["specialization_tags"] = build_specialization_tags(job, track=track)
    job["quality_gate_status"] = status
    job["quarantine_reasons"] = reasons
    return job


def compute_publish_score(job: Dict, salary_display: str = "") -> int:
    """
    Numeric quality score for channel selection (higher = better).
    salary_display: pre-extracted display string; empty uses job['salary'].
    """
    score = 0
    gate_status = str(job.get("quality_gate_status") or "")
    if gate_status == "excluded":
        return -100
    if gate_status == "quarantine":
        return -50

    level = str(job.get("level") or "")
    if level == "Junior":
        score += 4
    elif level == "Middle":
        score += 3

    title = str(job.get("title") or "").lower()
    if any(x in title for x in ("junior", "jr.", "jr ", "entry", "intern", "graduate", "trainee", "associate")):
        score += 2
    if any(x in title for x in ("senior", "staff", "principal", "lead ", "head of", "director")):
        score -= 2

    sal = salary_display or str(job.get("salary") or "")
    if sal and sal not in {"Не указана", "Not specified", "Договорная", ""}:
        score += 3
    if job.get("salary_min_usd"):
        score += 1

    if str(job.get("url") or "").startswith("http"):
        score += 2

    desc = str(job.get("description") or "")
    if len(desc) >= 200:
        score += 1
    if len(desc) >= 500:
        score += 1

    tags = job.get("tags") or []
    if isinstance(tags, list) and tags:
        score += min(2, max(1, len(tags) // 3))

    loc = str(job.get("location") or "").lower()
    if "remote" in loc or "удал" in loc or "flexible" in loc:
        score += 1

    remote_scope = str(job.get("remote_scope") or "")
    if remote_scope == "worldwide":
        score += 2
    elif remote_scope in {"country_restricted", "timezone_restricted"}:
        score += 1

    if int(job.get("level_confidence") or 0) >= 85:
        score += 1
    if job.get("primary_track") and job.get("primary_track") != "support_other":
        score += 1

    return score


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


def parse_channel_routes(raw: str) -> List[Tuple[List[str], str]]:
    """
    Parse multi-track routing string.

    Format (semicolon-separated rules):
      development,qa,devops:@junior_dev;data:@junior_data;design,pm:@junior_design;*:@junior_all

    Each rule: categories,comma-separated : channel_id
    Special categories: * or all = catch-all / default for unmatched.
    Returns list of (categories_lower, channel_id) in order.
    """
    routes: List[Tuple[List[str], str]] = []
    if not raw or not str(raw).strip():
        return routes
    for part in str(raw).split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        # split only on last colon? channel can be @name or -100id
        # use first colon after categories - categories don't have colon
        cats_part, ch = part.rsplit(":", 1)
        ch = ch.strip()
        if not ch:
            continue
        if not (ch.startswith("@") or ch.startswith("-") or ch.lstrip("-").isdigit()):
            # allow bare username → @username
            if ch.replace("_", "").isalnum():
                ch = f"@{ch}"
            else:
                continue
        cats = [c.strip().lower() for c in cats_part.split(",") if c.strip()]
        if cats:
            routes.append((cats, ch))
    return routes


def resolve_channels_for_job(
    job: Dict,
    routes: List[Tuple[List[str], str]],
    default_channel: str,
    enabled: bool = True,
    mirror_main: bool = False,
) -> List[str]:
    """
    Resolve target chat_ids for a job.

    - If multi-track disabled or no routes: [default_channel]
    - Else: all specialty routes matching category + catch-all (*) routes
    - Unmatched category → default_channel (or * route if defined)
    - mirror_main: also append default_channel when specialty matched
    """
    default_channel = (default_channel or "").strip()
    if not enabled or not routes:
        return [default_channel] if default_channel else []

    cat = str(job.get("category") or "other").lower()
    specialty: List[str] = []
    catchalls: List[str] = []

    for cats, ch in routes:
        is_star = any(c in ("*", "all", "default") for c in cats)
        if is_star:
            if ch not in catchalls:
                catchalls.append(ch)
            continue
        if cat in cats and ch not in specialty:
            specialty.append(ch)

    channels: List[str] = []
    if specialty:
        channels.extend(specialty)
        if mirror_main and default_channel and default_channel not in channels:
            channels.append(default_channel)
    else:
        # unmatched → catch-all routes or default CHANNEL_ID
        if catchalls:
            channels.extend(catchalls)
        elif default_channel:
            channels.append(default_channel)

    # de-dupe preserve order
    seen = set()
    out = []
    for ch in channels:
        if ch and ch not in seen:
            seen.add(ch)
            out.append(ch)
    return out


def describe_channel_routes(
    routes: List[Tuple[List[str], str]],
    default_channel: str,
    category_names: Optional[Dict[str, str]] = None,
) -> str:
    """Human-readable routes for logs /stats."""
    category_names = category_names or {}
    lines = []
    for cats, ch in routes:
        labels = []
        for c in cats:
            if c in ("*", "all", "default"):
                labels.append("*")
            else:
                labels.append(category_names.get(c, c))
        lines.append(f"{', '.join(labels)} → {ch}")
    if default_channel:
        lines.append(f"fallback → {default_channel}")
    return "\n".join(lines) if lines else f"single → {default_channel}"


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
