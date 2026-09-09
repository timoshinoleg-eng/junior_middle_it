"""Saved-search helpers for retention subscriptions.

A saved search deliberately uses the same profile shape as the existing matcher,
so realtime alerts reuse proven matching semantics instead of creating a second
ranking implementation.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List


@dataclass(frozen=True)
class SavedSearch:
    id: int
    user_id: int
    name: str
    categories: List[str]
    skills: str
    min_salary_filter: int
    hide_senior: bool
    enabled: bool = True

    def as_profile(self) -> Dict[str, Any]:
        return {
            "enabled_categories": list(self.categories),
            "skills": self.skills,
            "min_salary_filter": int(self.min_salary_filter or 0),
            "hide_senior": bool(self.hide_senior),
        }


def normalize_skills(skills: Iterable[str] | str, limit: int = 6) -> str:
    if isinstance(skills, str):
        raw = re.split(r"[,;|]", skills)
    else:
        raw = [str(item) for item in skills]
    out = []
    seen = set()
    for item in raw:
        value = re.sub(r"\s+", " ", str(item).strip().lower())
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
        if len(out) >= limit:
            break
    return ", ".join(out)


def normalize_categories(categories: Iterable[str]) -> List[str]:
    out = []
    seen = set()
    for category in categories or []:
        value = str(category or "").strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def build_search_name(categories: List[str], skills: str, fallback: str = "Мой поиск") -> str:
    cats = normalize_categories(categories)
    skill_list = [s.strip() for s in normalize_skills(skills, limit=3).split(",") if s.strip()]
    parts = []
    if cats:
        parts.append("/".join(cats[:2]))
    if skill_list:
        parts.append(", ".join(skill_list[:3]))
    return " · ".join(parts)[:80] or fallback[:80]


def _canonical_skill_tokens(skills: str) -> List[str]:
    """Canonical token set for identity; display order remains user-friendly."""
    return sorted(
        token.strip()
        for token in normalize_skills(skills).split(",")
        if token.strip()
    )


def search_fingerprint(
    categories: List[str],
    skills: str,
    min_salary_filter: int = 0,
    hide_senior: bool = True,
) -> str:
    payload = "|".join(
        [
            ",".join(sorted(normalize_categories(categories))),
            ",".join(_canonical_skill_tokens(skills)),
            str(int(min_salary_filter or 0)),
            "1" if hide_senior else "0",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def saved_search_from_row(row) -> SavedSearch:
    categories = [c for c in str(row[3] or "").split(",") if c]
    return SavedSearch(
        id=int(row[0]),
        user_id=int(row[1]),
        name=str(row[2] or "Мой поиск"),
        categories=categories,
        skills=str(row[4] or ""),
        min_salary_filter=int(row[5] or 0),
        hide_senior=bool(row[6]),
        enabled=bool(row[7]),
    )


def suggested_search_from_resume(job: Dict, matched_skills: List[str]) -> Dict[str, Any]:
    category = str(job.get("category") or "other").strip().lower()
    skills = normalize_skills(matched_skills[:5])
    categories = [category] if category else ["other"]
    return {
        "name": build_search_name(categories, skills, fallback=str(job.get("title") or "Похожие вакансии")),
        "categories": categories,
        "skills": skills,
        "min_salary_filter": 0,
        "hide_senior": True,
    }
