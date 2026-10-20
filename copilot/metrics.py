"""Metrics of the Copilot surfaces.

The numbers come from the growth event log, so they cost one grouped query
and never touch personal data. The module is pure: it only turns counters into
a report.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

EVENT_GROUPS: Dict[str, tuple] = {
    "fit_viewed": ("copilot_fit_viewed", "copilot_forward_analyzed", "copilot_webapp_fit_viewed"),
    "search_saved": ("copilot_search_saved",),
    "cv_built": ("copilot_cv_built", "copilot_webapp_cv_sent"),
    "cv_cached": ("copilot_cv_cached", "copilot_webapp_cv_cached"),
    "hr_clicked": ("copilot_hr_link_clicked",),
    "ai_viewed": ("copilot_ai_viewed",),
    "ai_failed": ("copilot_ai_failed",),
    "upskill_viewed": ("copilot_upskill_viewed",),
    "application_marked": ("copilot_application_marked",),
    "application_answered": ("copilot_application_answered",),
}


def all_event_names() -> List[str]:
    """Every event name the Copilot writes."""
    names: List[str] = []
    for group in EVENT_GROUPS.values():
        for name in group:
            if name not in names:
                names.append(name)
    return names


@dataclass(frozen=True)
class CopilotMetrics:
    """Counters of the Copilot surfaces over one window."""

    fit_viewed: int = 0
    search_saved: int = 0
    cv_built: int = 0
    cv_cached: int = 0
    hr_clicked: int = 0
    ai_viewed: int = 0
    ai_failed: int = 0
    upskill_viewed: int = 0
    application_marked: int = 0
    application_answered: int = 0

    @property
    def search_conversion_pct(self) -> int:
        """Share of the analyses that ended with a saved search."""
        if not self.fit_viewed:
            return 0
        return int(round(100.0 * self.search_saved / self.fit_viewed))

    @property
    def ai_fallback_pct(self) -> int:
        """Share of the AI answers that fell back to the deterministic report."""
        total = self.ai_viewed + self.ai_failed
        if not total:
            return 0
        return int(round(100.0 * self.ai_failed / total))

    def as_dict(self) -> Dict:
        return {
            "fit_viewed": self.fit_viewed,
            "search_saved": self.search_saved,
            "cv_built": self.cv_built,
            "cv_cached": self.cv_cached,
            "hr_clicked": self.hr_clicked,
            "ai_viewed": self.ai_viewed,
            "ai_failed": self.ai_failed,
            "upskill_viewed": self.upskill_viewed,
            "application_marked": self.application_marked,
            "application_answered": self.application_answered,
            "search_conversion_pct": self.search_conversion_pct,
            "ai_fallback_pct": self.ai_fallback_pct,
        }


def build_metrics(counts: Dict[str, int]) -> CopilotMetrics:
    """Build the metrics from a mapping of event name to counter."""
    counts = counts or {}

    def total(group: str) -> int:
        value = 0
        for name in EVENT_GROUPS.get(group, ()):  # pragma: no branch - tiny loop
            value += int(counts.get(name) or 0)
        return value

    return CopilotMetrics(
        fit_viewed=total("fit_viewed"),
        search_saved=total("search_saved"),
        cv_built=total("cv_built"),
        cv_cached=total("cv_cached"),
        hr_clicked=total("hr_clicked"),
        ai_viewed=total("ai_viewed"),
        ai_failed=total("ai_failed"),
        upskill_viewed=total("upskill_viewed"),
        application_marked=total("application_marked"),
        application_answered=total("application_answered"),
    )


def format_metrics(metrics: CopilotMetrics, *, days: int = 30) -> str:
    """Render the metrics as a short admin report."""
    lines = [
        f"🤖 Copilot metrics · {days}d",
        f"Разборов: {metrics.fit_viewed} · сохранено поисков: {metrics.search_saved} "
        f"({metrics.search_conversion_pct}%)",
        f"CV собрано: {metrics.cv_built} · из кэша: {metrics.cv_cached}",
        f"Переходов к HR: {metrics.hr_clicked} · upskill-радар: {metrics.upskill_viewed}",
        f"Откликов отмечено: {metrics.application_marked} · закрыто: {metrics.application_answered}",
        f"LLM: ответов {metrics.ai_viewed} · откатов на детерминированный {metrics.ai_failed} "
        f"({metrics.ai_fallback_pct}%)",
    ]
    return "\n".join(lines)
