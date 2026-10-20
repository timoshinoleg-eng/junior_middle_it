"""Upskill radar: what the current stack really covers on the vacancy feed.

The radar is computed from the vacancies the channel published in the last
weeks, so it reflects the real demand instead of a static checklist. Every
number is derived from the deterministic fit, no model is involved.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from copilot.fit import compute_fit

COVERAGE_THRESHOLD = 60
DEFAULT_DAYS = 30
MAX_ITEMS = 5

_SALARY_RE = re.compile(r"(\d{3,6})\s*(?:\$|usd|eur|руб|р\.)", re.IGNORECASE)


@dataclass(frozen=True)
class UpskillRadar:
    """Aggregated view of the vacancy feed against one profile."""

    total: int = 0
    covered: int = 0
    coverage_pct: int = 0
    top_missing: List[Tuple[str, int]] = field(default_factory=list)
    top_matched: List[Tuple[str, int]] = field(default_factory=list)
    salary_median: Optional[int] = None
    salary_jobs: int = 0

    def as_dict(self) -> Dict:
        return {
            "total": self.total,
            "covered": self.covered,
            "coverage_pct": self.coverage_pct,
            "top_missing": [list(item) for item in self.top_missing],
            "top_matched": [list(item) for item in self.top_matched],
            "salary_median": self.salary_median,
            "salary_jobs": self.salary_jobs,
        }


def _salary_value(job: Dict) -> Optional[int]:
    """Lower salary bound of a vacancy, when the payload carries one."""
    raw = job.get("salary_min_usd")
    try:
        value = int(raw)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    match = _SALARY_RE.search(str(job.get("salary") or ""))
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _median(values: List[int]) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return int(ordered[middle])
    return int((ordered[middle - 1] + ordered[middle]) // 2)


def build_radar(
    jobs: List[Dict],
    profile: Dict,
    *,
    threshold: int = COVERAGE_THRESHOLD,
    limit: int = MAX_ITEMS,
) -> UpskillRadar:
    """Aggregate the vacancy feed against one profile."""
    vacancies = [job for job in (jobs or []) if job]
    if not vacancies:
        return UpskillRadar()

    missing: Counter = Counter()
    matched: Counter = Counter()
    salaries: List[int] = []
    covered = 0

    for job in vacancies:
        report = compute_fit(profile, job)
        is_covered = report.stack_match_score >= int(threshold)
        if is_covered:
            covered += 1
            salary = _salary_value(job)
            if salary:
                salaries.append(salary)
        for skill in report.missing_skills:
            missing[str(skill)] += 1
        for skill in report.matched_skills:
            matched[str(skill)] += 1

    total = len(vacancies)
    return UpskillRadar(
        total=total,
        covered=covered,
        coverage_pct=int(round(100.0 * covered / total)) if total else 0,
        top_missing=missing.most_common(int(limit)),
        top_matched=matched.most_common(int(limit)),
        salary_median=_median(salaries),
        salary_jobs=len(salaries),
    )


def format_radar(radar: UpskillRadar, *, days: int = DEFAULT_DAYS) -> str:
    """Render the radar as a short message."""
    if not radar.total:
        return (
            f"📡 Upskill-радар за {days} дн.\n\n"
            "Пока нет вакансий для анализа. Загляни позже — посчитаю по свежим постам."
        )

    lines = [f"📡 Твой upskill-радар за {days} дн.", ""]
    lines.append(
        f"Твой стек закрывает {radar.coverage_pct}% вакансий "
        f"({radar.covered} из {radar.total})."
    )
    if radar.top_matched:
        lines.append(
            "Сильные стороны: "
            + ", ".join(f"{name} ({count})" for name, count in radar.top_matched)
        )
    if radar.top_missing:
        lines.append(
            "Чего чаще всего не хватает: "
            + ", ".join(f"{name} ({count})" for name, count in radar.top_missing)
        )
    if radar.salary_median:
        lines.append(
            f"Медиана нижней границы зарплаты среди подходящих: ${radar.salary_median}"
        )
    lines.append("")
    lines.append("Это эвристика по тексту вакансий, а не гарантия оффера.")
    return "\n".join(lines)
