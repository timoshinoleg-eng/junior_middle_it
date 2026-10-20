"""Deterministic vacancy fit computed from a stored user profile.

The weights mirror the proven Resume Match heuristic (75 / 20 / 5) but work
from the profile (skills and role) instead of a resume document, so a fit score
can be produced for every user without touching personal data.

Nothing in this module performs network, Telegram or database access: the same
input always produces the same output.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set

from resume_matcher import extract_skills, job_text

GRADE_JUNIOR = "junior"
GRADE_MIDDLE = "middle"
GRADE_SENIOR = "senior"
GRADE_UNKNOWN = "unknown"

GRADE_RANK = {GRADE_JUNIOR: 1, GRADE_MIDDLE: 2, GRADE_SENIOR: 3, GRADE_UNKNOWN: 0}

# Declared level is read from the title only. Descriptions mention seniors in
# almost every posting ("a senior mentor", "senior team"), so trusting the body
# would flag honest junior vacancies as fake.
_TITLE_GRADE_PATTERNS = (
    (GRADE_JUNIOR, re.compile(r"(?<![A-Za-z])(?:junior|jr\.?|стаж[её]р|intern)(?![A-Za-z])", re.IGNORECASE)),
    (GRADE_MIDDLE, re.compile(r"(?<![A-Za-z])(?:middle|mid[- ]?level)(?![A-Za-z])", re.IGNORECASE)),
    (GRADE_SENIOR, re.compile(r"(?<![A-Za-z])(?:senior|sr\.?|lead|principal|staff|architect)(?![A-Za-z])", re.IGNORECASE)),
)

# Only explicit ownership of a team counts as a senior requirement. A posting
# that merely mentions a senior colleague is still a junior posting.
_LEAD_REQUIREMENT = re.compile(
    r"(?:you will|you'll)\s+lead\b"
    r"|lead a team"
    r"|managing a team"
    r"|as a (?:tech|team) lead"
    r"|руководить\s+командой"
    r"|управлять\s+командой",
    re.IGNORECASE,
)
_YEARS_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|-\s*\d{1,2})?\s*(?:years?|yrs?|year|лет\b|год(?:а|ов)?)",
    re.IGNORECASE,
)
_YEARS_RANGE_RE = re.compile(
    r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s*(?:years?|yrs?|year|лет\b|год(?:а|ов)?)",
    re.IGNORECASE,
)

_STOPWORDS = {
    "and", "the", "for", "with", "from", "that", "this", "you", "your",
    "our", "are", "will", "have", "has", "job", "role", "team", "work",
    "remote", "company", "looking", "experience", "years", "skills", "using",
    "and", "или", "для", "что", "как", "мы", "вы", "наш", "ваш", "это",
    "все", "всего", "или", "так", "же", "быть", "если", "при", "над",
}

_ROLE_TERMS = {
    "backend": ("backend", "бэкенд", "python", "java", "golang", "php", "ruby", "dotnet"),
    "frontend": ("frontend", "фронтенд", "react", "vue", "angular", "javascript"),
    "fullstack": ("fullstack", "full-stack", "full stack"),
    "qa": ("qa", "quality assurance", "тестировщик", "tester", "sdet", "automation"),
    "devops": ("devops", "sre", "infrastructure", "инфраструктур", "platform engineer"),
    "data": ("data analyst", "data engineer", "data scientist", "аналитик данных"),
    "design": ("designer", "дизайнер", "ux designer", "ui designer"),
    "mobile": ("ios", "android", "mobile developer", "flutter"),
    "pm": ("product manager", "продакт", "project manager"),
}


@dataclass(frozen=True)
class FitReport:
    """Deterministic fit result for one vacancy and one profile."""

    score: int
    stack_match_score: int
    keyword_overlap_pct: int
    role_overlap: bool
    matched_skills: List[str]
    missing_skills: List[str]
    required_skills: List[str]
    profile_skills: List[str]
    warnings: List[str]

    def as_dict(self) -> Dict:
        return {
            "score": self.score,
            "stack_match_score": self.stack_match_score,
            "keyword_overlap_pct": self.keyword_overlap_pct,
            "role_overlap": self.role_overlap,
            "matched_skills": list(self.matched_skills),
            "missing_skills": list(self.missing_skills),
            "required_skills": list(self.required_skills),
            "profile_skills": list(self.profile_skills),
            "warnings": list(self.warnings),
        }


def _normalize(text: str) -> str:
    value = str(text or "").lower().replace("ё", "е")
    value = re.sub(r"[\u2010-\u2015]", "-", value)
    return re.sub(r"\s+", " ", value).strip()


def _keywords(text: str, limit: int = 40) -> Set[str]:
    tokens = re.findall(r"[a-zа-я][a-zа-я0-9+#.-]{2,}", _normalize(text))
    out: List[str] = []
    seen: Set[str] = set()
    for token in tokens:
        if token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= limit:
            break
    return set(out)


def _role_terms(text: str) -> Set[str]:
    normalized = _normalize(text)
    found: Set[str] = set()
    for role, markers in _ROLE_TERMS.items():
        for marker in markers:
            if marker in normalized:
                found.add(role)
                break
    return found


def _profile_text(profile: Optional[Dict]) -> str:
    """Flatten a profile into the text the matcher works with."""
    if not profile:
        return ""
    skills = profile.get("skills")
    if isinstance(skills, (list, tuple, set)):
        skills = ", ".join(str(item) for item in skills)
    parts = [str(skills or ""), str(profile.get("role") or ""), str(profile.get("categories") or "")]
    return " ".join(part for part in parts if part)


def profile_skills(profile: Optional[Dict]) -> List[str]:
    """Canonical skills detected in a profile."""
    return sorted(extract_skills(_profile_text(profile)))


def detect_years_range(text: str) -> tuple:
    """Lowest and highest number of years the posting asks for."""
    lows: List[int] = []
    highs: List[int] = []
    for match in _YEARS_RANGE_RE.finditer(str(text or "")):
        lows.append(int(match.group(1)))
        highs.append(int(match.group(2)))
    for match in _YEARS_RE.finditer(str(text or "")):
        value = int(match.group(1))
        lows.append(value)
        highs.append(value)
    if not lows:
        return None, None
    return min(lows), max(highs)


def detect_years_required(text: str) -> Optional[int]:
    """Largest number of years the posting asks for, if any."""
    _low, high = detect_years_range(text)
    return high


def detect_declared_grade(job: Dict) -> str:
    """Grade claimed by the vacancy title."""
    title = str(job.get("title") or "")
    for grade, pattern in _TITLE_GRADE_PATTERNS:
        if pattern.search(title):
            return grade
    return GRADE_UNKNOWN


def detect_real_grade(job: Dict) -> str:
    """Grade implied by the actual requirements of the vacancy.

    The entry threshold decides the level: "1-2 years" is a junior posting,
    while "5+ years" is a senior one. A wide range never turns a junior
    vacancy into a senior one.
    """
    text = job_text(job)
    low_years, _high_years = detect_years_range(text)
    if _LEAD_REQUIREMENT.search(text):
        return GRADE_SENIOR
    if low_years is None:
        return GRADE_UNKNOWN
    if low_years >= 5:
        return GRADE_SENIOR
    if low_years >= 2:
        return GRADE_MIDDLE
    return GRADE_JUNIOR


def grade_conflict(job: Dict) -> Optional[str]:
    """Short warning when the title promises less than the work requires."""
    declared = detect_declared_grade(job)
    real = detect_real_grade(job)
    if declared == GRADE_UNKNOWN or real == GRADE_UNKNOWN:
        return None
    if GRADE_RANK[real] <= GRADE_RANK[declared]:
        return None
    if real == GRADE_SENIOR:
        return "По заголовку уровень ниже, а требования — senior+: проверь, что вакансия реально под junior/middle."
    return "Требования по опыту выше заявленного уровня."


def compute_fit(profile: Optional[Dict], job: Dict) -> FitReport:
    """Score a vacancy against a profile without any external call."""
    profile_text = _profile_text(profile)
    vacancy_text = job_text(job)

    required = sorted(extract_skills(vacancy_text))
    have = set(extract_skills(profile_text))
    matched = sorted(set(required) & have)
    missing = sorted(set(required) - have)

    if required:
        stack_pct = int(round(100 * len(matched) / len(required)))
        skill_points = 75.0 * stack_pct / 100.0
    else:
        stack_pct = 0
        skill_points = 30.0

    vacancy_keywords = _keywords(vacancy_text)
    profile_keywords = _keywords(profile_text)
    if vacancy_keywords:
        keyword_pct = int(round(100 * len(vacancy_keywords & profile_keywords) / len(vacancy_keywords)))
    else:
        keyword_pct = 0
    keyword_points = min(20.0, keyword_pct * 0.20)

    roles = _role_terms(str(job.get("title") or ""))
    profile_roles = _role_terms(profile_text)
    role_overlap = bool(roles & profile_roles) if roles else False
    role_points = 5.0 if role_overlap else 0.0

    score = int(round(max(0.0, min(100.0, skill_points + keyword_points + role_points))))

    warnings: List[str] = []
    if missing:
        shown = ", ".join(missing[:5])
        warnings.append(f"В вакансии просят навыки, которых нет в профиле: {shown}.")
    if not required:
        warnings.append("Явный стек в вакансии не распознан — оценка примерная.")
    conflict = grade_conflict(job)
    if conflict:
        warnings.append(conflict)
    if profile_text.strip() and not have:
        warnings.append("В профиле пока нет распознанных навыков — оценка занижена, заполни профиль командой /setup.")

    return FitReport(
        score=score,
        stack_match_score=stack_pct,
        keyword_overlap_pct=keyword_pct,
        role_overlap=role_overlap,
        matched_skills=matched,
        missing_skills=missing,
        required_skills=required,
        profile_skills=sorted(have),
        warnings=warnings[:3],
    )
