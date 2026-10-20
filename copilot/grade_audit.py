"""Global audit of the real level behind a vacancy title.

The audit is deterministic and candidate independent: the same vacancy always
produces the same verdict, so the result is cached per vacancy instead of being
recomputed for every user. Detection is deliberately conservative - a vacancy is
flagged only when the title promises a level and the requirements clearly ask
for more.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from resume_matcher import job_text

from copilot.fit import (
    GRADE_JUNIOR,
    GRADE_MIDDLE,
    GRADE_SENIOR,
    GRADE_UNKNOWN,
    detect_declared_grade,
    detect_real_grade,
    detect_years_required,
)

_REALISM = {
    (GRADE_JUNIOR, GRADE_JUNIOR): 100,
    (GRADE_JUNIOR, GRADE_MIDDLE): 60,
    (GRADE_JUNIOR, GRADE_SENIOR): 30,
    (GRADE_MIDDLE, GRADE_JUNIOR): 90,
    (GRADE_MIDDLE, GRADE_MIDDLE): 100,
    (GRADE_MIDDLE, GRADE_SENIOR): 35,
    (GRADE_SENIOR, GRADE_JUNIOR): 80,
    (GRADE_SENIOR, GRADE_MIDDLE): 85,
    (GRADE_SENIOR, GRADE_SENIOR): 100,
}

_BADGES = {
    (GRADE_JUNIOR, GRADE_JUNIOR): "Уровень соответствует заголовку",
    (GRADE_JUNIOR, GRADE_MIDDLE): "Junior в заголовке, по требованиям — middle",
    (GRADE_JUNIOR, GRADE_SENIOR): "Junior в заголовке, по требованиям — senior+",
    (GRADE_MIDDLE, GRADE_JUNIOR): "Заголовок выше требований",
    (GRADE_MIDDLE, GRADE_MIDDLE): "Уровень соответствует заголовку",
    (GRADE_MIDDLE, GRADE_SENIOR): "Middle в заголовке, по требованиям — senior+",
    (GRADE_SENIOR, GRADE_JUNIOR): "Уровень ниже заголовка",
    (GRADE_SENIOR, GRADE_MIDDLE): "Уровень ниже заголовка",
    (GRADE_SENIOR, GRADE_SENIOR): "Уровень соответствует заголовку",
}


@dataclass(frozen=True)
class GradeAudit:
    """Verdict about the real level of one vacancy."""

    declared_grade: str
    real_grade: str
    realism_score: int
    is_fake_grade: bool
    badge_text: str
    signals: List[str]

    def as_dict(self) -> Dict:
        return {
            "declared_grade": self.declared_grade,
            "real_grade": self.real_grade,
            "realism_score": self.realism_score,
            "is_fake_grade": self.is_fake_grade,
            "badge_text": self.badge_text,
            "signals": list(self.signals),
        }

    @classmethod
    def from_dict(cls, raw: Dict) -> "GradeAudit":
        return cls(
            declared_grade=str(raw.get("declared_grade") or GRADE_UNKNOWN),
            real_grade=str(raw.get("real_grade") or GRADE_UNKNOWN),
            realism_score=int(raw.get("realism_score") or 0),
            is_fake_grade=bool(raw.get("is_fake_grade")),
            badge_text=str(raw.get("badge_text") or ""),
            signals=[str(item) for item in (raw.get("signals") or [])],
        )


def audit_grade(job: Dict) -> GradeAudit:
    """Compare the grade promised by the title with the grade actually required."""
    declared = detect_declared_grade(job)
    real = detect_real_grade(job)
    years = detect_years_required(job_text(job))

    signals: List[str] = []
    if declared == GRADE_UNKNOWN:
        signals.append("В заголовке нет явного уровня")
    if years is not None:
        signals.append(f"Требуется опыт: {years} г.")
    if real == GRADE_SENIOR and years is not None and years >= 5:
        signals.append("Порог senior+ по опыту")

    is_fake = declared in (GRADE_JUNIOR, GRADE_MIDDLE) and real == GRADE_SENIOR
    if is_fake:
        signals.append("Заявленный уровень ниже требуемого")

    realism = _REALISM.get((declared, real), 75 if declared == GRADE_UNKNOWN else 70)
    badge = _BADGES.get((declared, real), "Уровень в заголовке не указан")

    return GradeAudit(
        declared_grade=declared,
        real_grade=real,
        realism_score=realism,
        is_fake_grade=is_fake,
        badge_text=badge,
        signals=signals[:4],
    )
