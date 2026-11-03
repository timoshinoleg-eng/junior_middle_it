"""Pinned navigator post for the channel: value summary + URL action buttons.

The builder is pure (no Telegram I/O) so the contract is unit-testable; the
publisher lives in ``api/telegram_navigator_admin.py``. All buttons are URL
deep links into the bot chat — Telegram forbids callback buttons in channels.
"""
from __future__ import annotations

from typing import Dict, Tuple
from urllib.parse import quote

from message_formatter import build_bot_home_url, build_magnet_url

NAVIGATOR_TITLE = "🧭 Junior & Middle IT: канал + AI-копилот"

DEFAULT_CHANNEL = "@junior_middle_it"

_BUTTONS_DISABLED_NOTE = (
    "Кнопки действий появятся ниже, как только в переменных окружения "
    "задан юзернейм бота (BOT_USERNAME)."
)


def navigator_text(bot_username: str = "", channel: str = DEFAULT_CHANNEL) -> str:
    """HTML text of the pinned navigator post."""
    username = str(bot_username or "").strip().lstrip("@")
    mention = f"@{username}" if username else "наш бот"
    channel_name = str(channel or DEFAULT_CHANNEL).strip()
    lines = [
        f"<b>{NAVIGATOR_TITLE}</b>",
        "",
        f"В канале — свежие проверенные вакансии Junior/Middle: разработка, QA, "
        f"DevOps, Data, Product, Design.",
        f"{mention} — персональный поиск и помощь с прохождением скрининга:",
        "",
        "✨ <b>Разбор вакансии</b> — совпадение твоего стека, реальный грейд "
        "требований и контакт HR (кнопка под каждым постом).",
        "📄 <b>Проверка резюме</b> под конкретную вакансию — PDF/DOCX или текстом.",
        "⭐ <b>Избранное</b> — сохраняй вакансии из канала в один клик.",
        "🔔 <b>Персональные подборки</b> — по твоим навыкам и зарплате, без спама.",
        "",
        "🎁 <b>Бонус новым подписчикам:</b> гайд по ATS-скринингу и "
        "<b>+3 AI-кредита</b> — кнопкой ниже.",
    ]
    if not username:
        lines.extend(["", _BUTTONS_DISABLED_NOTE])
    lines.extend(["", f"📢 Канал: {channel_name}"])
    return "\n".join(lines)


def navigator_reply_markup(bot_username: str = "", channel: str = DEFAULT_CHANNEL) -> Dict:
    """URL-only inline keyboard; empty rows are omitted."""
    username = str(bot_username or "").strip().lstrip("@")
    if not username:
        return {"inline_keyboard": []}
    channel_link = f"https://t.me/{str(channel or DEFAULT_CHANNEL).lstrip('@')}"
    share_text = quote("Junior & Middle IT-вакансии и AI-копилот для поиска работы", safe="")
    rows = [
        [
            {"text": "🎯 Мой поиск вакансий", "url": build_bot_home_url(username)},
            {"text": "🎁 Гайд + 3 кредита", "url": build_magnet_url(username)},
        ],
        [
            {"text": "💎 Купить AI-кредиты", "url": f"https://t.me/{username}?start=stars"},
            {"text": "📤 Поделиться каналом", "url": f"https://t.me/share/url?url={quote(channel_link, safe='')}&text={share_text}"},
        ],
    ]
    return {"inline_keyboard": rows}


def build_navigator_post(bot_username: str = "", channel: str = DEFAULT_CHANNEL) -> Tuple[str, Dict]:
    return navigator_text(bot_username, channel), navigator_reply_markup(bot_username, channel)
