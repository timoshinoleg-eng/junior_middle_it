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


class JobMessageFormatter(_BaseJobMessageFormatter):
    """Base formatter with working share and Resume Match actions."""

    def create_inline_keyboard(
        self,
        job: dict,
        view_mode: str = "compact",
        bot_username: str = "",
    ) -> dict:
        keyboard = super().create_inline_keyboard(
            job,
            view_mode=view_mode,
            bot_username=bot_username,
        )
        rows = keyboard.setdefault("inline_keyboard", [])
        share_url = build_telegram_share_url(job, bot_username=bot_username)

        if share_url:
            for row in rows:
                for button in row:
                    if button.get("text") == "📤 Поделиться":
                        button.clear()
                        button.update({"text": "📤 Поделиться", "url": share_url})
                        break

        job_id = str(job.get("hash") or job.get("content_hash") or "")[:40]
        if job_id:
            resume_button = {
                "text": "📄 Проверить резюме",
                "callback_data": f"resume_match:{job_id}",
            }
            # Keep the utility CTA prominent but on its own row for mobile taps.
            insert_at = 1 if rows else 0
            if not any(
                button.get("callback_data") == f"resume_match:{job_id}"
                for row in rows
                for button in row
            ):
                rows.insert(insert_at, [resume_button])
        return keyboard


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
