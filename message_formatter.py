"""Public-channel vacancy formatter.

The public Telegram channel is a broadcast surface, not a personal settings UI.
This module keeps channel cards readable on mobile and deliberately avoids
callback buttons that would imply per-user state changes on a shared post.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from html import unescape as html_unescape
from urllib.parse import quote

from message_formatter_base import *  # noqa: F401,F403
from message_formatter_base import JobMessageFormatter as _BaseJobMessageFormatter


TRACK_NAMES_RU = {
    "development": "Разработка",
    "data_ai": "Data / AI",
    "vibe_coding": "AI / Builders",
    "qa": "QA",
    "devops_infra": "DevOps / Infra",
    "design_product": "Design / Product",
    "support_other": "Other IT",
}


def build_telegram_share_url(job: dict, bot_username: str = "") -> str:
    """Build an official Telegram share URL with a clear attributable CTA."""
    job_url = str(job.get("url") or "").strip()
    username = str(bot_username or "").strip().lstrip("@")
    job_id = str(job.get("hash") or job.get("content_hash") or "job")[:40]

    if job_url:
        share_target = job_url
    elif username:
        share_target = f"https://t.me/{username}?start=share_{job_id}"
    else:
        return ""

    title = str(job.get("title") or "IT-вакансия").strip()
    company = str(job.get("company") or "").strip()
    level = str(job.get("level") or "Junior/Middle").strip()
    headline = f"{title} — {company}" if company else title
    text_parts = [headline, f"{level} · remote IT"]
    if username:
        text_parts.append(
            f"Личный поиск вакансий: https://t.me/{username}?start=web_home"
        )

    return (
        "https://t.me/share/url?url="
        + quote(share_target, safe="")
        + "&text="
        + quote("\n".join(text_parts), safe="")
    )


def build_resume_match_url(job: dict, bot_username: str = "") -> str:
    """Open the resume check in the user's private bot chat."""
    username = str(bot_username or "").strip().lstrip("@")
    job_id = str(job.get("hash") or job.get("content_hash") or "")[:40]
    if not username or not job_id:
        return ""
    return f"https://t.me/{username}?start=resume_{job_id}"


def build_bot_home_url(bot_username: str = "") -> str:
    """Open the private personalized-search home screen."""
    username = str(bot_username or "").strip().lstrip("@")
    if not username:
        return ""
    return f"https://t.me/{username}?start=web_home"


class JobMessageFormatter(_BaseJobMessageFormatter):
    """Mobile-first public channel cards with URL-only actions."""

    @staticmethod
    def _repair_mojibake(text: str) -> str:
        if not text or not any(mark in text for mark in ("Ã", "Â", "â€", "ðŸ")):
            return text
        try:
            repaired = text.encode("latin1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text
        before = sum(text.count(mark) for mark in ("Ã", "Â", "â€", "ðŸ"))
        after = sum(repaired.count(mark) for mark in ("Ã", "Â", "â€", "ðŸ"))
        return repaired if after < before else text

    def _clean_summary(self, job: dict, max_chars: int) -> str:
        raw = str(job.get("description") or "").strip()
        if not raw:
            return ""
        raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
        raw = re.sub(r"(?s)<[^>]+>", " ", raw)
        text = html_unescape(raw)
        text = self._repair_mojibake(text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 35:
            return ""
        if len(text) <= max_chars:
            return text
        clipped = text[: max_chars + 1]
        if " " in clipped:
            clipped = clipped.rsplit(" ", 1)[0]
        return clipped.rstrip(" ,.;:") + "…"

    def _track_name(self, job: dict) -> str:
        track = str(job.get("primary_track") or "").strip()
        if track:
            return TRACK_NAMES_RU.get(track, track.replace("_", " ").title())
        category = str(job.get("category") or "other")
        return CATEGORY_NAMES_RU.get(category, category)

    def _format_badge(self, job: dict) -> str:
        level = str(job.get("level") or "IT").strip()
        emoji = LEVEL_EMOJIS.get(level, "🔵")
        track = self._track_name(job)
        badge = f"{level.upper()} · {track.upper()}"
        return f"{emoji} *{self._escape_markdown_v2(badge)}*"

    def _format_channel_location(self, job: dict) -> str:
        location = str(
            job.get("location_restriction") or job.get("location") or ""
        ).strip()
        geo = str(job.get("geo_restriction") or "").strip()
        scope = str(job.get("remote_scope") or "").strip()
        remote_evidence = str(job.get("remote_evidence") or "").strip()

        if geo:
            value = f"Remote · {geo}"
        elif scope == "worldwide":
            value = "Remote · Worldwide"
        elif location:
            if remote_evidence and "remote" not in location.lower():
                value = f"Remote · {location}"
            else:
                value = location
        elif remote_evidence:
            value = "Remote"
        else:
            value = "Локация не указана"
        return f"🌍 {self._escape_markdown_v2(value)}"

    def _format_channel_salary(self, job: dict) -> str:
        salary = str(job.get("salary") or "").strip()
        if not salary or salary.lower() in {"не указана", "not specified", "none"}:
            return "💰 _Зарплата не указана_"
        return f"💰 *{self._escape_markdown_v2(salary)}*"

    def _format_freshness(self, job: dict) -> str:
        raw = job.get("source_published_at")
        if not raw:
            return ""
        try:
            if isinstance(raw, datetime):
                published = raw
            else:
                published = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - published.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return ""
        seconds = age.total_seconds()
        if seconds < -300:
            return ""
        days = max(0, int(seconds // 86400))
        if days == 0:
            label = "Опубликована сегодня"
        elif days == 1:
            label = "Опубликована вчера"
        elif days <= 7:
            label = f"Опубликована {days} дн. назад"
        else:
            label = published.strftime("Опубликована %d.%m.%Y")
        return f"🕒 _{self._escape_markdown_v2(label)}_"

    def _quality_hint(self, job: dict) -> str:
        hints = []
        if str(job.get("remote_scope") or "") == "scope_unconfirmed":
            hints.append("географию найма проверь перед откликом")
        if str(job.get("level_source") or "") == "inferred":
            hints.append("уровень определён по описанию")
        if not hints:
            return ""
        return f"ℹ️ _{self._escape_markdown_v2(' · '.join(hints))}_"

    def _format_channel_card(self, job: dict, *, summary_chars: int, include_source: bool) -> str:
        title = str(job.get("title") or "IT-вакансия").strip()
        company = str(job.get("company") or "Компания не указана").strip()
        summary = self._clean_summary(job, summary_chars)

        lines = [
            self._format_badge(job),
            "",
            f"*{self._escape_markdown_v2(title)}*",
            f"🏢 {self._escape_markdown_v2(company)}",
            "",
            self._format_channel_location(job),
            self._format_channel_salary(job),
        ]

        freshness = self._format_freshness(job)
        if freshness:
            lines.append(freshness)
        hint = self._quality_hint(job)
        if hint:
            lines.append(hint)
        if summary:
            lines.extend(["", f"📌 {self._escape_markdown_v2(summary)}"])
        if include_source:
            source = str(job.get("source") or "").strip()
            if source:
                lines.extend(["", f"_Источник: {self._escape_markdown_v2(source)}_"])
        return "\n".join(lines)

    def _format_compact(self, job: dict) -> str:
        return self._format_channel_card(job, summary_chars=240, include_source=False)

    def _format_full(self, job: dict) -> str:
        return self._format_channel_card(job, summary_chars=850, include_source=True)

    def create_inline_keyboard(
        self,
        job: dict,
        view_mode: str = "compact",
        bot_username: str = "",
    ) -> dict:
        """Public channel actions are URL-only; no shared-message callbacks."""
        job_id = str(job.get("hash") or job.get("content_hash") or "")[:40]
        url = str(job.get("url") or "").strip()
        rows = []

        primary = []
        if url.startswith(("http://", "https://")):
            primary.append({"text": "🚀 Откликнуться", "url": url})
        resume_url = build_resume_match_url(job, bot_username=bot_username) if job_id else ""
        if resume_url:
            primary.append({"text": "📄 Проверить резюме", "url": resume_url})
        if primary:
            rows.append(primary)

        secondary = []
        bot_home = build_bot_home_url(bot_username)
        if bot_home:
            secondary.append({"text": "🎯 Мой поиск вакансий", "url": bot_home})
        share_url = build_telegram_share_url(job, bot_username=bot_username)
        if share_url:
            secondary.append({"text": "📤 Поделиться", "url": share_url})
        if secondary:
            rows.append(secondary)

        return {"inline_keyboard": rows}


_formatter = None


def get_formatter() -> JobMessageFormatter:
    global _formatter
    if _formatter is None:
        _formatter = JobMessageFormatter()
    return _formatter


def format_job_message(job: dict, view_mode: str = "compact"):
    return get_formatter().format_job(job, view_mode)


def format_job_list_message(jobs, limit: int = 10) -> str:
    return get_formatter().format_job_list(jobs, limit)
