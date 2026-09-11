"""Growth-aware facade for the job message formatter.

The proven MarkdownV2 implementation remains in ``message_formatter_base``.
This thin layer owns acquisition and utility CTAs so growth features can evolve
without risking the formatting code that already has production coverage.
"""
from urllib.parse import quote

from message_formatter_base import *  # noqa: F401,F403
from message_formatter_base import JobMessageFormatter as _BaseJobMessageFormatter


def build_telegram_share_url(job: dict, bot_username: str = "") -> str:
    """Build an official Telegram share URL with an attributable bot CTA."""
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
    text_parts = [headline, f"Уровень: {level}"]
    if username:
        text_parts.append(
            f"Больше Junior/Middle remote IT: https://t.me/{username}?start=share_{job_id}"
        )

    return (
        "https://t.me/share/url?url="
        + quote(share_target, safe="")
        + "&text="
        + quote("\n".join(text_parts), safe="")
    )


def build_resume_match_url(job: dict, bot_username: str = "") -> str:
    """Open Resume Match in the user's private bot chat."""
    username = str(bot_username or "").strip().lstrip("@")
    job_id = str(job.get("hash") or job.get("content_hash") or "")[:40]
    if not username or not job_id:
        return ""
    return f"https://t.me/{username}?start=resume_{job_id}"


class JobMessageFormatter(_BaseJobMessageFormatter):
    """Base formatter with a compact action hierarchy for mobile users."""

    def create_inline_keyboard(
        self,
        job: dict,
        view_mode: str = "compact",
        bot_username: str = "",
    ) -> dict:
        job_id = str(job.get("hash") or job.get("content_hash") or "")[:40]
        url = str(job.get("url") or "").strip()
        category = str(job.get("category") or "other")
        rows = []

        primary = []
        if url:
            primary.append({"text": "🚀 Откликнуться", "url": url})
        if job_id:
            resume_url = build_resume_match_url(job, bot_username=bot_username)
            resume_button = {"text": "📄 Resume Match"}
            if resume_url:
                resume_button["url"] = resume_url
            else:
                resume_button["callback_data"] = f"resume_match:{job_id}"
            primary.append(resume_button)
        if primary:
            rows.append(primary)

        utility = []
        if job_id:
            utility.append({"text": "💾 Сохранить", "callback_data": f"save:{job_id}"})
        share_url = build_telegram_share_url(job, bot_username=bot_username)
        if share_url:
            utility.append({"text": "📤 Поделиться", "url": share_url})
        if utility:
            rows.append(utility)

        details = []
        if job_id:
            details.append({
                "text": "⬇️ Подробнее" if view_mode == "compact" else "⬆️ Свернуть",
                "callback_data": f"{'expand' if view_mode == 'compact' else 'compact'}:{job_id}",
            })
        details.append({
            "text": f"🚫 {CATEGORY_NAMES_RU.get(category, category)}",
            "callback_data": f"hide_cat:{category}",
        })
        rows.append(details)

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
